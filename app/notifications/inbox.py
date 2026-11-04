"""In-app notifications (docs/specs/24): who hears about what, written with the event itself.

`notify` adds the rows to the event's own transaction and remembers them on the session; once
that transaction commits, they are published on Redis (CHANNEL), where every API process fans
them out to the browsers of their recipients (app/notifications/stream.py). A rolled-back event
publishes nothing. Redis being down loses the live push only: the rows stay, the bell's history
and unread count read them from the database.
"""

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event, func, inspect, or_, select
from sqlalchemy.orm import Session, SessionTransaction

from app.config import Settings
from app.models import (
    BusinessProject,
    Commit,
    FindingClosure,
    FindingDispute,
    MergeRequest,
    Project,
    ProjectGrant,
    Review,
    ReviewFinding,
    User,
    UserNotification,
)

log = logging.getLogger(__name__)

CHANNEL = "codepulse:notifications"
PENDING = "inbox_pending"
# Reviews queued in bulk: nobody is pinged per review (a backfill of a repo or a rescore that
# re-reviews thin results would send hundreds, to the author and to who started it).
QUIET_TRIGGERS = frozenset({"backfill", "criteria_rescore"})
SEVERITY_LABEL = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}
CLOSED_AS = {
    "fixed": "đã sửa",
    "accepted_risk": "chấp nhận rủi ro",
    "false_positive": "finding sai",
}


_client: Any = None


def _publish_redis(messages: list[dict[str, Any]]) -> None:
    """On a client of its own with short timeouts: the event has committed already, so a Redis
    that does not answer must not hold the request (and the shared pool is never closed)."""
    global _client
    if _client is None:
        from redis import Redis

        from app.config import get_settings

        _client = Redis.from_url(
            str(get_settings().redis_url), socket_connect_timeout=2, socket_timeout=2
        )
    for message in messages:
        _client.publish(CHANNEL, json.dumps(message, default=str))


# Swapped in tests; never raises into the caller (the event is already committed).
publisher: Callable[[list[dict[str, Any]]], None] = _publish_redis


