"""The dự án's optional shared .claude repo (outside the spec, requested by the user; see
PROGRESS.md and app/review/claude_repo.py). Admins pick a GitLab repo, a branch and extra
globs; "Kiểm tra" downloads it and shows which files a deep review would load."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.schemas import ClaudeRepoIn, ClaudeRepoPreviewOut
from app.api.security import AdminPrincipal
from app.collector.gitlab.client import GitLabError
from app.collector.sync import client_factory
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.models import BusinessProject, Connection
from app.review.claude_repo import load_shared_config
from app.review.discover import instruction_kind

router = APIRouter(tags=["admin"])
SessionDep = Annotated[Session, Depends(get_session)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]


def _get[M](session: Session, model: type[M], ident: int, name: str) -> M:
    row = session.get(model, ident)
    if row is None:
        raise HTTPException(404, f"{name} not found")
    return row


@router.put("/business-projects/{bp_id}/claude-repo", status_code=204)
def set_claude_repo(
    bp_id: int, body: ClaudeRepoIn, principal: AdminPrincipal, session: SessionDep, box: BoxDep
) -> None:
    """Check the repo (and branch) on GitLab, then store it for the dự án."""
    bp = _get(session, BusinessProject, bp_id, "dự án")
    connection = _get(session, Connection, body.connection_id, "connection")
    with client_factory(box)(connection) as client:
        try:
            info = client.project_by_path(body.path)
        except GitLabError as exc:
            if exc.status_code == 404:
                raise HTTPException(422, f"GitLab has no repo {body.path} (or no access)") from None
            raise
        if body.ref and not client.branch_exists(int(info["id"]), body.ref):
            raise HTTPException(422, f"branch {body.ref} not found in {body.path}")
    bp.claude_repo_connection_id = connection.id
    bp.claude_repo_external_id = int(info["id"])
    bp.claude_repo_path = str(info.get("path_with_namespace") or body.path)
    bp.claude_repo_ref = body.ref or None
    bp.claude_repo_include = (body.include or "").strip() or None
    audit(
        session,
        principal.user_id,
        "business_project.claude_repo",
        f"business_project:{bp.id}",
        repo=bp.claude_repo_path,
        ref=bp.claude_repo_ref,
    )
    session.commit()


@router.delete("/business-projects/{bp_id}/claude-repo", status_code=204)
def clear_claude_repo(bp_id: int, principal: AdminPrincipal, session: SessionDep) -> None:
    bp = _get(session, BusinessProject, bp_id, "dự án")
    bp.claude_repo_connection_id = bp.claude_repo_external_id = None
    bp.claude_repo_path = bp.claude_repo_ref = bp.claude_repo_include = None
    audit(session, principal.user_id, "business_project.claude_repo", f"business_project:{bp.id}")
    session.commit()


@router.post("/business-projects/{bp_id}/claude-repo/preview")
def preview_claude_repo(
    bp_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    box: BoxDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ClaudeRepoPreviewOut:
    """Download the shared repo now and list what a deep review would load from it (and what
    it would not, with the reason). Nothing is sent to the model."""
    bp = _get(session, BusinessProject, bp_id, "dự án")
    if not bp.claude_repo_connection_id:
        raise HTTPException(404, "no shared .claude repo configured")
    connection = _get(session, Connection, bp.claude_repo_connection_id, "connection")
    with client_factory(box)(connection) as client:
        shared = load_shared_config(
            client, bp, connection.id, sensitive_paths=settings.sensitive_paths
        )
    if shared is None:
        raise HTTPException(404, "no shared .claude repo configured")
    loaded: list[dict[str, Any]] = []
    not_loaded: list[dict[str, Any]] = []
    if shared.docs is not None:
        loaded = [
            {
                "path": d.path,
                "kind": instruction_kind(d.path),
                "chars": len(d.text),
                "clipped": d.clipped,
            }
            for d in shared.docs.instructions
        ]
        not_loaded = [{"path": p, "reason": r} for p, r in shared.docs.not_loaded]
    return ClaudeRepoPreviewOut(info=shared.info, loaded=loaded, not_loaded=not_loaded)
