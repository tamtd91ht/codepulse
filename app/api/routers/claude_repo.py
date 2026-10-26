"""The dự án's optional shared .claude repo (outside the spec, requested by the user; see
PROGRESS.md and app/review/claude_repo.py). Admins pick a GitLab repo, a branch and extra
globs; "Kiểm tra" downloads it and shows which files a deep review always quotes (review
commands, CLAUDE.md, pinned files) and which it picks per commit (app/review/claude_kit.py)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.schemas import ClaudeRepoIn, ClaudeRepoPreviewOut
from app.api.security import AdminPrincipal
from app.collector.gitlab.client import GitLabClient, GitLabError
from app.collector.sync import client_factory
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.models import BusinessProject, Connection
from app.review.claude_kit import MIN_SCORE, KitFile, select
from app.review.claude_repo import load_shared_config
from app.review.discover import CLIPPED

router = APIRouter(tags=["admin"])
SessionDep = Annotated[Session, Depends(get_session)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]


def _get[M](session: Session, model: type[M], ident: int, name: str) -> M:
    row = session.get(model, ident)
    if row is None:
        raise HTTPException(404, f"{name} not found")
    return row


# Folders a pasted link may point at: the repo itself, or its .claude folder (both read the same).
LINK_FOLDERS = ("", ".claude")


def _ref_of_link(client: GitLabClient, project_id: int, path: str, words: list[str]) -> str:
    """The branch of a folder link (…/-/tree/feature/x/.claude): the longest leading words that
    name a branch; what follows must be the repo root or its .claude folder."""
    for end in range(min(len(words), 8), 0, -1):
        ref = "/".join(words[:end])
        if client.branch_exists(project_id, ref):
            folder = "/".join(words[end:])
            if folder not in LINK_FOLDERS:
                raise HTTPException(
                    422,
                    f"link trỏ tới thư mục {folder}; chỉ dùng được .claude ở gốc repo"
                    f" (…/-/tree/{ref}/.claude) hoặc repo mà nội dung chính là .claude",
                )
            return ref
    raise HTTPException(422, f"không thấy nhánh nào của {path} trong link; nhập nhánh riêng")


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
        ref = body.ref
        if not ref and body.tree:
            ref = _ref_of_link(client, int(info["id"]), body.path, body.tree)
        elif ref and not client.branch_exists(int(info["id"]), ref):
            raise HTTPException(422, f"branch {ref} not found in {body.path}")
    bp.claude_repo_connection_id = connection.id
    bp.claude_repo_external_id = int(info["id"])
    bp.claude_repo_path = str(info.get("path_with_namespace") or body.path)
    bp.claude_repo_ref = ref or None
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
    """Download the shared repo now and list what every deep review quotes from it and what is
    chosen per commit (with the reason). Nothing is sent to the model."""
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
    # No change to rank against: what is left is exactly what each commit decides.
    chosen = select(shared.files, [], "", None, shared.info["include"])

    def row(f: KitFile) -> dict[str, Any]:
        return {
            "path": f.rel,
            "kind": f.kind,
            "name": f.name,
            "description": f.description,
            "chars": f.chars,
            "clipped": f.text.endswith(CLIPPED),
            "procedure": f.procedure,
            "why": f.why,
        }

    every = chosen.procedure + chosen.ranked + chosen.catalog
    loaded = [row(f) for f in every if f.procedure or f.score >= MIN_SCORE]
    not_loaded = [
        {**row(f), "reason": "paths" if f.globs else "by_commit"}
        for f in every
        if not (f.procedure or f.score >= MIN_SCORE)
    ]
    return ClaudeRepoPreviewOut(info=shared.info, loaded=loaded, not_loaded=not_loaded)
