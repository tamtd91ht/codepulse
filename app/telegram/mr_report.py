"""The analysis of a merge request reviewed at a Telegram request, as one HTML file behind a
signed link (docs/specs/15 §Kết quả MR, user 2026-11-02).

Only MR reviews asked for in Telegram get a link: the result message carries it. The link is
`<dashboard>/api/v1/review-reports/<token>`, token = "<review id>.<expiry>.<signature>",
HMAC-SHA256 with a key derived from SESSION_SECRET (never usable as a session token), valid
TELEGRAM_REPORT_LINK_HOURS (default 24). Anyone holding the link may download the file until
then (the user's choice: no login on the phone); after that it is refused.

The file is self-contained (inline CSS, no script, no external request) and holds what the
dashboard shows of the review: verdict, score, findings by severity with file, lines, message,
suggestion and the stored diff excerpt (at most 40 lines each, docs/specs/AGENTS), plus what
each severity means.
"""

import base64
import hashlib
import hmac
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Account, Connection, MergeRequest, Project, Review, ReviewFinding, User
from app.scoring.verdict import NOT_COUNTED, UNDECIDED, VERDICT_LABEL, count_severities

KEY_CONTEXT = b"codepulse/telegram-mr-report/v1"
# ASCII only: review id, expiry (unix seconds), base64url signature.
TOKEN = re.compile(r"^(\d{1,12})\.(\d{1,12})\.([A-Za-z0-9_-]{22})$", re.ASCII)
SIG_CHARS = 22  # 16 bytes of the HMAC, base64url: short enough for a chat message


@dataclass(frozen=True)
class Level:
    key: str
    label: str
    icon: str
    meaning: str
    action: str


# Order matters: the highest level first. Meanings follow the review rubric
# (app/review/rubric.py "Severity"), in the words a developer reads on a phone.
LEVELS: tuple[Level, ...] = (
    Level(
        "critical",
        "Critical",
        "🔴",
        "Nghiêm trọng: mất dữ liệu, lộ dữ liệu giữa khách hàng / người dùng, lỗ hổng bảo mật"
        " khai thác được, xử lý tiền sai, hoặc làm sập production.",
        "Phải sửa trước khi merge.",
    ),
    Level(
        "high",
        "High",
        "🟠",
        "Cao: lỗi trên luồng chính, thiếu kiểm tra quyền, race làm hỏng dữ liệu, hoặc không"
        " chịu nổi tải dự kiến (query không giới hạn trên bảng lớn dần, gọi chặn ở đường nóng,"
        " gọi ra ngoài không timeout).",
        "Nên sửa trước khi merge.",
    ),
    Level(
        "medium",
        "Medium",
        "🟡",
        "Trung bình: lỗi ở trường hợp biên, vấn đề hiệu năng rõ ràng, thiếu test cho logic"
        " quan trọng, thiếu retry / idempotency ở chỗ dễ bị gọi lại.",
        "Sửa sớm, có thể ở MR sau.",
    ),
    Level(
        "low",
        "Low",
        "🔵",
        "Thấp: khả năng bảo trì, code smell (hàm quá dài, magic number, tên khó hiểu).",
        "Sửa khi tiện.",
    ),
    Level("info", "Info", "⚪", "Gợi ý: góp ý nhỏ hoặc style.", "Không bắt buộc."),
)
LEVEL = {lv.key: lv for lv in LEVELS}
# The four the Telegram message always spells out (info is in the file only).
SHOWN = ("critical", "high", "medium", "low")
VERIFIED_LABEL = {
    "confirmed": "đã kiểm chứng",
    "downgraded": "đã hạ mức sau kiểm chứng",
    "rejected": "kiểm chứng bác bỏ, không tính",
    "unverified": "chưa kiểm chứng được, không tính điểm",
}


# -- signed links ---------------------------------------------------------------------------------


def _key(settings: Settings) -> bytes | None:
    secret = settings.session_secret.get_secret_value()
    if len(secret) < 32:
        return None  # same floor as sessions: no secret, no link
    return hmac.new(secret.encode(), KEY_CONTEXT, hashlib.sha256).digest()


