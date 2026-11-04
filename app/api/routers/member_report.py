"""Báo cáo thành viên (docs/specs/23): one person's code quality over a period, filtered by dự án
and repo, against the period before and against everyone the viewer sees; and the same as an
Excel workbook."""

import io
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.access import work_scope
from app.api.audit import audit
from app.api.deps import get_session
from app.api.personal import covers_person
from app.api.routers.criteria import summary as criteria_summary
from app.api.routers.evaluation import load_review_facts
from app.api.schemas import UserOut
from app.api.security import Principal, require
from app.config import Settings, get_settings
from app.models import (
    BusinessProject,
    Commit,
    CriteriaSet,
    CriteriaSetVersion,
    MergeRequest,
    Project,
    ProjectCriteriaSet,
    Review,
    ReviewCriteriaScore,
    User,
)
from app.scoring.evaluation import ReviewFacts
from app.scoring.member_report import (
    assess,
    by_repo,
    categories,
    review_row,
    summarize,
    weekly,
    weeks_of,
)
from app.scoring.verdict import VERDICT_LABEL

router = APIRouter(tags=["member-report"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# docs/specs/23 §9: the report has rights of its own (app/permissions/catalog.py).
Viewer = Annotated[Principal, require("report.member")]


def require_export(principal: Principal) -> None:
    """The Excel files need `report.export` on top of seeing the report."""
    if not principal.can("report.export"):
        raise HTTPException(403, "xuất Excel báo cáo thành viên cần quyền report.export")


DEFAULT_DAYS = 30
MAX_DAYS = 366
MIN_YEAR, MAX_YEAR = 2000, 2100
LIST_LIMIT = 8  # reviews to look at, in the JSON (the workbook lists every review)


class ScopeFilters:
    """The filters every report shares, checked once: the period in whole REPORT_TZ days, the
    dự án and repo the viewer may see."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        settings: Settings,
        date_from: date | None,
        date_to: date | None,
        business_project_id: int | None,
        project_id: int | None,
    ) -> None:
        self.tz = ZoneInfo(settings.report_tz)
        for day in (date_from, date_to):
            if day is not None and not MIN_YEAR <= day.year <= MAX_YEAR:
                raise HTTPException(422, f"dates must fall between {MIN_YEAR} and {MAX_YEAR}")
        self.end = date_to or datetime.now(self.tz).date()
        self.start = date_from or self.end - timedelta(days=DEFAULT_DAYS - 1)
        if self.start > self.end:
            raise HTTPException(422, "from must not be after to")
        if (self.end - self.start).days >= MAX_DAYS:
            raise HTTPException(422, f"the range may cover at most {MAX_DAYS} days")
        self.business: BusinessProject | None = None
        self.repo: Project | None = None
        if project_id is not None:
            self.repo = session.get(Project, project_id)
            # can_view_project(None) holds for admins only: repos outside every dự án stay hidden.
            if self.repo is None or not principal.can_view_project(self.repo.business_project_id):
                raise HTTPException(404, "repo not found")
        if business_project_id is not None:
            self.business = session.get(BusinessProject, business_project_id)
            if self.business is None or not principal.can_view_project(business_project_id):
                raise HTTPException(404, "dự án not found")
        if self.repo is not None:
            if business_project_id is None:
                business_project_id = self.repo.business_project_id
                if business_project_id is not None:
                    self.business = session.get(BusinessProject, business_project_id)
            elif self.repo.business_project_id != business_project_id:
                raise HTTPException(422, "repo không thuộc dự án đã chọn")
        self.bp_id = business_project_id
        self.project_id = project_id
        # The period of the same length right before, for "so với kỳ trước".
        days = (self.end - self.start).days + 1
        self.prev_end = self.start - timedelta(days=1)
        self.prev_start = self.prev_end - timedelta(days=days - 1)

    def bounds(self, start: date, end: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(start, time.min, self.tz),
            datetime.combine(end + timedelta(days=1), time.min, self.tz),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_project_id": self.bp_id,
            "business_project_name": self.business.name if self.business else None,
            "project_id": self.project_id,
            "project_path": self.repo.path_with_namespace if self.repo else None,
        }

    def audit_args(self) -> dict[str, Any]:
        return {
            "from": self.start.isoformat(),
            "to": self.end.isoformat(),
            **({"business_project_id": self.bp_id} if self.bp_id is not None else {}),
            **({"project_id": self.project_id} if self.project_id is not None else {}),
        }


class Filters(ScopeFilters):
    """One person's report: the shared filters, and that person, if the viewer covers them."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        settings: Settings,
        user_id: int,
        date_from: date | None,
        date_to: date | None,
        business_project_id: int | None,
        project_id: int | None,
    ) -> None:
        if not covers_person(session, principal, "report.member", user_id):
            raise HTTPException(
                403, "individual metrics are visible to the person, their lead and dự án managers"
            )
        user = session.get(User, user_id)
        if user is None:
            raise HTTPException(404, "person not found")
        self.user = user
        super().__init__(
            session, principal, settings, date_from, date_to, business_project_id, project_id
        )


def _facts(
    session: Session,
    principal: Principal,
    f: ScopeFilters,
    users: set[int] | None,
    start: date,
    end: date,
) -> list[ReviewFacts]:
    lo, hi = f.bounds(start, end)
    return load_review_facts(
        session,
        users,
        lo,
        hi,
        viewer=principal,
        business_project_id=f.bp_id,
        project_id=f.project_id,
    )


def _detail_ids(session: Session, principal: Principal, ids: list[int]) -> set[int]:
    """The reviews among `ids` whose content the viewer may open (`review.view`): the report's
    numbers come from `report.member`, but titles, links and per-review scores are review
    content."""
    if not ids:
        return set()
    query = select(Review.id).where(Review.id.in_(ids))
    if (allowed := work_scope(principal, "review.view")) is not None:
        query = query.where(allowed)
    return set(session.scalars(query))


def _labels(session: Session, ids: list[int]) -> dict[int, dict[str, Any]]:
    """MR !iid and title, or the commit's short sha and first line, per review id."""
    if not ids:
        return {}
    rows = session.execute(
        select(
            Review.id,
            MergeRequest.iid,
            MergeRequest.title,
            Review.commit_sha,
            Commit.message,
        )
        .outerjoin(MergeRequest, MergeRequest.id == Review.mr_id)
        .outerjoin(Commit, Commit.sha == Review.commit_sha)
        .where(Review.id.in_(ids))
    ).all()
    out: dict[int, dict[str, Any]] = {}
    for rid, iid, title, sha, message in rows:
        if iid is not None:
            out[rid] = {"ref": f"!{iid}", "title": (title or "")[:120]}
        else:
            first = (message or "").strip().splitlines()[:1]
            out[rid] = {"ref": (sha or "")[:8], "title": (first[0] if first else "")[:120]}
    return out


def _criteria_sets(
    session: Session, f: ScopeFilters, facts: list[ReviewFacts]
) -> list[dict[str, Any]]:
    """The criteria sets the reviews of the period have a score under (the dự án's
    primary first when a dự án is chosen)."""
    ids = [r.id for r in facts]
    if not ids:
        return []
    rows = session.execute(
        select(ReviewCriteriaScore.set_id, func.count())
        .where(ReviewCriteriaScore.review_id.in_(ids))
        .group_by(ReviewCriteriaScore.set_id)
    ).all()
    counts = {set_id: int(n) for set_id, n in rows}
    if not counts:
        return []
    # Primary in the dự án chosen, else in any dự án of the repos the person worked in.
    bp_ids = (
        {f.bp_id}
        if f.bp_id is not None
        else set(
            session.scalars(
                select(Project.business_project_id).where(
                    Project.id.in_({r.project_id for r in facts if r.project_id is not None}),
                    Project.business_project_id.is_not(None),
                )
            )
        )
    )
    primary = set(
        session.scalars(
            select(ProjectCriteriaSet.set_id).where(
                ProjectCriteriaSet.business_project_id.in_(bp_ids),
                ProjectCriteriaSet.is_primary.is_(True),
            )
        )
    )
    sets = session.execute(
        select(CriteriaSet, CriteriaSetVersion)
        .outerjoin(CriteriaSetVersion, CriteriaSetVersion.id == CriteriaSet.current_version_id)
        .where(CriteriaSet.id.in_(counts))
    ).all()
    out: list[dict[str, Any]] = [
        {
            "set_id": s.id,
            "name": s.name,
            "version": v.version if v else None,
            "is_primary": s.id in primary,
            "reviews": counts[s.id],
        }
        for s, v in sets
    ]
    out.sort(key=lambda s: (not s["is_primary"], -int(s["reviews"]), str(s["name"])))
    return out


def build_report(
    session: Session, principal: Principal, settings: Settings, f: Filters
) -> dict[str, Any]:
    uid = f.user.id
    mine = _facts(session, principal, f, {uid}, f.start, f.end)
    before = _facts(session, principal, f, {uid}, f.prev_start, f.prev_end)
    everyone = _facts(session, principal, f, None, f.start, f.end)
    me, previous, peers = summarize(mine), summarize(before), summarize(everyone)
    weeks = weeks_of(f.start, f.end)
    trend, trend_reviews = weekly(mine, weeks, f.tz)
    peer_trend, _ = weekly(everyone, weeks, f.tz)
    open_ = _detail_ids(session, principal, [r.id for r in mine])
    rows = [review_row(r) for r in mine if r.id in open_]
    scored = [r for r in rows if r["score"] is not None]
    lowest = sorted(scored, key=lambda r: (r["score"], -r["loc"]))[:LIST_LIMIT]
    severe = sorted(
        (r for r in rows if r["critical"] or r["high"]),
        key=lambda r: (-r["critical"], -r["high"], r["score"] or 0),
    )[:LIST_LIMIT]
    labels = _labels(session, [r["id"] for r in (*lowest, *severe)])
    for r in (*lowest, *severe):
        r.update(labels.get(r["id"], {"ref": "", "title": ""}))
    return {
        "user": UserOut.model_validate(f.user).model_dump(),
        "from": f.start,
        "to": f.end,
        "filters": f.as_dict(),
        "summary": me,
        "previous": {"from": f.prev_start, "to": f.prev_end, "summary": previous},
        "peers": peers,
        "assessment": assess(me, previous, peers).as_dict(),
        "weeks": weeks,
        "trend": trend,
        "trend_reviews": trend_reviews,
        "peer_trend": peer_trend,
        "by_repo": by_repo(mine),
        "categories": categories(mine, everyone),
        "lowest_reviews": lowest,
        "severe_reviews": severe,
        "criteria_sets": _criteria_sets(session, f, mine),
        "_facts": mine,  # the workbook lists every review; dropped from the JSON
    }


@router.get("/members/{user_id}/report")
def member_report(
    user_id: int,
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
) -> dict[str, Any]:
    """Báo cáo thành viên, tab Tổng quan (the Theo tiêu chí tab reads /criteria/summary with the
    set ids listed in `criteria_sets`)."""
    f = Filters(
        session, principal, settings, user_id, date_from, date_to, business_project_id, project_id
    )
    report = build_report(session, principal, settings, f)
    report.pop("_facts")
    if user_id != principal.user_id:
        audit(session, principal.user_id, "member_report.read", f"user:{user_id}", **f.audit_args())
        session.commit()
    return report


# -- the workbook --------------------------------------------------------------------------------


def xlsx_row(ws: Any, values: list[Any]) -> None:
    """Append a row of user-supplied values safely: control characters openpyxl refuses are
    dropped, and nothing ever becomes a formula: "=WEBSERVICE(...)" in a title stays text."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    ws.append([ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v for v in values])
    for cell in ws[ws.max_row]:
        if cell.data_type == "f":
            cell.data_type = "s"


def sheet_title(name: str) -> str:
    """A sheet title Excel accepts: no \\ / ? * : [ ], at most 31 characters."""
    import re

    return re.sub(r"[\\/*?:\[\]]", "-", name)[:31].strip("'") or "Sheet"


def _num(value: Any) -> Any:
    return float(value) if value is not None else None


def workbook(session: Session, principal: Principal, settings: Settings, f: Filters) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.worksheet import Worksheet

    report = build_report(session, principal, settings, f)
    facts: list[ReviewFacts] = report.pop("_facts")
    name = f.user.full_name or f.user.primary_email or f"#{f.user.id}"
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="EEF1F5")
    wb = Workbook()

    def sheet(title: str, header: list[str], rows: list[list[Any]], first: bool = False) -> Any:
        ws: Worksheet = wb.active if first else wb.create_sheet()  # type: ignore[assignment]
        ws.title = sheet_title(title)
        xlsx_row(ws, header)
        for cell in ws[1]:
            cell.font = bold
            cell.fill = head_fill
        for row in rows:
            xlsx_row(ws, row)
        for i, _ in enumerate(header, start=1):
            width = max(
                [len(str(header[i - 1]))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r)]
            )
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)
        ws.freeze_panes = "A2"
        return ws

    s, prev, peers = report["summary"], report["previous"]["summary"], report["peers"]
    a = report["assessment"]
    where = report["filters"]
    scope = (
        " / ".join(x for x in (where["business_project_name"], where["project_path"]) if x)
        or "Mọi dự án"
    )
    metrics = [
        ("Review", "reviews"),
        ("MR", "mrs"),
        ("Commit", "commits"),
        ("Dòng code (LOC)", "loc"),
        ("Điểm TB (theo LOC)", "avg_score"),
        ("Điểm thấp nhất", "min_score"),
        ("Tỉ lệ APPROVE (%)", "pass_rate"),
        ("Critical", "critical"),
        ("% review có critical", "critical_rate"),
        ("High", "high"),
        ("% review có high", "high_rate"),
        ("Medium", "medium"),
        ("Low", "low"),
        ("Lỗi / 100 LOC", "findings_per_100_loc"),
        ("Khiếu nại được chấp nhận", "disputed"),
        ("Bị kiểm chứng bác bỏ", "rejected"),
        ("Số repo", "repos"),
    ]
    ws = sheet(
        "Tổng quan",
        ["Chỉ số", "Kỳ này", "Kỳ trước", "Mặt bằng (mọi người trong phạm vi)"],
        [[label, _num(s[k]), _num(prev[k]), _num(peers[k])] for label, k in metrics],
        first=True,
    )
    ws.insert_rows(1, 7)
    ws["A1"] = f"Báo cáo thành viên: {name}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (
        f"Khoảng: {f.start:%d/%m/%Y} - {f.end:%d/%m/%Y}"
        f" · Kỳ trước: {f.prev_start:%d/%m/%Y} - {f.prev_end:%d/%m/%Y}"
    )
    ws["A3"] = f"Phạm vi: {scope} · Mặt bằng: {peers['members']} người"
    ws["A4"] = f"Đánh giá: {a['label']}" + (" (ít dữ liệu)" if a["thin"] else "")
    ws["A4"].font = bold
    ws["A5"] = " ".join(a["reasons"])
    ws["A5"].alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells("A5:D6")
    ws.row_dimensions[5].height = 45
    ws.freeze_panes = "A9"

    sheet(
        "Theo tuần",
        ["Tuần bắt đầu", "Số review", "Điểm TB", "Điểm TB mặt bằng"],
        [
            [w.isoformat(), n, t, p]
            for w, n, t, p in zip(
                report["weeks"],
                report["trend_reviews"],
                report["trend"],
                report["peer_trend"],
                strict=True,
            )
        ],
    )
    sheet(
        "Theo repo",
        [
            "Repo",
            "Review",
            "MR",
            "Commit",
            "LOC",
            "Điểm TB",
            "Tỉ lệ APPROVE (%)",
            "Critical",
            "% review có critical",
            "High",
            "% review có high",
            "Lỗi / 100 LOC",
        ],
        [
            [
                r["path"] or "(không rõ repo)",
                r["reviews"],
                r["mrs"],
                r["commits"],
                r["loc"],
                _num(r["avg_score"]),
                _num(r["pass_rate"]),
                r["critical"],
                _num(r["critical_rate"]),
                r["high"],
                _num(r["high_rate"]),
                _num(r["findings_per_100_loc"]),
            ]
            for r in report["by_repo"]
        ],
    )
    sheet(
        "Loại lỗi",
        ["Loại lỗi", "Số finding", "Tỉ trọng (%)", "Tỉ trọng mặt bằng (%)"],
        [[c["label"], c["count"], c["share"], c["peer_share"]] for c in report["categories"]],
    )

    # Theo tiêu chí: one block per set (the person, then everyone in scope).
    crit_rows: list[list[Any]] = []
    for item in report["criteria_sets"]:
        common = {
            "principal": principal,
            "session": session,
            "settings": settings,
            "business_project_id": f.bp_id,
            "project_id": f.project_id,
            "date_from": f.start,
            "date_to": f.end,
            "set_id": item["set_id"],
        }
        mine = criteria_summary(user_id=f.user.id, **common)
        all_ = criteria_summary(user_id=None, **common)
        peer_parts = {c["key"]: c for c in all_["components"]}
        crit_rows.append(
            [
                f"{item['name']} v{item['version']}"
                + (" (bộ chính)" if item["is_primary"] else ""),
                "Điểm cuối",
                None,
                _num(mine["score"]),
                _num(all_["score"]),
                mine["reviews"],
            ]
        )
        for c in mine["components"]:
            crit_rows.append(
                [
                    "",
                    c["name"],
                    c["weight"],
                    _num(c["score"]),
                    _num(peer_parts.get(c["key"], {}).get("score")),
                    c["findings"],
                ]
            )
    sheet(
        "Theo tiêu chí",
        ["Bộ tiêu chí", "Thành phần", "Trọng số (%)", "Điểm", "Điểm mặt bằng", "Review / finding"],
        crit_rows or [["Không có review nào được chấm theo bộ tiêu chí trong khoảng này."]],
    )

    open_ = _detail_ids(session, principal, [r.id for r in facts])
    rows = sorted((review_row(r) for r in facts if r.id in open_), key=lambda r: r["counted_at"])
    labels = _labels(session, [r["id"] for r in rows])
    criteria: dict[int, dict[int, float]] = {}
    set_ids = [c["set_id"] for c in report["criteria_sets"]]
    if rows and set_ids:
        for rid, set_id, value in session.execute(
            select(
                ReviewCriteriaScore.review_id, ReviewCriteriaScore.set_id, ReviewCriteriaScore.score
            ).where(
                ReviewCriteriaScore.review_id.in_([r["id"] for r in rows]),
                ReviewCriteriaScore.set_id.in_(set_ids),
            )
        ):
            criteria.setdefault(rid, {})[set_id] = float(value)
    base = settings.dashboard_url.rstrip("/")
    sheet(
        "Review",
        [
            "Ngày",
            "Repo",
            "Loại",
            "MR / commit",
            "Tiêu đề",
            "Điểm",
            "Kết luận",
            "LOC",
            "Critical",
            "High",
            "Medium",
            "Low",
            *[f"Tiêu chí: {c['name']}" for c in report["criteria_sets"]],
            "Link",
        ],
        [
            [
                r["counted_at"].astimezone(f.tz).strftime("%Y-%m-%d %H:%M"),
                r["repo"] or "",
                "MR" if r["target_type"] == "mr" else "Commit",
                labels.get(r["id"], {}).get("ref", ""),
                labels.get(r["id"], {}).get("title", ""),
                _num(r["score"]),
                VERDICT_LABEL.get(r["verdict"] or "", r["verdict"] or ""),
                r["loc"],
                r["critical"],
                r["high"],
                r["medium"],
                r["low"],
                *[criteria.get(r["id"], {}).get(sid) for sid in set_ids],
                f"{base}/reviews/{r['id']}",
            ]
            for r in rows
        ],
    )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@router.get("/members/{user_id}/report.xlsx")
def member_report_xlsx(
    user_id: int,
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
) -> Response:
    require_export(principal)
    f = Filters(
        session, principal, settings, user_id, date_from, date_to, business_project_id, project_id
    )
    body = workbook(session, principal, settings, f)
    audit(session, principal.user_id, "member_report.export", f"user:{user_id}", **f.audit_args())
    session.commit()
    who = (f.user.primary_email or str(f.user.id)).split("@")[0]
    safe = "".join(ch if (ch.isascii() and ch.isalnum()) or ch in "-_." else "-" for ch in who)
    filename = f"codepulse-bao-cao-{safe}-{f.start:%Y%m%d}-{f.end:%Y%m%d}.xlsx"
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
