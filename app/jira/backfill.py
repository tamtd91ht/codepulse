"""One-off: `merge_requests.jira_keys` of the MRs converted before docs/specs/27 (NULL there).

The keys are derived again from the MR's latest stored raw payload (raw_gitlab, endpoint
`merge_request`: title, description, source branch, as the extract does); an MR whose raw rows
are gone (retention) gets the keys of its stored title and branch. Then the MRs are linked to
the Jira issues already synced (`link_merge_requests`). Batched by MR id, each batch committed
on its own; only rows still NULL are written, so a convert running beside it wins.

Queued once by the scheduler after the upgrade (`app_settings[DONE_KEY]` records it); runs on a
worker. Idempotent: a later run finds nothing NULL.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.collector.gitlab import collect as ep
from app.jira.links import extract_keys, link_merge_requests
from app.models import AppSetting, MergeRequest, RawGitlab

log = logging.getLogger(__name__)

DONE_KEY = "jira_keys_backfill_v1"
JOB_ID = "jira-keys-backfill-once"
JOB_TIMEOUT = 2 * 60 * 60
BATCH = 500


def _raw_payloads(session: Session, mrs: list[Any]) -> dict[tuple[int, int], dict[str, Any]]:
    """(project_id, iid) -> the latest stored MR payload, for these MRs."""
    wanted = {(int(m.project_id), int(m.iid)) for m in mrs}
    iid = RawGitlab.params["iid"].astext.cast(Integer)
    rows = session.execute(
        select(RawGitlab.project_id, iid, RawGitlab.payload)
        .where(
            RawGitlab.endpoint == ep.EP_MR,
            RawGitlab.project_id.in_(sorted({p for p, _ in wanted})),
            iid.in_(sorted({i for _, i in wanted})),
        )
        .ext(distinct_on(RawGitlab.project_id, iid))
        .order_by(RawGitlab.project_id, iid, RawGitlab.id.desc())
    )
    return {
        (int(pid), int(n)): payload
        for pid, n, payload in rows
        if (int(pid), int(n)) in wanted and isinstance(payload, dict)
    }


def backfill_batch(session: Session, after_id: int = 0, batch: int = BATCH) -> tuple[int, int, int]:
    """Fill the keys of up to `batch` MRs with NULL keys and id > `after_id`; the caller
    commits. Returns (the last MR id seen, how many MRs (0: none was left), links written)."""
    mrs = session.execute(
        select(
            MergeRequest.id,
            MergeRequest.project_id,
            MergeRequest.iid,
            MergeRequest.title,
            MergeRequest.source_branch,
        )
        .where(MergeRequest.jira_keys.is_(None), MergeRequest.id > after_id)
        .order_by(MergeRequest.id)
        .limit(batch)
    ).all()
    if not mrs:
        return after_id, 0, 0
    raw = _raw_payloads(session, list(mrs))
    for mr in mrs:
        payload = raw.get((int(mr.project_id), int(mr.iid)))
        if payload is not None:
            keys = extract_keys(
                payload.get("title"),
                payload.get("description"),
                branch=payload.get("source_branch"),
            )
        else:
            keys = extract_keys(mr.title, branch=mr.source_branch)
        session.execute(
            update(MergeRequest)
            .where(MergeRequest.id == mr.id, MergeRequest.jira_keys.is_(None))
            .values(jira_keys=keys)
        )
    links = link_merge_requests(session, [int(mr.id) for mr in mrs])
    session.flush()
    return int(mrs[-1].id), len(mrs), links


def backfill_keys(session: Session, batch: int = BATCH) -> dict[str, int]:
    """Every MR with NULL keys, `batch` at a time, each batch committed."""
    out = {"merge_requests": 0, "links": 0}
    after = 0
    while True:
        last, seen, written = backfill_batch(session, after, batch)
        if not seen:
            break
        session.commit()
        after = last
        out["merge_requests"] += seen
        out["links"] += written
    return out


def backfill_keys_once_job() -> dict[str, Any]:
    """RQ entry point, queued by the scheduler at start-up: runs once per database."""
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        if session.get(AppSetting, DONE_KEY) is not None:
            return {"already": True}
        out = backfill_keys(session)
        session.add(AppSetting(key=DONE_KEY, value={"at": datetime.now(UTC).isoformat(), **out}))
        session.commit()
    log.info("jira keys backfill done", extra=out)
    return out