def as_message(row: UserNotification) -> dict[str, Any]:
    """What the browser receives, live and in the history (one shape for both)."""
    return {
        "id": row.id,
        "user_id": row.user_id,
        "kind": row.kind,
        "title": row.title,
        "body": row.body,
        "link": row.link,
        "tone": row.tone,
        "ref": row.ref,
        "actor_id": row.actor_id,
        "read_at": row.read_at.isoformat() if row.read_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    rows: list[UserNotification] = session.info.pop(PENDING, [])
    # A row added in a SAVEPOINT that was rolled back is no longer persistent: not published.
    kept = [row for row in rows if inspect(row).persistent]
    if not kept:
        return
    try:
        publisher([as_message(row) for row in kept])
    except Exception:
        log.warning("live notification not published", exc_info=True)


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    """The outermost transaction ended (committed: already published; rolled back: dropped)."""
    if transaction.parent is None:
        session.info.pop(PENDING, None)


def notify(
    session: Session,
    user_ids: Iterable[int | None],
    *,
    kind: str,
    title: str,
    body: str | None = None,
    link: str | None = None,
    tone: str = "info",
    actor_id: int | None = None,
    ref: str | None = None,
) -> list[UserNotification]:
    """One row per recipient (deduplicated; the actor never notifies themselves), in the
    caller's transaction; published live after it commits."""
    targets = sorted({u for u in user_ids if u is not None and u != actor_id})
    if not targets:
        return []
    now = datetime.now(UTC)
    active = set(
        session.scalars(select(User.id).where(User.id.in_(targets), User.active.is_not(False)))
    )
    rows = [
        UserNotification(
            user_id=user_id,
            kind=kind,
            title=title[:200],
            body=body[:500] if body else None,
            link=link,
            tone=tone,
            actor_id=actor_id,
            ref=ref,
            created_at=now,
        )
        for user_id in targets
        if user_id in active
    ]
    if not rows:
        return []
    session.add_all(rows)
    session.flush()
    session.info.setdefault(PENDING, []).extend(rows)
    return rows


# -- what each event says ------------------------------------------------------------------------


def _target(session: Session, review: Review) -> tuple[Project | None, str]:
    """The repo of a review and how the change is named ("gateway !12", "gateway a1b2c3d4")."""
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        project = session.get(Project, mr.project_id) if mr and mr.project_id else None
        what = f"!{mr.iid}" if mr else "MR"
    else:
        commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
        project = session.get(Project, commit.project_id) if commit and commit.project_id else None
        what = (review.commit_sha or "")[:8] or "commit"
    repo = (project.path_with_namespace or "").rsplit("/", 1)[-1] if project else ""
    return project, f"{repo} {what}".strip()


def _counts(session: Session, review_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    for severity in session.scalars(
        select(ReviewFinding.severity).where(ReviewFinding.review_id == review_id)
    ):
        counts[str(severity)] = counts.get(str(severity), 0) + 1
    return counts


VERDICT = {"approve": "Có thể merge", "fix_required": "Cần sửa", "block": "Chặn merge"}


def review_finished(session: Session, review: Review) -> list[UserNotification]:
    """A review ended: its author hears (unless a backfill or a rescore ran it), and whoever
    asked for it; a failed one only the person who asked."""
    if review.status not in ("done", "failed", "skipped") or review.trigger in QUIET_TRIGGERS:
        return []
    _, name = _target(session, review)
    link = f"/reviews/{review.id}"
    asked = review.requested_by_user_id
    if review.status != "done":
        failed = review.status == "failed"
        return notify(
            session,
            [asked],
            kind="review_failed",
            title=f"{'Review lỗi' if failed else 'Review không chạy'}: {name}",
            body=(review.error or "Mở để xem lý do và chạy lại.")[:300],
            link=link,
            tone="bad" if failed else "warn",
            ref=f"review:{review.id}",
        )
    counts = _counts(session, review.id)
    parts = [f"Điểm {float(review.score):.0f}"] if review.score is not None else []
    severe = [f"{counts[s]} {s}" for s in ("critical", "high") if counts.get(s)]
    parts.append(", ".join(severe) if severe else f"{sum(counts.values())} finding")
    if review.verdict in VERDICT:
        parts.append(VERDICT[review.verdict])
    tone = "bad" if counts.get("critical") else "warn" if counts.get("high") else "good"
    return notify(
        session,
        [asked, review.author_user_id],
        kind="review_done",
        title=f"Review xong: {name}",
        body=" · ".join(parts),
        link=link,
        tone=tone,
        ref=f"review:{review.id}",
    )


def _finding_owner(session: Session, finding: ReviewFinding) -> Review | None:
    return session.get(Review, finding.review_id) if finding.review_id else None


def finding_closed(
    session: Session, finding: ReviewFinding, closure: FindingClosure, actor_id: int | None
) -> list[UserNotification]:
    """A finding was closed (a close request checked or confirmed, an admin's close): the
    author of the change, and who asked for the close when that is someone else."""
    review = _finding_owner(session, finding)
    how = CLOSED_AS.get(closure.closed_as or "fixed", "đã đóng")
    if closure.severity_after and closure.severity_before != closure.severity_after:
        how += f", hạ {closure.severity_before} → {closure.severity_after}"
    return notify(
        session,
        [review.author_user_id if review else None, closure.requested_by],
        kind="finding_closed",
        title=f"Finding đã đóng ({how})",
        body=finding.title,
        link=f"/reviews/{finding.review_id}#finding-{finding.id}" if finding.review_id else None,
        tone="good",
        actor_id=actor_id,
        ref=f"finding:{finding.id}",
    )


def findings_closed_by_review(
    session: Session, review: Review, finding_ids: list[int]
) -> list[UserNotification]:
    """Findings a later change fixed, closed by its review on its own (auto-close, or an MR's
    new push no longer reporting them): one note per author and per review of theirs, linked
    to that review (the fixing one may be someone else's, out of their sight)."""
    if not finding_ids:
        return []
    rows = session.execute(
        select(
            Review.author_user_id, ReviewFinding.id, ReviewFinding.title, ReviewFinding.review_id
        )
        .join(Review, Review.id == ReviewFinding.review_id)
        .where(ReviewFinding.id.in_(finding_ids))
    ).all()
    groups: dict[tuple[int, int], list[tuple[int, str]]] = {}
    for author, fid, title, rid in rows:
        if author is not None and rid is not None:
            groups.setdefault((int(author), int(rid)), []).append((int(fid), str(title)))
    _, name = _target(session, review)
    out: list[UserNotification] = []
    for (author, rid), items in groups.items():
        one = len(items) == 1
        out += notify(
            session,
            [author],
            kind="finding_closed",
            title=(
                f"Finding đã tự đóng nhờ {name}"
                if one
                else f"{len(items)} finding đã tự đóng nhờ {name}"
            ),
            body="; ".join(t for _, t in items[:3]) + ("…" if len(items) > 3 else ""),
            link=f"/reviews/{rid}#finding-{items[0][0]}" if one else f"/reviews/{rid}",
            tone="good",
            ref=f"finding:{items[0][0]}" if one else f"review:{rid}",
        )
    return out


def _dispute_finding(session: Session, dispute: FindingDispute) -> ReviewFinding | None:
    return session.get(ReviewFinding, dispute.finding_id) if dispute.finding_id else None


def _bp_of_review(session: Session, review: Review | None) -> int | None:
    if review is None:
        return None
    project, _ = _target(session, review)
    return project.business_project_id if project else None


def dispute_deciders(
    session: Session, raiser: int, bp_id: int | None, settings: Settings
) -> list[int]:
    """Who may decide this person's dispute in dự án `bp_id` (`dispute.decide` covering their
    work, never themselves), as on the dispute page. Only someone who sees the dự án can
    (`covers_work`: a grant on it, manage_all, or an admin; a repo outside every dự án is the
    admins'), so the candidates are those few, then each one's rights are checked."""
    from fastapi import HTTPException

    from app.api.access import covers_work  # lazy: API layer
    from app.api.security import load_principal

    admins = {e.lower() for e in settings.admin_emails}
    sees: list[Any] = []
    if bp_id is not None:
        sees.append(User.manage_all.is_(True))
        sees.append(
            User.id.in_(
                select(ProjectGrant.user_id).where(ProjectGrant.business_project_id == bp_id)
            )
        )
    if admins:
        sees.append(func.lower(User.primary_email).in_(admins))
    if not sees:
        return []
    candidates = session.scalars(
        select(User.id).where(User.active.is_not(False), User.id != raiser, or_(*sees))
    )
    deciders: list[int] = []
    for uid in sorted(set(candidates)):
        try:
            principal = load_principal(session, uid, settings)
        except HTTPException:  # made inactive meanwhile
            log.info("dispute decider skipped", extra={"user_id": uid})
            continue
        if covers_work(principal, "dispute.decide", raiser, bp_id):
            deciders.append(uid)
    return deciders


def dispute_raised(
    session: Session, dispute: FindingDispute, settings: Settings
) -> list[UserNotification]:
    finding = _dispute_finding(session, dispute)
    review = _finding_owner(session, finding) if finding else None
    if dispute.raised_by is None or finding is None:
        return []
    raiser = session.get(User, dispute.raised_by)
    who = (raiser.full_name or raiser.primary_email) if raiser else "Một thành viên"
    bp_id = _bp_of_review(session, review)
    bp = session.get(BusinessProject, bp_id) if bp_id is not None else None
    return notify(
        session,
        dispute_deciders(session, dispute.raised_by, bp_id, settings),
        kind="dispute_new",
        title=f"Khiếu nại mới từ {who}",
        body=f"{finding.severity} · {finding.title}"
        + (f" ({bp.name})" if bp is not None else "")
        + (f" — {dispute.reason}" if dispute.reason else ""),
        link="/disputes",
        tone="warn",
        actor_id=dispute.raised_by,
        ref=f"dispute:{dispute.id}",
    )


def dispute_decided(session: Session, dispute: FindingDispute) -> list[UserNotification]:
    finding = _dispute_finding(session, dispute)
    accepted = dispute.decision == "accepted"
    outcome = "finding được tính là sai, điểm đã tính lại." if accepted else "finding vẫn mở."
    return notify(
        session,
        [dispute.raised_by],
        kind="dispute_decided",
        title="Khiếu nại được chấp nhận" if accepted else "Khiếu nại bị từ chối",
        body=f"{finding.title} — {outcome}" if finding else outcome,
        link=f"/reviews/{finding.review_id}#finding-{finding.id}"
        if finding and finding.review_id
        else "/disputes",
        tone="good" if accepted else "warn",
        actor_id=dispute.decided_by,
        ref=f"dispute:{dispute.id}",
    )
