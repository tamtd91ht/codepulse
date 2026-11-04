"""Báo cáo nhóm (docs/specs/23 §8): several people side by side over one period: who writes
the best code, who needs help, who makes which kind of mistake, how each scores under a
criteria set; and the same as an Excel workbook."""

import io
from collections import defaultdict
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.personal import person_scope, sees_team
from app.api.routers.evaluation import _team_members
from app.api.routers.member_report import (
    ScopeFilters,
    _criteria_sets,
    _detail_ids,
    _facts,
    _labels,
    _num,
    require_export,
    sheet_title,
    xlsx_row,
)
from app.api.schemas import UserOut
from app.api.security import Principal, require
from app.config import Settings, get_settings
from app.models import (
    CriteriaSet,
    CriteriaSetVersion,
    ProjectGrant,
    ReviewCriteriaScore,
    Team,
    User,
)
from app.scoring.criteria import CATEGORY_LABELS
from app.scoring.evaluation import Bucket, ReviewFacts
from app.scoring.member_report import LEVELS, assess, review_row, summarize, weekly, weeks_of
from app.scoring.verdict import VERDICT_LABEL

router = APIRouter(tags=["member-report"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# docs/specs/23 §9: whose rows one sees is the scope of `report.member`.
Viewer = Annotated[Principal, require("report.member")]

MAX_PEOPLE = 100
TOO_MANY = f"so sánh tối đa {MAX_PEOPLE} người; chọn team hoặc chọn ít người hơn"
TOP_CATEGORIES = 8


class TeamFilters(ScopeFilters):
    """The shared filters plus who is compared: a team, a list of people, or both (the people
    of the team that are in the list); neither = everyone the viewer covers."""

    def __init__(
        self,
        session: Session,
        principal: Principal,
        settings: Settings,
        date_from: date | None,
        date_to: date | None,
        business_project_id: int | None,
        project_id: int | None,
        team_id: int | None,
        user_ids: list[int],
    ) -> None:
        super().__init__(
            session, principal, settings, date_from, date_to, business_project_id, project_id
        )
        scope = person_scope(session, principal, "report.member")
        self.team: Team | None = None
        people: set[int] | None = scope
        if team_id is not None:
            if not sees_team(principal, team_id):
                raise HTTPException(403, "not allowed to see this team")
            self.team = session.get(Team, team_id)
            if self.team is None:
                raise HTTPException(404, "team not found")
            members = _team_members(session, team_id, self.start, self.end)
            people = members if people is None else people & members
        if user_ids:
            asked = set(user_ids)
            if scope is not None and asked - scope:
                raise HTTPException(
                    403, "individual metrics are visible to the person, their lead and managers"
                )
            people = asked if people is None else people & asked
        self.explicit = team_id is not None or bool(user_ids)
        self.people = people  # None: everyone (admin / FLAG scope, nobody named)
        if user_ids and len(set(user_ids)) > MAX_PEOPLE:  # cheap refusal before any loading
            raise HTTPException(422, TOO_MANY)

    def audit_args(self) -> dict[str, Any]:
        return {
            **super().audit_args(),
            **({"team_id": self.team.id} if self.team else {}),
            **({"user_ids": sorted(self.people)} if self.people is not None else {}),
        }


def _criteria_by_person(
    session: Session,
    principal: Principal,
    facts: list[ReviewFacts],
    set_id: int,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[str, Any]]:
    """Under one set: its components (current version first), each person's LOC-weighted final
    and component scores, and the group's. Only reviews the viewer may open count (like
    /criteria/summary)."""
    allowed = _detail_ids(session, principal, [f.id for f in facts])
    loc = {f.id: max(f.loc, 1) for f in facts if f.id in allowed}
    author = {f.id: f.user_id for f in facts}
    current = session.execute(
        select(CriteriaSetVersion)
        .join(CriteriaSet, CriteriaSet.current_version_id == CriteriaSetVersion.id)
        .where(CriteriaSet.id == set_id)
    ).scalar_one_or_none()
    names: dict[str, tuple[str, int]] = {
        str(c["key"]): (str(c["name"]), int(c["weight"]))
        for c in (current.components if current else [])
    }
    order = list(names)
    acc: dict[int | None, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0])
    )
    counts: dict[int | None, int] = defaultdict(int)
    if loc:
        rows = session.execute(
            select(
                ReviewCriteriaScore.review_id,
                ReviewCriteriaScore.score,
                ReviewCriteriaScore.breakdown,
            ).where(
                ReviewCriteriaScore.set_id == set_id,
                ReviewCriteriaScore.review_id.in_(list(loc)),
            )
        ).all()
        for rid, value, breakdown in rows:
            w = float(loc[rid])
            for who in (author[rid], None):  # the person, then the group (None)
                counts[who] += 1
                final = acc[who]["__final__"]
                final[0] += float(value) * w
                final[1] += w
                for part in (breakdown or {}).get("components", []):
                    key = str(part.get("key"))
                    names.setdefault(
                        key, (str(part.get("name") or key), int(part.get("weight") or 0))
                    )
                    if part.get("score") is None:
                        continue
                    slot = acc[who][key]
                    slot[0] += float(part["score"]) * w
                    slot[1] += w
    keys = order + sorted(k for k in names if k not in order)

    def avg(slot: list[float] | None) -> float | None:
        return round(slot[0] / slot[1], 1) if slot and slot[1] else None

    def scores(who: int | None) -> dict[str, Any]:
        mine = acc.get(who, {})
        return {
            "reviews": counts.get(who, 0),
            "score": avg(mine.get("__final__")),
            "components": {k: avg(mine.get(k)) for k in keys},
        }

    people = {who: scores(who) for who in acc if who is not None}
    components = [{"key": k, "name": names[k][0], "weight": names[k][1]} for k in keys]
    return components, people, scores(None)


