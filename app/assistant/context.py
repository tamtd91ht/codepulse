"""What Stetho sees: a text snapshot of CodePulse, ONLY what the asker may already see.

Every section reuses the dashboard's own endpoint functions (or the report builder) with the
asker's Principal, so the visibility rules are the dashboard's (docs/specs/09 "Phân quyền",
"Phân quyền theo dự án"): a manager gets team aggregates, never per-person rows of a team they
do not lead; a dự án without a grant does not exist. What the endpoint refuses (403/404) is
named as refused, never worked around.

No source code reaches the model: finding titles and messages, file paths and line numbers only
(no `code_excerpt`, no `suggestion`).

Order: the asker, the screen they are on (with its filters), what the question names (a team or
a dự án by name), then the dự án overview and the teams they see.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Literal
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers.disputes import list_disputes
from app.api.routers.metrics import team_hotspots, user_category_heatmap, user_metrics
from app.api.routers.overview import project_detail, projects_overview
from app.api.routers.reviews import get_review, list_findings, list_reviews
from app.api.security import Principal
from app.config import Settings
from app.models import BusinessProject, MetricsTeamPeriod, Team, User
from app.reporting.report import build_team_report, render
from app.scoring.aggregate import period_start_of

MAX_CHARS = 60_000
MAX_TEAMS = 40
MAX_ROWS = 40
MAX_NAMED = 2
MAX_TEXT = 300
SEVERITIES = ("critical", "high", "medium", "low", "info")

Period = Literal["week", "month"]


@dataclass
class Screen:
    """The dashboard page the asker has open: its path, query string (filters) and title."""

    path: str
    search: str = ""
    title: str = ""

    def params(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.search.lstrip("?")).items() if v}


def _num(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:.{digits}f}"


def _signed(value: Any) -> str:
    if value is None:
        return "—"
    return f"+{_num(value)}" if float(value) > 0 else _num(value)


def _short(text: Any, limit: int = MAX_TEXT) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _when(value: Any, tz: ZoneInfo) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return str(value)
    if not isinstance(value, datetime):
        return "—"
    return str(value.astimezone(tz).strftime("%d/%m/%Y %H:%M"))


def _int(raw: str | None) -> int | None:
    return int(raw) if raw and raw.isdigit() else None


def _period(raw: str | None) -> Period:
    return "month" if raw == "month" else "week"


def _guarded(title: str, build: Callable[[], list[str]]) -> list[str]:
    """A section, or why the asker may not see it (the endpoint's own refusal)."""
    try:
        return build()
    except HTTPException as exc:
        why = (
            "không có quyền xem"
            if exc.status_code == 403
            else "không tìm thấy hoặc không có quyền xem"
        )
        return [f"## {title}", f"({why} — không đưa dữ liệu vào.)"]


# -- the asker ---------------------------------------------------------------------------------


def asker_section(session: Session, principal: Principal, now: datetime, tz: ZoneInfo) -> list[str]:
    user = session.get(User, principal.user_id)
    name = (user.full_name or user.primary_email) if user else f"user {principal.user_id}"
    if principal.is_admin:
        role = "admin — xem được mọi dự án, team và số liệu cá nhân"
    else:
        teams = {t.id: t.name for t in session.scalars(select(Team))}
        managed = ", ".join(sorted(teams.get(t, str(t)) for t in principal.managed_teams))
        role = f"manager của {managed or '(không team nào)'} — chỉ số liệu tổng hợp của team"
        if principal.lead_teams:
            led = ", ".join(sorted(teams.get(t, str(t)) for t in principal.lead_teams))
            role += f"; lead của {led} — thấy số liệu cá nhân của thành viên các team này"
        granted = principal.visible_projects() or set()
        names = sorted(
            bp.name
            for bp in session.scalars(
                select(BusinessProject).where(BusinessProject.id.in_(granted))
            )
        )
        role += f"; dự án được xem: {', '.join(names) if names else 'không có'}"
    return [
        f"# Ảnh chụp CodePulse — {now.astimezone(tz).strftime('%d/%m/%Y %H:%M')} ({tz.key})",
        f"Người hỏi: {name} · {role}.",
    ]


# -- dự án ---------------------------------------------------------------------------------------


def overview_section(
    session: Session, principal: Principal, settings: Settings, period: Period
) -> list[str]:
    data = projects_overview(principal, session, settings, period=period)
    t = data["totals"]
    label = "Tuần" if period == "week" else "Tháng"
    lines = [
        f"## Tổng quan dự án — {label} bắt đầu {data['period_start']} (so với kỳ trước)",
        f"Tổng: {t['reviews']} review ({_signed(t['delta']['reviews'])}), điểm TB "
        f"{_num(t['avg_score'])} ({_signed(t['delta']['avg_score'])}), tỉ lệ đạt "
        f"{_num(t['pass_rate'])}%, finding mới critical {t['critical']} / high {t['high']}, "
        f"đang mở critical {t['open_critical']} / high {t['open_high']} · "
        f"{t['projects']} dự án, {t['repos']} repo theo dõi.",
    ]
    for p in data["projects"][:MAX_ROWS]:
        trend = ", ".join(_num(v) for v in p["trend"])
        lines.append(
            f"- {p['name']}: {p['reviews']} review, điểm TB {_num(p['avg_score'])} "
            f"({_signed(p['delta']['avg_score'])}), đạt {_num(p['pass_rate'])}%, mới "
            f"critical {p['critical']}/high {p['high']}, đang mở critical {p['open_critical']}/"
            f"high {p['open_high']}, verdict approve {p['approve']}/needs_human "
            f"{p['needs_human']}/fix_required {p['fix_required']}/block {p['block']} · điểm TB "
            f"{len(p['trend'])} kỳ gần nhất: {trend}"
        )
    return lines


def project_section(
    session: Session, principal: Principal, settings: Settings, bp_id: int, period: Period
) -> list[str]:
    data = project_detail(bp_id, principal, session, settings, period=period)
    tz = ZoneInfo(settings.report_tz)
    t = data["totals"]
    lines = [
        f"## Dự án {data['project']['name']} — kỳ {period} bắt đầu {data['period_start']}",
        f"Mô tả: {_short(data['project']['description'])}"
        if data["project"]["description"]
        else "",
        f"{t['reviews']} review ({_signed(t['delta']['reviews'])}), điểm TB {_num(t['avg_score'])} "
        f"({_signed(t['delta']['avg_score'])}), đạt {_num(t['pass_rate'])}%, mới critical "
        f"{t['critical']}/high {t['high']}, đang mở critical {t['open_critical']}/high "
        f"{t['open_high']}, {t['repos']} repo.",
        "Theo repo (kỳ này):",
    ]
    for r in data["repos"][:MAX_ROWS]:
        lines.append(
            f"- {r['path_with_namespace']}{'' if r['tracked'] else ' (ngừng theo dõi)'}: "
            f"{r['reviews']} review, điểm TB {_num(r['avg_score'])}, mới critical {r['critical']}"
            f"/high {r['high']}, đang mở critical {r['open_critical']}/high {r['open_high']}, "
            f"review gần nhất {_when(r['last_review_at'], tz)}"
        )
    lines.append(
        "Diễn biến theo kỳ (cũ → mới): "
        + "; ".join(
            f"{s['period_start']}: {s['reviews']} review, điểm {_num(s['avg_score'])}, "
            f"critical {s['critical']}/high {s['high']}"
            for s in data["series"]
        )
    )
    if data["categories"]:
        lines.append(
            "Loại finding kỳ này: "
            + ", ".join(f"{c['category']} {c['count']}" for c in data["categories"])
        )
    if data["recent"]:
        lines.append("Review gần nhất người hỏi xem được:")
        lines += [_review_line(r, tz) for r in data["recent"]]
    return [line for line in lines if line]


def _review_line(r: dict[str, Any], tz: ZoneInfo) -> str:
    author = r.get("author")
    who = f" · tác giả {author.full_name}" if author is not None and author.full_name else ""
    return (
        f"- #{r['id']} {r.get('project') or '?'} {r.get('target') or ''}{who} · {r['status']}"
        f" · verdict {r.get('verdict') or '—'} · điểm {_num(r.get('score'))} · {r.get('mode')}"
        f" · xong {_when(r.get('finished_at'), tz)}"
        + (f" · tóm tắt: {_short(r['summary'], 200)}" if r.get("summary") else "")
        + (f" · lỗi: {_short(r['error'], 160)}" if r.get("error") else "")
    )


# -- teams ---------------------------------------------------------------------------------------


def _visible_team_ids(principal: Principal, session: Session) -> list[int] | None:
    if principal.is_admin:
        return None
    return sorted(principal.own_teams | principal.lead_teams | principal.managed_teams)


def teams_section(session: Session, principal: Principal, tz: ZoneInfo) -> list[str]:
    ids = _visible_team_ids(principal, session)
    query = select(Team).order_by(Team.name)
    if ids is not None:
        query = query.where(Team.id.in_(ids))
    teams = list(session.scalars(query.limit(MAX_TEAMS + 1)))
    week = period_start_of(datetime.now(tz).date(), "week") - timedelta(weeks=1)
    rows = {
        m.team_id: m
        for m in session.scalars(
            select(MetricsTeamPeriod).where(
                MetricsTeamPeriod.team_id.in_([t.id for t in teams]),
                MetricsTeamPeriod.period_type == "week",
                MetricsTeamPeriod.period_start == week,
            )
        )
    }
    lines = [f"## Team người hỏi xem được — số liệu tuần {week} (tuần trước, đã tổng hợp)"]
    for team in teams[:MAX_TEAMS]:
        m = rows.get(team.id)
        if m is None:
            lines.append(f"- {team.name}: chưa có số liệu tuần này")
            continue
        lines.append(
            f"- {team.name}: quality index {_num(m.quality_index)}, điểm TB {_num(m.avg_score)}, "
            f"{m.reviews_count or 0} review, critical {m.critical or 0}/high {m.high or 0}, "
            f"defect density {_num(m.defect_density, 2)}"
        )
    if not teams:
        lines.append("Không có team nào.")
    if len(teams) > MAX_TEAMS:
        lines.append("… và các team khác (đã lược bớt).")
    return lines


def team_section(
    session: Session, principal: Principal, settings: Settings, team_id: int, period: Period
) -> list[str]:
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(404, "team not found")
    if not (principal.is_admin or team_id in principal.lead_teams | principal.managed_teams):
        # A member's own team: the anonymous aggregates of the Team page, not the report.
        if not principal.can_see_team(team_id):
            raise HTTPException(403, "not allowed to see this team")
        return [f"## Team {team.name}", "(Chỉ là thành viên: không có báo cáo của team.)"]
    tz = ZoneInfo(settings.report_tz)
    start = period_start_of(datetime.now(UTC).astimezone(tz).date(), period)
    audience: Literal["lead", "manager"] = (
        "lead" if principal.is_admin or team_id in principal.lead_teams else "manager"
    )
    report = build_team_report(
        session, team_id, period, start, tz, audience, projects=principal.visible_projects()
    )
    hot = team_hotspots(team_id, principal, session, settings, period=period, start=start)
    version = (
        "bản lead: có số liệu từng người" if audience == "lead" else "bản manager: chỉ tổng hợp"
    )
    lines = [
        f"## Team {team.name} — báo cáo {period} bắt đầu {start} ({version})",
        render(report, "md").strip(),
    ]
    if hot["categories"] or hot["files"]:
        lines.append(
            "Điểm nóng kỳ này — loại finding: "
            + (", ".join(f"{c['category']} {c['count']}" for c in hot["categories"]) or "—")
            + " · file: "
            + (", ".join(f"{f['file_path']} {f['count']}" for f in hot["files"]) or "—")
        )
    return lines


# -- a person ----------------------------------------------------------------------------------


def member_section(
    session: Session, principal: Principal, settings: Settings, user_id: int
) -> list[str]:
    today = datetime.now(ZoneInfo(settings.report_tz)).date()
    data = user_metrics(
        user_id,
        principal,
        session,
        period="week",
        date_from=today - timedelta(weeks=12),
        date_to=today,
    )
    user = session.get(User, user_id)
    lines = [
        f"## Số liệu cá nhân của {user.full_name if user else user_id} — 12 tuần gần nhất",
        f"Quality index của team (so sánh): {_num(data['team_quality_index'])}; thay đổi so với "
        f"kỳ trước: " + ", ".join(f"{k} {_signed(v)}" for k, v in (data["delta"] or {}).items()),
    ]
    for m in data["series"]:
        lines.append(
            f"- tuần {m.period_start}: quality index {_num(m.quality_index)}, điểm TB "
            f"{_num(m.avg_score)}, {m.reviews_count or 0} review, critical {m.critical or 0}/high "
            f"{m.high or 0}/medium {m.medium or 0}, defect density {_num(m.defect_density, 2)}"
        )
    if not data["series"]:
        lines.append("Chưa có số liệu tuần nào trong 12 tuần qua.")
    heat = user_category_heatmap(user_id, principal, session, settings, weeks=12)
    totals: dict[str, int] = {}
    for row in heat:
        totals[row["category"]] = totals.get(row["category"], 0) + int(row["count"])
    if totals:
        lines.append(
            "Finding theo loại (12 tuần): "
            + ", ".join(f"{c} {n}" for c, n in sorted(totals.items(), key=lambda kv: -kv[1]))
        )
    return lines


# -- reviews, findings, disputes -----------------------------------------------------------------


def reviews_section(
    session: Session, principal: Principal, settings: Settings, params: dict[str, str]
) -> list[str]:
    verdict = params.get("verdict")
    mode = params.get("mode")
    page = list_reviews(
        principal,
        session,
        settings,
        user_id=_int(params.get("user")),
        project_id=_int(params.get("project") or params.get("repo")),
        business_project_id=_int(params.get("bp")),
        verdict=verdict if verdict in ("approve", "needs_human", "fix_required", "block") else None,  # type: ignore[arg-type]
        mode=mode if mode in ("fast", "deep") else None,  # type: ignore[arg-type]
        limit=MAX_ROWS,
    )
    tz = ZoneInfo(settings.report_tz)
    lines = [f"{len(page.items)} review thay đổi gần nhất khớp bộ lọc của màn hình:"]
    lines += [_review_line(r, tz) for r in page.items] or ["Không có."]
    return lines


def review_section(
    session: Session, principal: Principal, settings: Settings, review_id: int
) -> list[str]:
    r = get_review(review_id, principal, session)
    tz = ZoneInfo(settings.report_tz)
    author = session.get(User, r["author_user_id"]) if r["author_user_id"] else None
    lines = [
        f"## Review #{r['id']} đang xem — {r.get('project') or '?'} "
        f"{'MR' if r['target_type'] == 'mr' else 'commit'}"
        + (f" · tác giả {author.full_name}" if author and author.full_name else ""),
        f"Trạng thái {r['status']} · verdict {r.get('verdict') or '—'} · điểm {_num(r['score'])} · "
        f"risk {r.get('risk_score') if r.get('risk_score') is not None else '—'} · chế độ "
        f"{r['mode']} · vòng {r['round']} · {r['loc_reviewed'] or 0} dòng · model "
        f"{r['model'] or '—'} · xong {_when(r['finished_at'], tz)}",
    ]
    if r.get("summary"):
        lines.append(f"Tóm tắt của review: {_short(r['summary'], 1500)}")
    if r.get("error"):
        lines.append(f"Lỗi: {_short(r['error'], 500)}")
    findings = sorted(
        r["findings"], key=lambda f: SEVERITIES.index(f.severity) if f.severity in SEVERITIES else 9
    )
    lines.append(f"Finding ({len(findings)}):")
    for f in findings[:MAX_ROWS]:
        where = (
            f"{f.file_path}:{f.line_start}" if f.file_path and f.line_start else f.file_path or "?"
        )
        lines.append(
            f"- [{f.severity}/{f.category}] {_short(f.title, 160)} · {where} · trạng thái "
            f"{f.status or 'open'} · kiểm chứng {f.verified or '—'} · độ tin cậy "
            f"{_num(f.confidence, 2)}" + (f" · {_short(f.message)}" if f.message else "")
        )
    if len(r.get("history") or []) > 1:
        lines.append(
            "Các vòng review của MR/commit này: "
            + "; ".join(
                f"#{h['id']} {h.get('status')} điểm {_num(h.get('score'))}" for h in r["history"]
            )
        )
    return lines


def findings_section(
    session: Session, principal: Principal, settings: Settings, params: dict[str, str]
) -> list[str]:
    severity = [s for s in (params.get("severity") or "").split(",") if s in SEVERITIES]
    status = params.get("status") or "open"
    if status not in (
        "open",
        "fixed",
        "disputed",
        "accepted_risk",
        "false_positive",
        "any",
        "counted",
    ):
        status = "open"
    found_from = _datetime(params.get("from"))
    found_to = _datetime(params.get("to"))
    if status == "counted" and (found_from is None or found_to is None):
        status = "any"
    page = list_findings(
        principal,
        session,
        settings,
        severity=severity or ["critical", "high"],
        status=status,  # type: ignore[arg-type]
        project_id=_int(params.get("repo_id")),
        business_project_id=_int(params.get("bp")),
        found_from=found_from,
        found_to=found_to,
        user_id=_int(params.get("user")),
        team_id=_int(params.get("team")),
        limit=MAX_ROWS,
    )
    lines = [
        f"Finding khớp bộ lọc (mức {', '.join(severity or ['critical', 'high'])}, trạng thái "
        f"{status}; {len(page.items)} mới nhất):"
    ]
    for f in page.items:
        where = (
            f"{f['file_path']}:{f['line_start']}"
            if f["file_path"] and f["line_start"]
            else f["file_path"] or "?"
        )
        lines.append(
            f"- [{f['severity']}/{f['category']}] {_short(f['title'], 160)} · {f['project']} "
            f"{f['target']} · {where} · tác giả {f['author'] or '?'} · {f['age_days']} ngày · "
            f"review #{f['review_id']}"
        )
    return lines if page.items else [*lines, "Không có."]


def _datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def disputes_section(session: Session, principal: Principal) -> list[str]:
    items = list_disputes(principal, session, pending=True)
    lines = [f"Khiếu nại đang chờ duyệt người hỏi xem được ({len(items)}):"]
    for d in items[:MAX_ROWS]:
        lines.append(
            f"- #{d['id']} finding [{d['finding_severity'] or '?'}] "
            f"{_short(d['finding_title'], 160)} (review #{d['review_id']}) · người khiếu nại "
            f"{d['raised_by_name'] or '?'} · lý do: {_short(d.get('reason'), 200)}"
        )
    return lines if items else [*lines, "Không có."]


# -- the screen ----------------------------------------------------------------------------------

_DETAIL = re.compile(r"^/(projects|teams|members|reviews)/(\d+)$")
NO_DATA = "Màn hình này không có số liệu riêng — trả lời dựa trên các phần bên dưới."


def screen_section(
    session: Session, principal: Principal, settings: Settings, screen: Screen
) -> list[str]:
    path = screen.path.rstrip("/") or "/"
    params = screen.params()
    period = _period(params.get("period"))
    head = [f"## Màn hình đang xem: {screen.title or path} ({path}{screen.search})"]
    body: list[str]
    if m := _DETAIL.match(path):
        kind, ident = m.group(1), int(m.group(2))
        if kind == "projects":
            body = _guarded(
                "Dự án", lambda: project_section(session, principal, settings, ident, period)
            )
        elif kind == "teams":
            body = _guarded(
                "Team", lambda: team_section(session, principal, settings, ident, period)
            )
        elif kind == "members":
            body = _guarded(
                "Số liệu cá nhân", lambda: member_section(session, principal, settings, ident)
            )
        else:
            body = _guarded("Review", lambda: review_section(session, principal, settings, ident))
    elif path == "/reviews":
        body = _guarded(
            "Kết quả review", lambda: reviews_section(session, principal, settings, params)
        )
    elif path == "/critical":
        body = _guarded(
            "Critical Board", lambda: findings_section(session, principal, settings, params)
        )
    elif path == "/disputes":
        body = disputes_section(session, principal)
    elif path in ("/", "/overview", "/teams"):
        body = ['Màn hình tổng hợp: số liệu ở các phần "Tổng quan dự án" và "Team" bên dưới.']
    else:
        body = [NO_DATA]
    return head + body


def named_sections(
    session: Session,
    principal: Principal,
    settings: Settings,
    question: str,
    screen: Screen | None,
) -> list[str]:
    """Teams and dự án the question names (longest names first), beyond the one on screen."""
    asked = question.casefold()
    on_screen = _DETAIL.match((screen.path.rstrip("/") if screen else "") or "/")
    shown = (on_screen.group(1), int(on_screen.group(2))) if on_screen else None
    out: list[str] = []
    team_ids = _visible_team_ids(principal, session)
    team_query = select(Team)
    if team_ids is not None:
        team_query = team_query.where(Team.id.in_(team_ids))
    teams = [
        t for t in session.scalars(team_query) if len(t.name) >= 3 and t.name.casefold() in asked
    ]
    for team in sorted(teams, key=lambda t: -len(t.name))[:MAX_NAMED]:
        if shown != ("teams", team.id):
            out += [
                "",
                *_guarded(
                    f"Team {team.name}",
                    partial(team_section, session, principal, settings, team.id, "week"),
                ),
            ]
    bp_query = select(BusinessProject)
    if (visible := principal.visible_projects()) is not None:
        bp_query = bp_query.where(BusinessProject.id.in_(visible))
    bps = [
        bp for bp in session.scalars(bp_query) if len(bp.name) >= 3 and bp.name.casefold() in asked
    ]
    for bp in sorted(bps, key=lambda b: -len(b.name))[:MAX_NAMED]:
        if shown != ("projects", bp.id):
            out += [
                "",
                *_guarded(
                    f"Dự án {bp.name}",
                    partial(project_section, session, principal, settings, bp.id, "week"),
                ),
            ]
    return out


def build_context(
    session: Session,
    principal: Principal,
    settings: Settings,
    screen: Screen | None = None,
    question: str = "",
    now: datetime | None = None,
) -> str:
    tz = ZoneInfo(settings.report_tz)
    period = _period(screen.params().get("period")) if screen else "week"
    lines = asker_section(session, principal, now or datetime.now(UTC), tz)
    if screen is not None:
        lines += ["", *screen_section(session, principal, settings, screen)]
    lines += named_sections(session, principal, settings, question, screen)
    lines += ["", *overview_section(session, principal, settings, period)]
    lines += ["", *teams_section(session, principal, tz)]
    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        return text[:MAX_CHARS] + "\n… (ngữ cảnh đã bị cắt bớt cho vừa giới hạn)"
    return text
