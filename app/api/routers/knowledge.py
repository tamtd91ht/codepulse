"""Review knowledge of a repo (docs/specs/19 §3.5): the active build and its versions, the
"Rà soát lại dự án" button, approval and manual edits.

Reading is open to anyone signed in (like repo profiles: it is built from the repository's own
documents); refreshing, approving and editing need a lead or an admin, and are audited.
"""

import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_secret_box, get_session
from app.api.security import CurrentPrincipal, LeadPrincipal
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.sync import Enqueuer
from app.crypto import SecretBox
from app.models import (
    BusinessProject,
    Connection,
    Project,
    ReviewKnowledge,
    ReviewKnowledgeEdit,
    User,
)
from app.models.knowledge import EDIT_ACTIONS
from app.review.knowledge import (
    MAX_MANUAL_INVARIANTS,
    SEVERITIES,
    Knowledge,
    active_knowledge,
    claude_tree_sha,
    edit_dict,
    edits_for,
    is_stale,
)
from app.workers.knowledge_jobs import KNOWLEDGE_JOB_TIMEOUT, job_active, job_id_for

log = logging.getLogger(__name__)
router = APIRouter(tags=["knowledge"])
SessionDep = Annotated[Session, Depends(get_session)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]
QueueDep = Annotated[Enqueuer, Depends(get_queue)]

REQUEST_TIMEOUT_S = 10.0
MAX_EDIT_PAYLOAD_BYTES = 4096  # a manual edit is a sentence or two, never a document
MANUAL_ID_PREFIX = "manual:"


class KnowledgeEditIn(BaseModel):
    # `item_id` names an existing item for disable / set_severity / reword; add_invariant
    # ignores it (the server assigns a `manual:` id).
    item_id: str = Field(default="", max_length=120)
    action: Literal["disable", "set_severity", "reword", "add_invariant"]
    payload: dict[str, Any] = Field(default_factory=dict)


def _tracked_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or not project.tracked:
        raise HTTPException(404, "repo not found")
    return project


def _manual_id(taken: set[str]) -> str:
    """A fresh `manual:` id no edit of the repo uses yet."""
    while True:
        candidate = f"{MANUAL_ID_PREFIX}{secrets.token_hex(4)}"
        if candidate not in taken:
            return candidate


def _client(box: SecretBox, connection: Connection) -> GitLabClient:
    return GitLabClient(
        connection.base_url,
        box.decrypt(connection.token_encrypted),
        timeout=REQUEST_TIMEOUT_S,
        max_retries=0,
    )


def current_shas(
    session: Session, box: SecretBox, project: Project
) -> tuple[str | None, str | None]:
    """(sha of the repo's `.claude` tree at the default branch, head sha of the dự án's shared
    .claude repo), best effort: None where GitLab does not answer or there is nothing."""
    connection = session.get(Connection, project.connection_id) if project.connection_id else None
    if connection is None:
        return None, None
    business = (
        session.get(BusinessProject, project.business_project_id)
        if project.business_project_id
        else None
    )
    claude = shared = None
    try:
        with _client(box, connection) as client:
            claude = claude_tree_sha(client, project.external_id, project.default_branch or "HEAD")
            if (
                business is not None
                and business.claude_repo_external_id
                and business.claude_repo_connection_id == connection.id
            ):
                ext = business.claude_repo_external_id
                ref = business.claude_repo_ref or str(client.project(ext)["default_branch"])
                shared = str(client.commit(ext, ref).get("id") or "") or None
    except (GitLabError, KeyError, TypeError) as exc:
        log.warning("knowledge staleness not checked", extra={"error": exc.__class__.__name__})
    return claude, shared


def _user_name(session: Session, user_id: int | None) -> str | None:
    user = session.get(User, user_id) if user_id else None
    return user.full_name if user else None


def knowledge_out(session: Session, row: ReviewKnowledge, stale: bool) -> dict[str, Any]:
    knowledge = Knowledge.model_validate(row.data or {})
    return {
        "id": row.id,
        "version": row.version,
        "schema_version": row.schema_version,
        "status": row.status,
        "approved_by": _user_name(session, row.approved_by_user_id),
        "approved_by_user_id": row.approved_by_user_id,
        "approved_at": row.approved_at,
        "built_by": row.built_by,
        "created_at": row.created_at,
        "sources": row.sources or {},
        "data": row.data,
        "stale": stale,
        "counts": knowledge.counts(),
    }


