"""GitLab members of a repo or dự án -> CodePulse people, and commit authors -> people (outside
docs/specs/05, requested by the user 2026-10-26; see app/identity/members.py). Admin only;
every change is audited, one entry per linked account with its previous owner.

Errors are in Vietnamese: the Admin screen shows them as they are to a non-technical admin."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.schemas import (
    AccountOut,
    AuthorLinkIn,
    GitLabMemberOut,
    GitLabMembersImport,
    GitLabMembersImported,
    GitLabMembersOut,
)
from app.api.security import AdminPrincipal
from app.collector.gitlab.client import GitLabError
from app.collector.sync import client_factory
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.identity.members import (
    Choice,
    GitLabUser,
    ImportRefused,
    fetch_members,
    import_members,
    link_email,
    resolve,
    suggest,
)
from app.models import Account, BusinessProject, Connection, Project, User

router = APIRouter(tags=["identity"])
SessionDep = Annotated[Session, Depends(get_session)]

REFUSED = {
    "unknown_gitlab_user": (404, "Không tìm thấy tài khoản GitLab {0} nữa; lấy lại danh sách."),
    "inactive": (409, "{0}: tài khoản GitLab không còn hoạt động."),
    "email_taken": (409, "Email {0} đã là của một người khác; chọn 'Gắn với' người đó."),
    "account_linked": (
        409,
        "{0} đã được gắn với một người khác; lấy lại danh sách để xem người đó.",
    ),
    "person_has_account": (409, "Người được chọn đã có một tài khoản GitLab khác."),
    "user_not_found": (404, "Không tìm thấy người được chọn; tải lại trang."),
}


def _refused(exc: ImportRefused) -> HTTPException:
    status, text = REFUSED.get(exc.code, (409, "{0}"))
    return HTTPException(status, text.format(str(exc)))


def _members(
    session: Session, box: SecretBox, settings: Settings, projects: list[Project]
) -> dict[str, Any]:
    connections = {
        c.id: c
        for c in session.scalars(
            select(Connection).where(Connection.id.in_({p.connection_id for p in projects}))
        )
    }
    linked = {
        int(key)
        for key in session.scalars(
            select(Account.external_key).where(
                Account.kind == "gitlab_user", Account.user_id.is_not(None)
            )
        )
        if key.isdigit()
    }
    session.commit()  # no transaction stays open while GitLab is paginated
    try:
        listing = fetch_members(projects, connections, client_factory(box), linked)
    except GitLabError as exc:
        if exc.status_code in (401, 403, 404):
            raise HTTPException(
                502, "GitLab: token của bot không xem được thành viên của repo này."
            ) from None
        raise
    rows = suggest(session, listing.members, settings.allowed_email_domains)
    return {
        "repos": len(projects),
        "truncated": listing.truncated,
        "members": [
            GitLabMemberOut(
                gitlab_id=s.member.gitlab_id,
                username=s.member.username,
                name=s.member.name,
                state=s.member.state,
                access=s.member.access,
                email=s.member.email,
                repos=s.member.repos,
                connection_id=s.member.connection_id,
                action=s.action,
                user_id=s.user_id,
                reason=s.reason,
            )
            for s in rows
        ],
    }


@router.get("/projects/{project_id}/gitlab-members", response_model=GitLabMembersOut)
def repo_members(
    project_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """Who has access to the repo, with what CodePulse should do with each of them."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return _members(session, box, settings, [project])


@router.get("/business-projects/{bp_id}/gitlab-members", response_model=GitLabMembersOut)
def project_members(
    bp_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    """The members of every tracked repo of the dự án, once per GitLab user."""
    if session.get(BusinessProject, bp_id) is None:
        raise HTTPException(404, "dự án not found")
    projects = list(
        session.scalars(
            select(Project)
            .where(Project.business_project_id == bp_id, Project.tracked)
            .order_by(Project.path_with_namespace)
        )
    )
    return _members(session, box, settings, projects)


@router.post("/gitlab-members/import", response_model=GitLabMembersImported)
def import_gitlab_members(
    body: GitLabMembersImport,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitLabMembersImported:
    """Create or pick the person of each confirmed member and link their GitLab account.

    Each member is read back from GitLab first (username, state): the request only says who
    goes to whom, never what the account is called."""
    choices = [Choice(**c.model_dump()) for c in body.members]
    wanted: dict[int, list[int]] = {}
    for c in choices:
        if c.action != "skip":
            wanted.setdefault(c.connection_id, []).append(c.gitlab_id)
    connections = {
        c.id: c for c in session.scalars(select(Connection).where(Connection.id.in_(wanted)))
    }
    if set(wanted) - set(connections):
        raise HTTPException(404, "Không tìm thấy kết nối GitLab; lấy lại danh sách.")
    session.commit()  # no transaction stays open while GitLab answers
    gitlab: dict[int, GitLabUser] = {}
    make = client_factory(box)
    try:
        for connection_id, ids in wanted.items():
            with make(connections[connection_id]) as client:
                gitlab |= resolve(client, ids)
        result = import_members(session, choices, gitlab, settings.allowed_email_domains)
    except ImportRefused as exc:
        session.rollback()
        raise _refused(exc) from None
    except IntegrityError:
        session.rollback()  # the same email created concurrently
        raise HTTPException(409, "Email vừa được dùng cho người khác; thử lại.") from None
    for item in result.items:
        audit(session, principal.user_id, "identity.import_member", "gitlab", **item)
    audit(
        session,
        principal.user_id,
        "identity.import_members",
        "gitlab",
        created=result.created,
        linked=result.linked,
        skipped=result.skipped,
        autolinked=result.autolinked,
    )
    session.commit()
    return GitLabMembersImported(
        created=len(result.created),
        linked=result.linked,
        skipped=result.skipped,
        emails_linked=result.emails_linked,
    )


@router.post("/identity/authors", response_model=AccountOut)
def link_author(body: AuthorLinkIn, principal: AdminPrincipal, session: SessionDep) -> Account:
    """A commit author (from the commit list) to an existing person or a new one."""
    email = body.email.strip().lower()
    try:
        if body.create:
            taken = select(User.id).where(func.lower(User.primary_email) == email)
            if session.scalars(taken).first() is not None:
                raise ImportRefused("email_taken", email)
            user = User(
                full_name=(body.name or email.partition("@")[0]).strip(), primary_email=email
            )
            session.add(user)
            session.flush()
            user_id = user.id
        elif body.user_id is not None:
            if session.get(User, body.user_id) is None:
                raise ImportRefused("user_not_found", str(body.user_id))
            user_id = body.user_id
        else:
            raise HTTPException(422, "Chọn một người (user_id) hoặc tạo mới (create: true).")
        account, previous = link_email(session, email, body.name, user_id)
    except ImportRefused as exc:
        session.rollback()
        raise _refused(exc) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "Email vừa được dùng cho người khác; thử lại.") from None
    audit(
        session,
        principal.user_id,
        "account.link",
        f"account:{account.id}",
        email=email,
        user_id=user_id,
        previous_user_id=previous,
        created=body.create,
    )
    session.commit()
    return account
