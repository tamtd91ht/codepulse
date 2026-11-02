"""Download of an MR review's analysis by signed link (app/telegram/mr_report.py).

No session: the token is the permission (the user's choice, 2026-11-02: links in a Telegram
chat open on a phone without logging in). It names one review and an expiry, is HMAC-signed,
and is refused once expired. Every download is audited.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.config import Settings, get_settings
from app.models import Review
from app.telegram.mr_report import read_token, render

router = APIRouter(tags=["review-reports"])

# The file is static HTML: no script, no outside request, not indexed, not cached.
HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:",
}


def _refused(text: str, status: int) -> HTMLResponse:
    page = (
        "<!doctype html><html lang='vi'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>CodePulse</title><body style='font-family:system-ui;padding:24px'><p>{text}</p>"
    )
    return HTMLResponse(page, status_code=status, headers=HEADERS)


@router.get("/review-reports/{token}", response_model=None)
def download_review_report(
    token: str,
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    now = datetime.now(UTC)
    review_id, expires, why = read_token(settings, token[:200], now)
    if review_id is None or expires is None:
        if why == "expired":
            return _refused(
                "Link tải kết quả đã hết hạn. Gõ lại lệnh /review trong Telegram, hoặc mở"
                " review trên CodePulse.",
                410,
            )
        return _refused("Link không hợp lệ.", 404)
    review = session.get(Review, review_id)
    if review is None or review.target_type != "mr" or review.status != "done":
        return _refused("Không còn kết quả review này.", 404)
    name, body = render(session, review, settings, now, expires)
    audit(session, None, "telegram.report_download", f"review:{review.id}")
    session.commit()
    return Response(
        body,
        media_type="text/html; charset=utf-8",
        headers={**HEADERS, "Content-Disposition": f'attachment; filename="{name}"'},
    )