def _version_out(row: ReviewKnowledge) -> dict[str, Any]:
    meta = (row.data or {}).get("meta") or {}
    return {
        "id": row.id,
        "version": row.version,
        "status": row.status,
        "created_at": row.created_at,
        "built_by": row.built_by,
        "approved_at": row.approved_at,
        "cost_usd": meta.get("cost_usd"),
        "error": meta.get("error") if row.status == "failed" else None,
        "counts": Knowledge.model_validate(row.data or {}).counts(),
    }


@router.get("/projects/{project_id}/knowledge")
def get_knowledge(
    project_id: int, principal: CurrentPrincipal, session: SessionDep, box: BoxDep, queue: QueueDep
) -> dict[str, Any]:
    """The active build with its manual edits; `stats` arrives with task 8.11."""
    project = _tracked_project(session, project_id)
    row, _ = active_knowledge(session, project)
    active: dict[str, Any] | None = None
    if row is not None:
        claude_sha, shared_sha = current_shas(session, box, project)
        active = knowledge_out(session, row, is_stale(row, claude_sha, shared_sha))
    return {
        "project_id": project.id,
        "active": active,
        "edits": [edit_dict(e) for e in edits_for(session, project)],
        "stats": None,
        "building": job_active(queue, job_id_for(project.id)),
        # The newest failed build when it is newer than the active one (or there is
        # none): the page must say why a build failed, not only that it did.
        "last_failure": _last_failure(session, project, row),
    }


def _last_failure(
    session: Session, project: Project, active: ReviewKnowledge | None
) -> dict[str, Any] | None:
    failed = session.scalars(
        select(ReviewKnowledge)
        .where(
            ReviewKnowledge.scope == "project",
            ReviewKnowledge.project_id == project.id,
            ReviewKnowledge.status == "failed",
        )
        .order_by(ReviewKnowledge.created_at.desc(), ReviewKnowledge.id.desc())
        .limit(1)
    ).first()
    if failed is None or (active is not None and failed.created_at <= active.created_at):
        return None
    meta = (failed.data or {}).get("meta") or {}
    return {
        "id": failed.id,
        "created_at": failed.created_at,
        "built_by": failed.built_by,
        "error": meta.get("error"),
        "cost_usd": meta.get("cost_usd"),
    }


@router.get("/projects/{project_id}/knowledge/versions")
def list_versions(
    project_id: int, principal: CurrentPrincipal, session: SessionDep
) -> list[dict[str, Any]]:
    project = _tracked_project(session, project_id)
    rows = session.scalars(
        select(ReviewKnowledge)
        .where(ReviewKnowledge.scope == "project", ReviewKnowledge.project_id == project.id)
        .order_by(ReviewKnowledge.version.desc())
    )
    return [_version_out(r) for r in rows]


@router.get("/projects/{project_id}/knowledge/versions/{version}")
def get_version(
    project_id: int,
    version: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    box: BoxDep,
) -> dict[str, Any]:
    project = _tracked_project(session, project_id)
    row = session.scalars(
        select(ReviewKnowledge).where(
            ReviewKnowledge.scope == "project",
            ReviewKnowledge.project_id == project.id,
            ReviewKnowledge.version == version,
        )
    ).first()
    if row is None:
        raise HTTPException(404, "version not found")
    claude_sha, shared_sha = current_shas(session, box, project)
    return knowledge_out(session, row, is_stale(row, claude_sha, shared_sha))


@router.post("/projects/{project_id}/knowledge/refresh", status_code=202)
def refresh_knowledge(
    project_id: int, principal: LeadPrincipal, session: SessionDep, queue: QueueDep
) -> dict[str, str]:
    """ "Rà soát lại dự án": rebuild the premises from the default branch in the worker."""
    project = _tracked_project(session, project_id)
    if project.allow_external_llm is False:
        raise HTTPException(409, "this repo does not allow sending code to an external LLM")
    job_id = job_id_for(project.id)
    if job_active(queue, job_id):
        raise HTTPException(409, "a build is already queued or running for this repo")
    queue.enqueue(
        "app.workers.knowledge_jobs.build_knowledge_job",
        project.id,
        requested_by=principal.user_id,
        built_by="manual",
        job_id=job_id,
        job_timeout=KNOWLEDGE_JOB_TIMEOUT,
    )
    audit(session, principal.user_id, "project.knowledge_refresh", f"project:{project.id}")
    session.commit()
    return {"job_id": job_id}


