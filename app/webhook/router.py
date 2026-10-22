"""POST /api/v1/webhooks/gitlab: authenticate, de-duplicate, enqueue, answer 200 (docs/specs/04)."""

import hmac
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_queue, get_secret_box, get_session
from app.collector.sync import Enqueuer
from app.crypto import SecretBox
from app.db import get_redis
from app.models import Connection, Project

log = logging.getLogger(__name__)
router = APIRouter()

SUPPORTED_EVENTS = {"Push Hook", "Merge Request Hook", "Pipeline Hook"}
MR_ACTIONS = {"open", "reopen", "update", "merge"}
DEDUPE_TTL_S = 7 * 24 * 3600


def project_external_id(payload: dict[str, Any]) -> int | None:
    value = (payload.get("project") or {}).get("id") or payload.get("project_id")
    return int(value) if value is not None else None


def token_matches(session: Session, box: SecretBox, external_id: int, token: str) -> bool:
    """Constant-time check against every connection that has a project with this GitLab id."""
    secrets = session.scalars(
        select(Connection.webhook_secret_encrypted)
        .join(Project, Project.connection_id == Connection.id)
        .where(Project.external_id == external_id)
    ).all()
    matched = False
    for encrypted in secrets:
        if not encrypted:
            continue
        try:
            secret = box.decrypt(encrypted)
        except ValueError:
            log.error("webhook secret cannot be decrypted", extra={"project": external_id})
            continue
        if hmac.compare_digest(secret.encode(), token.encode()):
            matched = True  # keep looping so timing does not reveal which connection matched
    return matched


@router.post("/webhooks/gitlab")
def gitlab_webhook(
    # Sync handler: DB and Redis calls are blocking, so FastAPI runs it in its threadpool.
    payload: Annotated[Any, Body()],
    session: Annotated[Session, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    queue: Annotated[Enqueuer, Depends(get_queue)],
    box: Annotated[SecretBox, Depends(get_secret_box)],
    x_gitlab_token: Annotated[str, Header()] = "",
    x_gitlab_event: Annotated[str, Header()] = "",
    x_gitlab_event_uuid: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    external_id = project_external_id(payload) if isinstance(payload, dict) else None
    if external_id is None:
        raise HTTPException(400, "payload has no project id")
    if not x_gitlab_token or not token_matches(session, box, external_id, x_gitlab_token):
        raise HTTPException(401, "invalid webhook token")

    if x_gitlab_event not in SUPPORTED_EVENTS:
        return {"status": "ignored"}
    if x_gitlab_event == "Merge Request Hook":
        action = (payload.get("object_attributes") or {}).get("action")
        if action not in MR_ACTIONS:
            return {"status": "ignored"}

    if x_gitlab_event_uuid and not redis.set(
        f"webhook:gitlab:{x_gitlab_event_uuid}", 1, nx=True, ex=DEDUPE_TTL_S
    ):
        return {"status": "duplicate"}

    queue.enqueue(
        "app.webhook.jobs.process_gitlab_event",
        x_gitlab_event,
        payload,
        job_id=f"webhook-{x_gitlab_event_uuid}" if x_gitlab_event_uuid else None,
    )
    log.info(
        "webhook accepted",
        extra={"event": x_gitlab_event, "project": external_id, "uuid": x_gitlab_event_uuid},
    )
    return {"status": "queued"}
