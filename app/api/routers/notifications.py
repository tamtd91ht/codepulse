"""Outbound notification webhooks (admin). Outside the spec, requested by the user; see
docs/specs/16_NOTIFICATION_WEBHOOKS.md. Secrets are encrypted at rest and never returned."""

import logging
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.schemas import (
    NotificationWebhookIn,
    NotificationWebhookOut,
    NotificationWebhookPatch,
)
from app.api.security import AdminPrincipal
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.models import BusinessProject, NotificationWebhook, Project
from app.notifications.deliver import TIMEOUT_S
from app.notifications.jobs import send_test

log = logging.getLogger(__name__)
router = APIRouter(tags=["notifications"])
SessionDep = Annotated[Session, Depends(get_session)]


def get_http_client() -> httpx.Client:
    """Overridden in tests; one short-lived client per test delivery."""
    return httpx.Client(timeout=TIMEOUT_S, follow_redirects=False)


def _get[M](session: Session, model: type[M], ident: int, name: str) -> M:
    row = session.get(model, ident)
    if row is None:
        raise HTTPException(404, f"{name} not found")
    return row


def _check_scope(session: Session, business_project_id: int | None, project_id: int | None) -> None:
    if business_project_id is not None:
        _get(session, BusinessProject, business_project_id, "dự án")
    if project_id is not None:
        project = _get(session, Project, project_id, "repo")
        if business_project_id is not None and project.business_project_id != business_project_id:
            raise HTTPException(422, "repo does not belong to this dự án")


def _out(hook: NotificationWebhook) -> NotificationWebhookOut:
    return NotificationWebhookOut(
        id=hook.id,
        name=hook.name,
        url=hook.url,
        format=hook.format,
        has_secret=bool(hook.secret_encrypted),
        events=list(hook.events or []),
        business_project_id=hook.business_project_id,
        project_id=hook.project_id,
        enabled=hook.enabled,
        last_delivery_at=hook.last_delivery_at,
        last_status_code=hook.last_status_code,
        last_error=hook.last_error,
        failures=hook.failures,
        deliveries=hook.deliveries,
        created_at=hook.created_at,
    )


@router.get("/webhooks", response_model=list[NotificationWebhookOut])
def list_webhooks(principal: AdminPrincipal, session: SessionDep) -> list[NotificationWebhookOut]:
    rows = session.scalars(select(NotificationWebhook).order_by(NotificationWebhook.id))
    return [_out(h) for h in rows]


@router.post("/webhooks", response_model=NotificationWebhookOut, status_code=201)
def create_webhook(
    body: NotificationWebhookIn,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> NotificationWebhookOut:
    _check_scope(session, body.business_project_id, body.project_id)
    hook = NotificationWebhook(
        name=body.name,
        url=str(body.url),
        format=body.format,
        secret_encrypted=box.encrypt(body.secret) if body.secret else None,
        events=body.events,
        business_project_id=body.business_project_id,
        project_id=body.project_id,
        enabled=body.enabled,
    )
    session.add(hook)
    session.flush()
    audit(
        session,
        principal.user_id,
        "webhook.create",
        f"webhook:{hook.id}",
        host=urlsplit(hook.url).hostname,  # chat webhook URLs are secrets: never the full URL
        format=hook.format,
        events=hook.events,
        business_project_id=hook.business_project_id,
        project_id=hook.project_id,
    )
    session.commit()
    return _out(hook)


@router.patch("/webhooks/{webhook_id}", response_model=NotificationWebhookOut)
def update_webhook(
    webhook_id: int,
    body: NotificationWebhookPatch,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
) -> NotificationWebhookOut:
    hook = _get(session, NotificationWebhook, webhook_id, "webhook")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to update")
    if "business_project_id" in changes or "project_id" in changes:
        bp = changes.get("business_project_id", hook.business_project_id)
        project = changes.get("project_id", hook.project_id)
        _check_scope(session, bp, project)
        hook.business_project_id, hook.project_id = bp, project
    if body.name is not None:
        hook.name = body.name
    if body.url is not None:
        hook.url = str(body.url)
    if body.format is not None:
        hook.format = body.format
    if body.events is not None:
        hook.events = list(body.events)
    if "secret" in changes:  # "" removes the secret, a value replaces it
        hook.secret_encrypted = box.encrypt(body.secret) if body.secret else None
    if body.enabled is not None:
        hook.enabled = body.enabled
        if body.enabled:
            hook.failures, hook.last_error = 0, None
    audit(
        session,
        principal.user_id,
        "webhook.update",
        f"webhook:{hook.id}",
        fields=sorted(changes),  # names only, never the secret
    )
    session.commit()
    return _out(hook)


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: int, principal: AdminPrincipal, session: SessionDep) -> None:
    hook = _get(session, NotificationWebhook, webhook_id, "webhook")
    session.delete(hook)
    audit(
        session,
        principal.user_id,
        "webhook.delete",
        f"webhook:{webhook_id}",
        name=hook.name,
        host=urlsplit(hook.url).hostname,
    )
    session.commit()


@router.post("/webhooks/{webhook_id}/test")
def test_webhook(
    webhook_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    settings: Annotated[Settings, Depends(get_settings)],
    http: Annotated[httpx.Client, Depends(get_http_client)],
) -> dict[str, Any]:
    """POST a sample `ping` payload to the endpoint and report what it answered."""
    hook = _get(session, NotificationWebhook, webhook_id, "webhook")
    session.commit()  # no transaction stays open while the endpoint is called
    with http:
        result = send_test(hook, session, box, settings.dashboard_url, http)
    session.refresh(hook)
    audit(
        session,
        principal.user_id,
        "webhook.test",
        f"webhook:{hook.id}",
        ok=result.ok,
        status=result.status_code,
    )
    session.commit()
    return {
        "ok": result.ok,
        "status_code": result.status_code,
        "error": result.error,
        "attempts": result.attempts,
        "enabled": hook.enabled,
    }