@router.post("/projects/{project_id}/knowledge/approve")
def approve_knowledge(
    project_id: int, principal: LeadPrincipal, session: SessionDep, box: BoxDep
) -> dict[str, Any]:
    project = _tracked_project(session, project_id)
    row, _ = active_knowledge(session, project)
    if row is None:
        raise HTTPException(404, "this repo has no review knowledge yet")
    row.approved_by_user_id = principal.user_id
    row.approved_at = datetime.now(UTC)
    audit(
        session,
        principal.user_id,
        "project.knowledge_approve",
        f"project:{project.id}",
        knowledge_id=row.id,
        version=row.version,
    )
    session.commit()
    claude_sha, shared_sha = current_shas(session, box, project)
    return knowledge_out(session, row, is_stale(row, claude_sha, shared_sha))


@router.post("/projects/{project_id}/knowledge/edits", status_code=201)
def add_edit(
    project_id: int, body: KnowledgeEditIn, principal: LeadPrincipal, session: SessionDep
) -> dict[str, Any]:
    """A manual change kept across builds (docs/specs/19 §3.4, §7): disable, set_severity,
    reword an existing item, or add_invariant (at most MAX_MANUAL_INVARIANTS)."""
    project = _tracked_project(session, project_id)
    if body.action not in EDIT_ACTIONS:  # pragma: no cover - Literal already refuses
        raise HTTPException(422, "unknown action")
    if len(json.dumps(body.payload, ensure_ascii=False).encode("utf-8")) > MAX_EDIT_PAYLOAD_BYTES:
        raise HTTPException(422, f"payload over {MAX_EDIT_PAYLOAD_BYTES} bytes")
    row, _ = active_knowledge(session, project)
    existing = edits_for(session, project)
    item_id = body.item_id.strip()
    if body.action == "add_invariant":
        statement = str(body.payload.get("statement") or "").strip()
        if not statement:
            raise HTTPException(422, "payload.statement is required")
        manual = [e for e in existing if e.action == "add_invariant" and e.scope == "project"]
        if len(manual) >= MAX_MANUAL_INVARIANTS:
            raise HTTPException(422, f"at most {MAX_MANUAL_INVARIANTS} manual invariants")
        severity = str(body.payload.get("severity") or "high")
        if severity not in SEVERITIES:
            raise HTTPException(422, "payload.severity must be a severity")
        # The server names manual items, so a client cannot shadow a compiled item's id or
        # another lead's addition.
        item_id = _manual_id({e.item_id for e in existing})
    else:
        if not item_id:
            raise HTTPException(422, "item_id is required")
        if row is None:
            raise HTTPException(404, "this repo has no review knowledge yet")
        knowledge = Knowledge.model_validate(row.data or {})
        if knowledge.find(item_id) is None:
            raise HTTPException(404, f"no item {item_id} in the active knowledge")
        if body.action == "set_severity":
            severity = str(body.payload.get("severity") or "")
            if severity not in SEVERITIES:
                raise HTTPException(422, "payload.severity must be a severity")
        if (
            body.action == "reword"
            and not str(body.payload.get("statement") or body.payload.get("text") or "").strip()
        ):
            raise HTTPException(422, "payload.statement is required")
    edit = ReviewKnowledgeEdit(
        scope="project",
        project_id=project.id,
        item_id=item_id,
        action=body.action,
        payload=body.payload,
        user_id=principal.user_id,
    )
    session.add(edit)
    session.flush()
    audit(
        session,
        principal.user_id,
        "project.knowledge_edit",
        f"project:{project.id}",
        edit_id=edit.id,
        item_id=item_id,
        edit_action=body.action,
    )
    session.commit()
    return edit_dict(edit)


@router.delete("/projects/{project_id}/knowledge/edits/{edit_id}", status_code=204)
def delete_edit(
    project_id: int, edit_id: int, principal: LeadPrincipal, session: SessionDep
) -> None:
    project = _tracked_project(session, project_id)
    edit = session.get(ReviewKnowledgeEdit, edit_id)
    if edit is None or edit.scope != "project" or edit.project_id != project.id:
        raise HTTPException(404, "edit not found")
    session.delete(edit)
    audit(
        session,
        principal.user_id,
        "project.knowledge_edit_delete",
        f"project:{project.id}",
        edit_id=edit_id,
        item_id=edit.item_id,
        edit_action=edit.action,
    )
    session.commit()