def build_team_report(
    session: Session, principal: Principal, f: TeamFilters, set_id: int | None
) -> tuple[dict[str, Any], list[ReviewFacts]]:
    now = _facts(session, principal, f, f.people, f.start, f.end)
    before = _facts(session, principal, f, f.people, f.prev_start, f.prev_end)
    group, group_prev = summarize(now), summarize(before)
    by_person: dict[int, list[ReviewFacts]] = defaultdict(list)
    prev_person: dict[int, list[ReviewFacts]] = defaultdict(list)
    for r in now:
        by_person[r.user_id].append(r)
    for r in before:
        prev_person[r.user_id].append(r)
    # Everyone named (a team, a list) gets a row, reviewed or not; otherwise those with reviews,
    # plus, with a dự án chosen, the people granted it (its members, even without a review).
    ids = set(by_person) | (f.people or set() if f.explicit else set())
    if f.bp_id is not None and not f.explicit:
        granted = set(
            session.scalars(
                select(ProjectGrant.user_id).where(ProjectGrant.business_project_id == f.bp_id)
            )
        )
        ids |= granted if f.people is None else granted & f.people
    users = {
        u.id: u
        for u in session.scalars(select(User).where(User.id.in_(ids), User.active.is_not(False)))
    }
    # The rows actually shown: active people named, or active people with a review.
    if len(users) > MAX_PEOPLE:
        raise HTTPException(422, TOO_MANY)
    weeks = weeks_of(f.start, f.end)
    group_trend, group_reviews = weekly(now, weeks, f.tz)

    # The kinds of mistakes the group makes most, and each person's share of their own.
    totals = Bucket()
    for r in now:
        totals.add(r)
    top = [c for c, _ in totals.categories.most_common(TOP_CATEGORIES)]
    group_cats = sum(totals.categories.values()) or 1

    criteria_sets = _criteria_sets(session, f, now)
    chosen = next((s for s in criteria_sets if s["set_id"] == set_id), None) or (
        criteria_sets[0] if criteria_sets else None
    )
    if set_id is not None and (chosen is None or chosen["set_id"] != set_id):
        raise HTTPException(404, "không có bộ tiêu chí này trong các review của nhóm")
    components: list[dict[str, Any]] = []
    crit_people: dict[int, dict[str, Any]] = {}
    crit_group: dict[str, Any] | None = None
    if chosen is not None:
        components, crit_people, crit_group = _criteria_by_person(
            session, principal, now, chosen["set_id"]
        )

    rows: list[dict[str, Any]] = []
    for uid, user in users.items():
        me = summarize(by_person.get(uid, []))
        prev = summarize(prev_person.get(uid, []))
        mine = Bucket()
        for r in by_person.get(uid, []):
            mine.add(r)
        cat_total = sum(mine.categories.values()) or 1
        rows.append(
            {
                "user": UserOut.model_validate(user).model_dump(),
                **me,
                "previous_avg_score": prev["avg_score"],
                "assessment": assess(me, prev, group).as_dict(),
                "trend": weekly(by_person.get(uid, []), weeks, f.tz)[0],
                "categories": {
                    c: round(100.0 * mine.categories.get(c, 0) / cat_total, 1) for c in top
                },
                "top_category": (
                    CATEGORY_LABELS.get(
                        mine.categories.most_common(1)[0][0], mine.categories.most_common(1)[0][0]
                    )
                    if mine.categories
                    else None
                ),
                "criteria": crit_people.get(uid),
            }
        )
    # Weakest first: who needs attention is what the reader looks for.
    order = {"support": 0, "fair": 1, "good": 2, "none": 3}
    rows.sort(
        key=lambda r: (
            order[r["assessment"]["level"]],
            r["avg_score"] if r["avg_score"] is not None else 101,
            str(r["user"]["full_name"] or ""),
        )
    )
    levels = {k: sum(1 for r in rows if r["assessment"]["level"] == k) for k in LEVELS}
    report = {
        "from": f.start,
        "to": f.end,
        "filters": {
            **f.as_dict(),
            "team_id": f.team.id if f.team else None,
            "team_name": f.team.name if f.team else None,
        },
        "previous": {"from": f.prev_start, "to": f.prev_end, "summary": group_prev},
        "summary": group,
        "levels": levels,
        "weeks": weeks,
        "trend": group_trend,
        "trend_reviews": group_reviews,
        "members": rows,
        "categories": [
            {
                "category": c,
                "label": CATEGORY_LABELS.get(c, c),
                "count": totals.categories[c],
                "share": round(100.0 * totals.categories[c] / group_cats, 1),
            }
            for c in top
        ],
        "criteria_sets": criteria_sets,
        "criteria": (
            {
                "set_id": chosen["set_id"],
                "name": chosen["name"],
                "version": chosen["version"],
                "components": components,
                "group": crit_group,
            }
            if chosen is not None
            else None
        ),
    }
    return report, now