def _sign(key: bytes, payload: str) -> str:
    mac = hmac.new(key, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")[:SIG_CHARS]


def link_token(settings: Settings, review_id: int, now: datetime) -> str | None:
    key = _key(settings)
    if key is None:
        return None
    expires = int((now + timedelta(hours=settings.telegram_report_link_hours)).timestamp())
    payload = f"{review_id}.{expires}"
    return f"{payload}.{_sign(key, payload)}"


def link_url(settings: Settings, review_id: int, now: datetime) -> str | None:
    """The download link of one review's analysis, or None when links cannot be signed."""
    token = link_token(settings, review_id, now)
    if token is None:
        return None
    return f"{settings.dashboard_url.rstrip('/')}/api/v1/review-reports/{token}"


def read_token(
    settings: Settings, token: str, now: datetime
) -> tuple[int | None, datetime | None, str]:
    """(review id, expiry, "") for a good token; (None, None, "invalid" | "expired") otherwise."""
    key = _key(settings)
    match = TOKEN.match(token)
    if key is None or match is None:
        return None, None, "invalid"
    review_id, expires, signature = match.groups()
    if not hmac.compare_digest(_sign(key, f"{review_id}.{expires}").encode(), signature.encode()):
        return None, None, "invalid"
    if int(expires) < now.timestamp():
        return None, None, "expired"
    return int(review_id), datetime.fromtimestamp(int(expires), UTC), ""


# -- counts ---------------------------------------------------------------------------------------


@dataclass
class Tally:
    counted: dict[str, int]  # settled findings per severity (what the verdict and score use)
    unverified: int  # critical/high/... the verification could not decide (not counted)

    @property
    def top(self) -> Level | None:
        return next((lv for lv in LEVELS if self.counted.get(lv.key)), None)


def tally(findings: list[ReviewFinding]) -> Tally:
    """The counts the verdict uses (app/scoring/verdict.py count_severities), and apart the
    findings the verification could not decide."""
    counted = count_severities([(f.severity, f.status, f.verified) for f in findings])
    unverified = sum(
        1 for f in findings if (f.status or "open") not in NOT_COUNTED and f.verified == UNDECIDED
    )
    return Tally(counted, unverified)


def findings_of(session: Session, review_id: int) -> list[ReviewFinding]:
    return list(
        session.scalars(
            select(ReviewFinding)
            .where(ReviewFinding.review_id == review_id)
            .order_by(ReviewFinding.file_path, ReviewFinding.line_start, ReviewFinding.id)
        )
    )


# -- the HTML file --------------------------------------------------------------------------------


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


STYLE = """
:root{--bg:#ffffff;--fg:#1f2328;--muted:#59636e;--line:#d1d9e0;--card:#f6f8fa;
--critical:#cf222e;--high:#bc4c00;--medium:#9a6700;--low:#0969da;--info:#59636e;--ok:#1a7f37}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--fg:#e6edf3;--muted:#9198a1;
--line:#3d444d;--card:#151b23;--critical:#ff7b72;--high:#f0883e;--medium:#d29922;
--low:#58a6ff;--info:#9198a1;--ok:#3fb950}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:960px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 12px}
h3{font-size:16px;margin:0 0 6px}a{color:var(--low)}
.muted{color:var(--muted)}.small{font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;
margin:0 0 12px}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}
.kpi{border:1px solid var(--line);border-radius:8px;padding:8px 12px;min-width:110px}
.kpi b{display:block;font-size:20px}
.badge{display:inline-block;border-radius:999px;padding:1px 10px;font-size:13px;font-weight:600;
border:1px solid currentColor}
.critical{color:var(--critical)}.high{color:var(--high)}.medium{color:var(--medium)}
.low{color:var(--low)}.info{color:var(--info)}.ok{color:var(--ok)}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:8px;
border-bottom:1px solid var(--line);vertical-align:top}
pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;
overflow:auto;font:13px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre}
.finding{border-left:4px solid currentColor}.finding>*{color:var(--fg)}
@media print{pre{white-space:pre-wrap}a{color:inherit}}
"""


def _when(value: datetime | None, tz: ZoneInfo) -> str:
    return value.astimezone(tz).strftime("%d/%m/%Y %H:%M") if value else "—"


def render(
    session: Session, review: Review, settings: Settings, now: datetime, expires: datetime
) -> tuple[str, str]:
    """(file name, HTML) of one MR review's analysis."""
    tz = ZoneInfo(settings.report_tz)
    mr = session.get(MergeRequest, review.mr_id) if review.mr_id else None
    project = session.get(Project, mr.project_id) if mr and mr.project_id else None
    connection = session.get(Connection, project.connection_id) if project else None
    repo = project.path_with_namespace if project else "?"
    iid = mr.iid if mr else "?"
    gitlab = (
        f"{connection.base_url.rstrip('/')}/{repo}/-/merge_requests/{iid}"
        if connection and project and mr and connection.base_url.startswith(("https://", "http://"))
        else None
    )
    author = session.get(User, review.author_user_id) if review.author_user_id else None
    if author is None and mr and mr.author_account_id:
        account = session.get(Account, mr.author_account_id)
        who = f"@{account.display_name}" if account and account.display_name else "chưa gắn người"
    else:
        who = (author.full_name or author.primary_email or "") if author else "chưa gắn người"
    findings = findings_of(session, review.id)
    t = tally(findings)
    top = t.top
    verdict = VERDICT_LABEL.get(review.verdict or "", review.verdict or "—")
    score = f"{review.score:.0f}" if review.score is not None else "—"
    dashboard = f"{settings.dashboard_url.rstrip('/')}/reviews/{review.id}"
    title = f"Kết quả review MR !{iid} · {repo}"

    out: list[str] = [
        "<!doctype html><html lang='vi'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<meta name='robots' content='noindex'>",
        # Opened later from a file viewer, where the HTTP header does not apply.
        "<meta http-equiv='Content-Security-Policy'"
        " content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        f"<title>{_e(title)}</title><style>{STYLE}</style></head><body><main>",
        f"<h1>{_e(title)}</h1>",
        f"<p class='muted'>{_e(mr.title if mr and mr.title else '')}</p>",
        "<p class='small muted'>"
        f"Tác giả: {_e(who)} · Commit đã review: <code>{_e(review.reviewed_sha[:12])}</code>"
        f" · Review xong: {_e(_when(review.finished_at, tz))}"
        + (f" · <a href='{_e(gitlab)}'>Mở MR trên GitLab</a>" if gitlab else "")
        + f" · <a href='{_e(dashboard)}'>Mở trên CodePulse</a></p>",
        "<div class='kpis'>",
        f"<div class='kpi'><span class='muted small'>Kết luận</span><b>{_e(verdict)}</b></div>",
        f"<div class='kpi'><span class='muted small'>Điểm</span><b>{_e(score)}/100</b></div>",
        "<div class='kpi'><span class='muted small'>Mức cao nhất</span><b class='"
        + (f"{top.key}'>{_e(top.label)}" if top else "ok'>Không có lỗi")
        + "</b></div>",
    ]
    for key in SHOWN:
        lv = LEVEL[key]
        out.append(
            f"<div class='kpi'><span class='muted small'>{_e(lv.label)}</span>"
            f"<b class='{key}'>{t.counted.get(key, 0)}</b></div>"
        )
    out.append("</div>")
    if t.unverified:
        out.append(
            f"<p class='small muted'>{t.unverified} finding chưa kiểm chứng được: liệt kê bên"
            " dưới để người xem quyết định, không tính vào điểm và kết luận.</p>"
        )
    summary = (review.intent or {}).get("summary") if isinstance(review.intent, dict) else None
    if summary:
        out.append(f"<div class='card'><h3>MR này làm gì</h3><p>{_e(summary)}</p></div>")

    out.append("<h2>Các mức nghĩa là gì</h2><table><thead><tr><th>Mức</th><th>Nghĩa là</th>")
    out.append("<th>Cần làm</th></tr></thead><tbody>")
    for lv in LEVELS:
        out.append(
            f"<tr><td><span class='badge {lv.key}'>{_e(lv.label)}</span></td>"
            f"<td>{_e(lv.meaning)}</td><td>{_e(lv.action)}</td></tr>"
        )
    out.append("</tbody></table>")

    out.append("<h2>Chi tiết</h2>")
    if not findings:
        out.append("<p class='ok'>Không có finding nào.</p>")
    ordered = sorted(
        findings,
        key=lambda f: (
            (f.status or "open") in NOT_COUNTED,  # not counted ones last
            [lv.key for lv in LEVELS].index(f.severity) if f.severity in LEVEL else 9,
        ),
    )
    for n, f in enumerate(ordered, 1):
        level = LEVEL.get(f.severity)
        where = f.file_path or ""
        if f.line_start:
            where += f":{f.line_start}" + (
                f"-{f.line_end}" if f.line_end and f.line_end != f.line_start else ""
            )
        notes = [f.category]
        if f.verified in VERIFIED_LABEL:
            notes.append(VERIFIED_LABEL[f.verified])
        if f.status == "false_positive":
            notes.append("lead đánh dấu báo nhầm, không tính")
        elif f.status == "fixed":
            notes.append("đã sửa")
        out.append(f"<section class='card finding {_e(f.severity)}'>")
        label = level.label if level else f.severity
        out.append(
            f"<h3>{n}. <span class='badge {_e(f.severity)}'>{_e(label)}</span>"
            f" {_e(f.title or f.rule_id or '')}</h3>"
        )
        out.append(
            f"<p class='small muted'><code>{_e(where or '—')}</code> · {_e(' · '.join(notes))}</p>"
        )
        if f.message:
            out.append(f"<p>{_e(f.message)}</p>")
        if f.suggestion:
            out.append(f"<p><b>Gợi ý sửa:</b> {_e(f.suggestion)}</p>")
        if f.code_excerpt:
            out.append(f"<pre>{_e(f.code_excerpt)}</pre>")
        out.append("</section>")
    out.append(
        f"<p class='small muted'>Tạo lúc {_e(_when(now, tz))} bởi CodePulse. Link tải hết hạn"
        f" khoảng {_e(_when(expires, tz))}. Điểm là tín hiệu coaching, không phải căn cứ duy nhất"
        " để đánh giá.</p></main></body></html>"
    )
    safe_repo = "".join(
        c if (c.isascii() and c.isalnum()) or c in "-_" else "-" for c in repo.split("/")[-1]
    )
    return f"codepulse-review-{safe_repo}-mr{iid}.html", "".join(out)
