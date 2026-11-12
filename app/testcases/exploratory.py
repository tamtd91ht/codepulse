"""Exploratory sessions (docs/specs/30 §15.12, 30a G8): a timed session on a test of a run, with
notes taken as it goes.

The session is kept on the test (`test_run_tests.session`):
{started_at, elapsed_s, running, notes: [{at, offset_s, kind, text, user_id, user_name}]}.
`started_at` is the start of the current running segment (after a pause: of the last one; set
at all means the session was started), `elapsed_s` the time banked by the segments before it;
the time shown is `elapsed_s + (now - started_at)` while `running`, else `elapsed_s` (`elapsed`).
Pausing adds the running segment to `elapsed_s`; resuming starts a new segment. Recording the
result copies the notes into its comment and takes the session's time when none is typed
(`session_summary`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.testcases import content

ACTIONS = ("start", "pause", "resume")
NOTE_KINDS = ("note", "bug", "question", "idea")
NOTE_LABEL = {"note": "Ghi chú", "bug": "Bug", "question": "Câu hỏi", "idea": "Ý tưởng"}
NOTE_MAX = 2_000
NOTES_MAX = 500


class SessionError(ValueError):
    """An action that does not fit the session's state (409); the message is shown as is."""


def _at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def elapsed(state: dict[str, Any] | None, now: datetime | None = None) -> int:
    """Seconds the session has run so far."""
    if not state:
        return 0
    total = int(state.get("elapsed_s") or 0)
    since = _at(state.get("started_at"))
    if state.get("running") and since is not None:
        total += max(0, int(((now or datetime.now(UTC)) - since).total_seconds()))
    return total


def act(state: dict[str, Any] | None, action: str, now: datetime | None = None) -> dict[str, Any]:
    """The session after `action` (start | pause | resume); raises SessionError."""
    at = now or datetime.now(UTC)
    out = dict(state or {})
    started = bool(out.get("started_at"))
    if action == "start":
        if started:
            raise SessionError("phiên đã bắt đầu; dùng tạm dừng / tiếp tục")
        return {
            "started_at": at.isoformat(),
            "elapsed_s": 0,
            "running": True,
            "notes": list(out.get("notes") or []),
        }
    if action == "pause":
        if not started or not out.get("running"):
            raise SessionError("phiên không chạy")
        out["elapsed_s"] = elapsed(out, at)
        out["running"] = False
        return out
    if action == "resume":
        if not started:
            raise SessionError("phiên chưa bắt đầu")
        if out.get("running"):
            raise SessionError("phiên đang chạy")
        out["running"] = True
        out["started_at"] = at.isoformat()
        return out
    raise SessionError("thao tác không hợp lệ")


def add_note(
    state: dict[str, Any] | None,
    kind: str,
    text: str,
    user_id: int | None,
    now: datetime | None = None,
    user_name: str | None = None,
) -> dict[str, Any]:
    """The session with one more note; raises SessionError (not started, too many) or ValueError
    (kind, empty or long text)."""
    at = now or datetime.now(UTC)
    out = dict(state or {})
    if not out.get("started_at"):
        raise SessionError("phiên chưa bắt đầu")
    if kind not in NOTE_KINDS:
        raise ValueError("loại ghi chú không hợp lệ")
    body = (text or "").replace("\r\n", "\n").strip()
    if not body:
        raise ValueError("ghi chú trống")
    if len(body) > NOTE_MAX:
        raise ValueError(f"ghi chú tối đa {NOTE_MAX} ký tự")
    notes = list(out.get("notes") or [])
    if len(notes) >= NOTES_MAX:
        raise SessionError(f"mỗi phiên tối đa {NOTES_MAX} ghi chú")
    notes.append(
        {
            "at": at.isoformat(),
            "offset_s": elapsed(out, at),
            "kind": kind,
            "text": body,
            "user_id": user_id,
            "user_name": user_name,
        }
    )
    out["notes"] = notes
    return out


def _clock(seconds: int) -> str:
    h, rest = divmod(max(0, int(seconds)), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def session_summary(
    state: dict[str, Any] | None, now: datetime | None = None
) -> tuple[str, int | None]:
    """What recording a result takes from the session: a comment listing its notes in order
    ("[mm:ss] Bug: ..."), and its time in seconds (None: never started). ("", None) for a test
    without a session."""
    if not state or not state.get("started_at"):
        return "", None
    seconds = elapsed(state, now)
    notes = [n for n in state.get("notes") or [] if isinstance(n, dict)]
    head = f"Phiên exploratory ({content.format_estimate(seconds) or '0s'})"
    if not notes:
        return head, seconds
    lines = [f"{head}:"]
    for note in notes:
        label = NOTE_LABEL.get(str(note.get("kind")), "Ghi chú")
        lines.append(
            f"[{_clock(int(note.get('offset_s') or 0))}] {label}: {note.get('text') or ''}"
        )
    return "\n".join(lines), seconds