def _filters(
    session: Session,
    principal: Principal,
    settings: Settings,
    date_from: date | None,
    date_to: date | None,
    business_project_id: int | None,
    project_id: int | None,
    team_id: int | None,
    user_ids: str | None,
) -> TeamFilters:
    try:
        ids = [int(x) for x in (user_ids or "").split(",") if x.strip()]
    except ValueError:
        raise HTTPException(422, "user_ids: danh sách id cách nhau dấu phẩy") from None
    return TeamFilters(
        session,
        principal,
        settings,
        date_from,
        date_to,
        business_project_id,
        project_id,
        team_id,
        ids,
    )


@router.get("/members/team-report")
def team_report(
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
    team_id: int | None = None,
    user_ids: str | None = None,
    set_id: int | None = None,
) -> dict[str, Any]:
    """Báo cáo nhóm: the group's numbers against the period before, one row per person (weakest
    first), the mistake kinds per person, the scores per criteria component per person."""
    f = _filters(
        session,
        principal,
        settings,
        date_from,
        date_to,
        business_project_id,
        project_id,
        team_id,
        user_ids,
    )
    report, _ = build_team_report(session, principal, f, set_id)
    audit(session, principal.user_id, "member_report.team_read", "members", **f.audit_args())
    session.commit()
    return report


@router.get("/members/team-report.xlsx")
def team_report_xlsx(
    principal: Viewer,
    session: SessionDep,
    settings: SettingsDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    business_project_id: int | None = None,
    project_id: int | None = None,
    team_id: int | None = None,
    user_ids: str | None = None,
    set_id: int | None = None,
) -> Response:
    require_export(principal)
    f = _filters(
        session,
        principal,
        settings,
        date_from,
        date_to,
        business_project_id,
        project_id,
        team_id,
        user_ids,
    )
    report, facts = build_team_report(session, principal, f, set_id)
    body = team_workbook(session, principal, settings, f, report, facts)
    audit(session, principal.user_id, "member_report.team_export", "members", **f.audit_args())
    session.commit()
    who = "".join(
        ch if (ch.isascii() and ch.isalnum()) or ch in "-_" else "-"
        for ch in (f.team.name if f.team else "nhom")
    )
    filename = f"codepulse-bao-cao-nhom-{who}-{f.start:%Y%m%d}-{f.end:%Y%m%d}.xlsx"
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def team_workbook(
    session: Session,
    principal: Principal,
    settings: Settings,
    f: TeamFilters,
    report: dict[str, Any],
    facts: list[ReviewFacts],
) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.worksheet import Worksheet

    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="EEF1F5")
    wb = Workbook()

    def sheet(
        title: str, header: list[str], rows: list[list[Any]], first: bool = False
    ) -> Worksheet:
        ws: Worksheet = wb.active if first else wb.create_sheet()  # type: ignore[assignment]
        ws.title = sheet_title(title)
        xlsx_row(ws, header)
        for cell in ws[1]:
            cell.font = bold
            cell.fill = head_fill
        for row in rows:
            xlsx_row(ws, row)
        for i, h in enumerate(header, start=1):
            width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r)])
            ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 50)
        ws.freeze_panes = "A2"
        return ws

    def name(row: dict[str, Any]) -> str:
        u = row["user"]
        return str(u.get("full_name") or u.get("primary_email") or f"#{u['id']}")

    s, prev = report["summary"], report["previous"]["summary"]
    where = report["filters"]
    scope = (
        " / ".join(
            x
            for x in (where["team_name"], where["business_project_name"], where["project_path"])
            if x
        )
        or "Mọi người trong phạm vi"
    )
    metrics = [
        ("Người có review", "members"),
        ("Review", "reviews"),
        ("Dòng code (LOC)", "loc"),
        ("Điểm TB (theo LOC)", "avg_score"),
        ("Tỉ lệ APPROVE (%)", "pass_rate"),
        ("Critical", "critical"),
        ("% review có critical", "critical_rate"),
        ("High", "high"),
        ("% review có high", "high_rate"),
        ("Lỗi / 100 LOC", "findings_per_100_loc"),
    ]
    ws = sheet(
        "Tổng quan nhóm",
        ["Chỉ số", "Kỳ này", "Kỳ trước"],
        [[label, _num(s[k]), _num(prev[k])] for label, k in metrics]
        + [[], ["Mức đánh giá", "Số người"]]
        + [[LEVELS[k], n] for k, n in report["levels"].items()],
        first=True,
    )
    ws.insert_rows(1, 4)
    ws["A1"] = f"Báo cáo nhóm: {scope}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = (
        f"Khoảng: {f.start:%d/%m/%Y} - {f.end:%d/%m/%Y}"
        f" · Kỳ trước: {f.prev_start:%d/%m/%Y} - {f.prev_end:%d/%m/%Y}"
    )
    ws["A3"] = (
        "Mức: Tốt (điểm TB ≥ 80, không critical, ≥ 60% APPROVE)"
        " · Cần hỗ trợ (có critical hoặc điểm TB < 65)"
    )
    ws.freeze_panes = "A6"

    members = report["members"]
    sheet(
        "So sánh thành viên",
        [
            "Thành viên",
            "Mức",
            "Review",
            "MR",
            "Commit",
            "LOC",
            "Điểm TB",
            "Điểm TB kỳ trước",
            "Tỉ lệ APPROVE (%)",
            "Critical",
            "% review có critical",
            "High",
            "% review có high",
            "Lỗi / 100 LOC",
            "Loại lỗi nhiều nhất",
            "Lý do",
        ],
        [
            [
                name(m),
                m["assessment"]["label"]
                + (" (ít dữ liệu)" if m["assessment"]["thin"] and m["reviews"] else ""),
                m["reviews"],
                m["mrs"],
                m["commits"],
                m["loc"],
                _num(m["avg_score"]),
                _num(m["previous_avg_score"]),
                _num(m["pass_rate"]),
                m["critical"],
                _num(m["critical_rate"]),
                m["high"],
                _num(m["high_rate"]),
                _num(m["findings_per_100_loc"]),
                m["top_category"] or "",
                " ".join(m["assessment"]["reasons"]),
            ]
            for m in members
        ],
    )
    cats = report["categories"]
    sheet(
        "Loại lỗi",
        ["Thành viên", *[f"{c['label']} (%)" for c in cats]],
        [[name(m), *[m["categories"].get(c["category"]) for c in cats]] for m in members]
        + [["Cả nhóm", *[c["share"] for c in cats]]],
    )
    crit = report["criteria"]
    if crit is not None:
        comps = crit["components"]
        header = [
            "Thành viên",
            "Review",
            "Điểm cuối",
            *[f"{c['name']} ({c['weight']}%)" for c in comps],
        ]
        rows = [
            [
                name(m),
                m["criteria"]["reviews"],
                _num(m["criteria"]["score"]),
                *[_num(m["criteria"]["components"].get(c["key"])) for c in comps],
            ]
            for m in members
            if m["criteria"]
        ]
        g = crit["group"]
        rows.append(
            [
                "Cả nhóm",
                g["reviews"],
                _num(g["score"]),
                *[_num(g["components"].get(c["key"])) for c in comps],
            ]
        )
        sheet(f"Tiêu chí - {crit['name']}", header, rows)
    else:
        sheet(
            "Theo tiêu chí",
            ["Ghi chú"],
            [["Không có review nào được chấm theo bộ tiêu chí trong khoảng này."]],
        )

    names = {m["user"]["id"]: name(m) for m in members}
    open_ = _detail_ids(session, principal, [r.id for r in facts])
    rows_ = sorted(
        (review_row(r) | {"user_id": r.user_id} for r in facts if r.id in open_),
        key=lambda r: r["counted_at"],
    )
    labels = _labels(session, [r["id"] for r in rows_])
    base = settings.dashboard_url.rstrip("/")
    sheet(
        "Review",
        [
            "Ngày",
            "Thành viên",
            "Repo",
            "Loại",
            "MR / commit",
            "Tiêu đề",
            "Điểm",
            "Kết luận",
            "LOC",
            "Critical",
            "High",
            "Link",
        ],
        [
            [
                r["counted_at"].astimezone(f.tz).strftime("%Y-%m-%d %H:%M"),
                names.get(r["user_id"], f"#{r['user_id']}"),
                r["repo"] or "",
                "MR" if r["target_type"] == "mr" else "Commit",
                labels.get(r["id"], {}).get("ref", ""),
                labels.get(r["id"], {}).get("title", ""),
                _num(r["score"]),
                VERDICT_LABEL.get(r["verdict"] or "", r["verdict"] or ""),
                r["loc"],
                r["critical"],
                r["high"],
                f"{base}/reviews/{r['id']}",
            ]
            for r in rows_
        ],
    )
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


__all__ = ["router"]
