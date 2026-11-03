"""Weighted criteria sets (docs/specs/22 §6): the sets and their versions (`criteria.manage`),
the sets of a dự án (`criteria.assign`), a preview that writes nothing, and the summary of the
stored scores, which follows `review.view` like the reviews themselves."""

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.api.access import review_repo, reviews_in_projects, work_scope
from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.security import CurrentPrincipal, Principal, require
from app.collector.sync import Enqueuer
from app.config import Settings, get_settings
from app.models import (
    BusinessProject,
    Commit,
    CriteriaSet,
    CriteriaSetVersion,
    MergeRequest,
    Project,
    ProjectCriteriaSet,
    Review,
    ReviewCriteriaScore,
    ReviewFinding,
    User,
)
from app.scoring.criteria import (
    CATEGORIES,
    CATEGORY_LABELS,
    KEY,
    MAX_COMPONENTS,
    MIN_COMPONENTS,
    TEMPLATES,
    Components,
)
from app.scoring.criteria_store import SetVersion, score_review
from app.workers.criteria_jobs import JOB, JOB_TIMEOUT

log = logging.getLogger(__name__)
router = APIRouter(tags=["criteria"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
QueueDep = Annotated[Enqueuer, Depends(get_queue)]
ManagePrincipal = Annotated[Principal, require("criteria.manage")]
AssignPrincipal = Annotated[Principal, require("criteria.assign")]
ViewPrincipal = Annotated[Principal, require("review.view")]

NAME_MAX, DESCRIPTION_MAX, NOTE_MAX = 100, 1000, 500
PREVIEW_MAX = 20


# -- payloads -----------------------------------------------------------------------------------


class SetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    # Omitted with `from_template`: the template's components.
    components: list[dict[str, Any]] | None = None
    apply_caps: bool = True
    from_template: str | None = None


class SetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=NAME_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    components: list[dict[str, Any]]
    apply_caps: bool = True
    note: str | None = Field(default=None, max_length=NOTE_MAX)


class CloneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=NAME_MAX)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    components: list[dict[str, Any]]
    apply_caps: bool = True
    business_project_id: int
    limit: int = Field(default=PREVIEW_MAX, ge=1, le=PREVIEW_MAX)
    # The set being edited, if any: the model's placements for it are used as stored.
    set_id: int | None = None


class AssignedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    set_id: int
    is_primary: bool = False


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sets: list[AssignedIn] = Field(max_length=50)


class RecomputeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    since: date | None = None


# -- helpers ------------------------------------------------------------------------------------


def _unprocessable(loc: list[Any], msg: str) -> HTTPException:
    return HTTPException(422, [{"loc": ["body", *loc], "msg": msg, "type": "value_error"}])


def _components(raw: list[dict[str, Any]] | None, apply_caps: bool) -> Components:
    """The components checked as a whole (docs/specs/22 §6 "Kiểm tra đầu vào"), else 422 whose
    `detail` names each field: [{loc, msg, type}] as FastAPI's own validation errors."""
    try:
        return Components.model_validate({"components": raw or [], "apply_caps": apply_caps})
    except ValidationError as exc:
        detail = []
        for err in exc.errors():
            loc = list(err["loc"]) or ["components"]
            msg = str(err["msg"]).removeprefix("Value error, ")
            detail.append({"loc": ["body", *loc], "msg": msg, "type": err["type"]})
        raise HTTPException(422, detail) from exc


def _dump(components: Components) -> list[dict[str, Any]]:
    return [c.model_dump() for c in components.components]


def _name(session: Session, name: str, exclude: int | None = None) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise _unprocessable(["name"], "tên không được trống")
    query = select(CriteriaSet.id).where(func.lower(CriteriaSet.name) == cleaned.lower())
    if exclude is not None:
        query = query.where(CriteriaSet.id != exclude)
    if session.scalar(query) is not None:
        raise HTTPException(409, "đã có bộ tiêu chí cùng tên")
    return cleaned


def _get_set(session: Session, set_id: int) -> CriteriaSet:
    found = session.get(CriteriaSet, set_id)
    if found is None:
        raise HTTPException(404, "không có bộ tiêu chí này")
    return found


def _may_read(principal: Principal) -> None:
    """The sets themselves: whoever makes them or gives them to a dự án."""
    if not (principal.can("criteria.manage") or principal.can("criteria.assign")):
        raise HTTPException(403, "thiếu quyền xem bộ tiêu chí")


def _may_assign(principal: Principal, bp_id: int) -> None:
    if not principal.covers("criteria.assign", None, bp_id):
        raise HTTPException(403, "thiếu quyền gán bộ tiêu chí cho dự án này")


def _business(session: Session, principal: Principal, bp_id: int) -> BusinessProject:
    bp = session.get(BusinessProject, bp_id)
    if bp is None or not principal.can_view_project(bp_id):
        raise HTTPException(404, "không có dự án này")
    return bp


def _used(session: Session, set_id: int) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(ReviewCriteriaScore.set_id == set_id)
                | exists().where(ProjectCriteriaSet.set_id == set_id)
            )
        )
    )


def _commit(session: Session) -> None:
    """A race lost to a concurrent request (same name, same version number, the same dự án's
    sets): 409, not 500."""
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            409, "xung đột với một thay đổi khác cùng lúc; tải lại rồi thử lại"
        ) from exc


def _new_version(
    session: Session,
    criteria_set: CriteriaSet,
    components: Components,
    note: str | None,
    user_id: int,
) -> CriteriaSetVersion:
    # Two edits at once: the second waits here and numbers its version after the first's.
    session.execute(
        select(CriteriaSet.id).where(CriteriaSet.id == criteria_set.id).with_for_update()
    )
    last = session.scalar(
        select(func.max(CriteriaSetVersion.version)).where(
            CriteriaSetVersion.set_id == criteria_set.id
        )
    )
    version = CriteriaSetVersion(
        set_id=criteria_set.id,
        version=(last or 0) + 1,
        components=_dump(components),
        apply_caps=components.apply_caps,
        note=(note or "").strip() or None,
        created_by=user_id,
    )
    session.add(version)
    session.flush()
    criteria_set.current_version_id = version.id
    return version


def _version_row(session: Session, version: CriteriaSetVersion) -> dict[str, Any]:
    """A version as the history lists it: who made it as an id and a name."""
    user = session.get(User, version.created_by) if version.created_by else None
    return {
        "id": version.id,
        "version": version.version,
        "note": version.note,
        "apply_caps": version.apply_caps,
        "created_at": version.created_at,
        "created_by": user.id if user else None,
        "created_by_name": user.full_name if user else None,
    }


def _set_out(session: Session, principal: Principal, criteria_set: CriteriaSet) -> dict[str, Any]:
    """A set as the lists show it: its current version and the dự án using it (names only of
    the dự án the viewer sees; the counts are of all)."""
    current = (
        session.get(CriteriaSetVersion, criteria_set.current_version_id)
        if criteria_set.current_version_id
        else None
    )
    links = session.execute(
        select(ProjectCriteriaSet, BusinessProject)
        .join(BusinessProject, BusinessProject.id == ProjectCriteriaSet.business_project_id)
        .where(ProjectCriteriaSet.set_id == criteria_set.id)
        .order_by(BusinessProject.name)
    ).tuples()
    projects = [(link, bp) for link, bp in links]
    return {
        "id": criteria_set.id,
        "name": criteria_set.name,
        "description": criteria_set.description,
        "archived": criteria_set.archived,
        "version": current.version if current else None,
        "version_id": current.id if current else None,
        "components": current.components if current else [],
        "apply_caps": current.apply_caps if current else True,
        "current_version": (
            {
                "id": current.id,
                "version": current.version,
                "components": current.components,
                "apply_caps": current.apply_caps,
                "note": current.note,
                "created_at": current.created_at,
            }
            if current
            else None
        ),
        # Any score or any dự án: then it is archived, never deleted.
        "used": _used(session, criteria_set.id),
        "projects_count": len(projects),
        "primary_count": sum(1 for link, _ in projects if link.is_primary),
        "projects": [
            {"id": bp.id, "name": bp.name, "is_primary": link.is_primary}
            for link, bp in projects
            if principal.can_view_project(bp.id)
        ],
        "created_at": criteria_set.created_at,
        "updated_at": criteria_set.updated_at,
    }


def _detail(session: Session, principal: Principal, criteria_set: CriteriaSet) -> dict[str, Any]:
    versions = session.scalars(
        select(CriteriaSetVersion)
        .where(CriteriaSetVersion.set_id == criteria_set.id)
        .order_by(CriteriaSetVersion.version.desc())
    )
    return {
        **_set_out(session, principal, criteria_set),
        "versions": [_version_row(session, v) for v in versions],
    }


def _version_out(session: Session, version: CriteriaSetVersion) -> dict[str, Any]:
    return {
        **_version_row(session, version),
        "set_id": version.set_id,
        "components": version.components,
    }


# -- meta and sets ------------------------------------------------------------------------------


@router.get("/criteria/meta")
def criteria_meta(principal: CurrentPrincipal) -> dict[str, Any]:
    return {
        "categories": [{"key": c, "label": CATEGORY_LABELS[c]} for c in CATEGORIES],
        "limits": {
            "min_components": MIN_COMPONENTS,
            "max_components": MAX_COMPONENTS,
            # A component's name and description (docs/specs/22 §1); the set's are set_*.
            "name_max": 60,
            "description_max": 500,
            "key_pattern": KEY.pattern,
            "component_name_max": 60,
            "component_description_max": 500,
            "weight_min": 1,
            "weight_max": 100,
            "weight_total": 100,
            "set_name_max": NAME_MAX,
            "set_description_max": DESCRIPTION_MAX,
            "note_max": NOTE_MAX,
            "preview_max": PREVIEW_MAX,
        },
        "templates": [{"key": key, **template} for key, template in TEMPLATES.items()],
    }


@router.get("/criteria-sets")
def list_sets(
    principal: CurrentPrincipal, session: SessionDep, archived: bool | None = None
) -> list[dict[str, Any]]:
    _may_read(principal)
    query = select(CriteriaSet).order_by(CriteriaSet.name)
    if archived is not None:
        query = query.where(CriteriaSet.archived.is_(archived))
    return [_set_out(session, principal, s) for s in session.scalars(query)]


@router.get("/criteria-sets/{set_id}")
def get_set(set_id: int, principal: CurrentPrincipal, session: SessionDep) -> dict[str, Any]:
    _may_read(principal)
    return _detail(session, principal, _get_set(session, set_id))


@router.get("/criteria-sets/{set_id}/versions/{version}")
def get_version(
    set_id: int, version: int, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    _may_read(principal)
    row = session.scalars(
        select(CriteriaSetVersion).where(
            CriteriaSetVersion.set_id == set_id, CriteriaSetVersion.version == version
        )
    ).first()
    if row is None:
        raise HTTPException(404, "không có phiên bản này")
    return _version_out(session, row)


@router.post("/criteria-sets", status_code=201)
def create_set(body: SetCreate, principal: ManagePrincipal, session: SessionDep) -> dict[str, Any]:
    components_raw, description = body.components, body.description
    if body.from_template is not None:
        template = TEMPLATES.get(body.from_template)
        if template is None:
            raise _unprocessable(["from_template"], "không có mẫu này")
        if components_raw is None:
            components_raw = template["components"]
        if description is None:
            description = template["description"]
    components = _components(components_raw, body.apply_caps)
    criteria_set = CriteriaSet(
        name=_name(session, body.name),
        description=(description or "").strip() or None,
        created_by=principal.user_id,
    )
    session.add(criteria_set)
    session.flush()
    note = f"Từ mẫu {TEMPLATES[body.from_template]['name']}" if body.from_template else None
    version = _new_version(session, criteria_set, components, note, principal.user_id)
    audit(
        session,
        principal.user_id,
        "criteria_set.create",
        f"criteria_set:{criteria_set.id}",
        name=criteria_set.name,
        version=version.version,
        from_template=body.from_template,
    )
    _commit(session)
    return _detail(session, principal, criteria_set)


@router.put("/criteria-sets/{set_id}")
def update_set(
    set_id: int, body: SetUpdate, principal: ManagePrincipal, session: SessionDep
) -> dict[str, Any]:
    """Name and description in place; components or caps that change make a new version
    (docs/specs/22 §1), the old ones stay as they were."""
    criteria_set = _get_set(session, set_id)
    components = _components(body.components, body.apply_caps)
    criteria_set.name = _name(session, body.name, exclude=criteria_set.id)
    criteria_set.description = (body.description or "").strip() or None
    current = (
        session.get(CriteriaSetVersion, criteria_set.current_version_id)
        if criteria_set.current_version_id
        else None
    )
    changed = (
        current is None
        or current.components != _dump(components)
        or current.apply_caps != components.apply_caps
    )
    version = (
        _new_version(session, criteria_set, components, body.note, principal.user_id)
        if changed
        else current
    )
    audit(
        session,
        principal.user_id,
        "criteria_set.update",
        f"criteria_set:{criteria_set.id}",
        name=criteria_set.name,
        version=version.version if version else None,
        new_version=changed,
    )
    _commit(session)
    return {**_detail(session, principal, criteria_set), "new_version": changed}


@router.post("/criteria-sets/{set_id}/clone", status_code=201)
def clone_set(
    set_id: int, body: CloneIn, principal: ManagePrincipal, session: SessionDep
) -> dict[str, Any]:
    source = _get_set(session, set_id)
    current = (
        session.get(CriteriaSetVersion, source.current_version_id)
        if source.current_version_id
        else None
    )
    if current is None:
        raise HTTPException(409, "bộ nguồn chưa có phiên bản nào")
    components = _components(current.components, current.apply_caps)
    copy = CriteriaSet(
        name=_name(session, body.name),
        description=source.description,
        created_by=principal.user_id,
    )
    session.add(copy)
    session.flush()
    note = f"Nhân bản từ {source.name} v{current.version}"
    _new_version(session, copy, components, note, principal.user_id)
    audit(
        session,
        principal.user_id,
        "criteria_set.clone",
        f"criteria_set:{copy.id}",
        source=source.id,
        source_version=current.version,
        name=copy.name,
    )
    _commit(session)
    return _detail(session, principal, copy)


@router.post("/criteria-sets/{set_id}/archive")
def archive_set(set_id: int, principal: ManagePrincipal, session: SessionDep) -> dict[str, Any]:
    criteria_set = _get_set(session, set_id)
    primary = session.scalar(
        select(func.count())
        .select_from(ProjectCriteriaSet)
        .where(ProjectCriteriaSet.set_id == set_id, ProjectCriteriaSet.is_primary.is_(True))
    )
    if primary:
        raise HTTPException(409, f"bộ đang là bộ chính ở {primary} dự án: đổi bộ chính trước")
    criteria_set.archived = True
    audit(session, principal.user_id, "criteria_set.archive", f"criteria_set:{set_id}")
    session.commit()
    return _detail(session, principal, criteria_set)


@router.post("/criteria-sets/{set_id}/unarchive")
def unarchive_set(set_id: int, principal: ManagePrincipal, session: SessionDep) -> dict[str, Any]:
    criteria_set = _get_set(session, set_id)
    criteria_set.archived = False
    audit(session, principal.user_id, "criteria_set.unarchive", f"criteria_set:{set_id}")
    session.commit()
    return _detail(session, principal, criteria_set)


@router.delete("/criteria-sets/{set_id}", status_code=204)
def delete_set(set_id: int, principal: ManagePrincipal, session: SessionDep) -> Response:
    """Only a set never used (no score, no dự án): a used one is archived instead."""
    criteria_set = _get_set(session, set_id)
    if _used(session, set_id):
        raise HTTPException(409, "bộ đã được dùng (có điểm hoặc đang gán dự án): hãy lưu trữ")
    name = criteria_set.name
    criteria_set.current_version_id = None
    session.flush()
    session.delete(criteria_set)
    audit(session, principal.user_id, "criteria_set.delete", f"criteria_set:{set_id}", name=name)
    session.commit()
    return Response(status_code=204)


# -- preview ------------------------------------------------------------------------------------


def _label(session: Session, review: Review) -> tuple[str, str]:
    """(target, title): "MR !12" and the MR's title, or "commit abc12345" and its first line."""
    if review.mr_id is not None:
        mr = session.get(MergeRequest, review.mr_id)
        target = f"MR !{mr.iid}" if mr else "MR"
        return target, (mr.title if mr and mr.title else target)
    target = f"commit {(review.commit_sha or '')[:8]}"
    commit = session.get(Commit, review.commit_sha) if review.commit_sha else None
    lines = (commit.message or "").strip().splitlines() if commit else []
    return target, (lines[0][:200] if lines else target)


@router.post("/criteria/preview")
def preview(
    body: PreviewIn,
    principal: ManagePrincipal,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """The set being edited, on the latest finished reviews of a dự án. Writes nothing."""
    components = _components(body.components, body.apply_caps)
    _business(session, principal, body.business_project_id)
    draft = SetVersion(
        set_id=body.set_id or 0,
        name="preview",
        version_id=0,
        version=0,
        components=components.components,
        apply_caps=components.apply_caps,
    )
    query = select(Review).where(
        Review.status == "done", reviews_in_projects([body.business_project_id])
    )
    if (allowed := work_scope(principal, "review.view")) is not None:
        query = query.where(allowed)
    reviews = session.scalars(
        query.order_by(Review.finished_at.desc().nulls_last(), Review.id.desc()).limit(body.limit)
    )
    out: list[dict[str, Any]] = []
    for review in reviews:
        findings = list(
            session.scalars(
                select(ReviewFinding)
                .where(ReviewFinding.review_id == review.id)
                .order_by(ReviewFinding.id)
            )
        )
        result = score_review(
            review,
            findings,
            draft,
            settings.scoring_weights,
            settings.verify_min_confidence_unverified,
        )
        repo = review_repo(session, review)
        author = session.get(User, review.author_user_id) if review.author_user_id else None
        current = float(review.score) if review.score is not None else None
        target, title = _label(session, review)
        out.append(
            {
                "review_id": review.id,
                "title": title,
                "target": target,
                "project": repo.path_with_namespace if repo else None,
                "author_user_id": review.author_user_id,
                "author": author.full_name if author else None,
                "finished_at": review.finished_at,
                "loc_reviewed": review.loc_reviewed,
                "findings_count": len(findings),
                "score": current,
                "criteria_score": result.score,
                "delta": round(result.score - current, 2) if current is not None else None,
                "capped": result.capped,
                "breakdown": result.breakdown(),
                "findings": [
                    {"id": f.id, "severity": f.severity, "category": f.category, "title": f.title}
                    for f in findings
                ],
            }
        )
    return {"business_project_id": body.business_project_id, "items": out}


# -- the sets of a dự án ------------------------------------------------------------------------


def _project_sets(session: Session, bp_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        select(ProjectCriteriaSet, CriteriaSet)
        .join(CriteriaSet, CriteriaSet.id == ProjectCriteriaSet.set_id)
        .where(ProjectCriteriaSet.business_project_id == bp_id)
        .order_by(ProjectCriteriaSet.position, CriteriaSet.id)
    ).tuples()
    out: list[dict[str, Any]] = []
    for link, criteria_set in rows:
        current = (
            session.get(CriteriaSetVersion, criteria_set.current_version_id)
            if criteria_set.current_version_id
            else None
        )
        out.append(
            {
                "set_id": criteria_set.id,
                "name": criteria_set.name,
                "description": criteria_set.description,
                "archived": criteria_set.archived,
                "is_primary": link.is_primary,
                "position": link.position,
                "version": current.version if current else None,
                "components": current.components if current else [],
                "apply_caps": current.apply_caps if current else True,
            }
        )
    return out


@router.get("/business-projects/{bp_id}/criteria-sets")
def list_project_sets(
    bp_id: int, principal: CurrentPrincipal, session: SessionDep
) -> list[dict[str, Any]]:
    _business(session, principal, bp_id)
    return _project_sets(session, bp_id)


def _enqueue(queue: Enqueuer, bp_id: int, set_id: int, since: str | None) -> bool:
    try:
        queue.enqueue(JOB, bp_id, set_id, since, job_timeout=JOB_TIMEOUT)
    except Exception:
        log.exception(
            "criteria recompute not queued", extra={"business_project_id": bp_id, "set_id": set_id}
        )
        return False
    return True


@router.put("/business-projects/{bp_id}/criteria-sets")
def assign_sets(
    bp_id: int,
    body: AssignIn,
    principal: AssignPrincipal,
    session: SessionDep,
    queue: QueueDep,
) -> list[dict[str, Any]]:
    """Replace the dự án's sets: in this order, exactly one primary when there are any. A set
    new to the dự án gets its scores recomputed by a job."""
    _business(session, principal, bp_id)
    _may_assign(principal, bp_id)
    # One replacement at a time per dự án: a concurrent one waits, then sees this one's rows.
    session.execute(select(BusinessProject.id).where(BusinessProject.id == bp_id).with_for_update())
    wanted = [item.set_id for item in body.sets]
    if len(set(wanted)) != len(wanted):
        raise _unprocessable(["sets"], "một bộ tiêu chí bị gán hai lần")
    if wanted and sum(1 for item in body.sets if item.is_primary) != 1:
        raise _unprocessable(["sets"], "cần đúng một bộ chính")
    found = {
        s.id: s for s in session.scalars(select(CriteriaSet).where(CriteriaSet.id.in_(wanted)))
    }
    missing = [i for i in wanted if i not in found]
    if missing:
        raise _unprocessable(["sets"], f"không có bộ tiêu chí: {', '.join(map(str, missing))}")
    before = {
        link.set_id: link
        for link in session.scalars(
            select(ProjectCriteriaSet).where(ProjectCriteriaSet.business_project_id == bp_id)
        )
    }
    added = [i for i in wanted if i not in before]
    archived = [found[i].name for i in added if found[i].archived]
    if archived:
        raise _unprocessable(["sets"], f"bộ đã lưu trữ không gán mới được: {', '.join(archived)}")
    unready = [found[i].name for i in wanted if found[i].current_version_id is None]
    if unready:
        raise _unprocessable(["sets"], f"bộ chưa có phiên bản: {', '.join(unready)}")
    # Delete and insert: updating in place would meet two primaries on the way.
    for link in before.values():
        session.delete(link)
    session.flush()
    session.add_all(
        ProjectCriteriaSet(
            business_project_id=bp_id,
            set_id=item.set_id,
            is_primary=item.is_primary,
            position=position,
        )
        for position, item in enumerate(body.sets)
    )
    session.flush()
    audit(
        session,
        principal.user_id,
        "project.criteria_sets",
        f"business_project:{bp_id}",
        sets=[{"set_id": i.set_id, "is_primary": i.is_primary} for i in body.sets],
        added=added,
        removed=[i for i in before if i not in wanted],
    )
    _commit(session)
    not_queued = [set_id for set_id in added if not _enqueue(queue, bp_id, set_id, None)]
    if not_queued:
        # Saved, but the old reviews have no score under the new sets yet: say so.
        raise HTTPException(
            503, "đã gán bộ tiêu chí nhưng chưa xếp được việc tính điểm; bấm Tính lại sau"
        )
    return _project_sets(session, bp_id)


@router.post("/business-projects/{bp_id}/criteria-sets/{set_id}/recompute", status_code=202)
def recompute(
    bp_id: int,
    set_id: int,
    principal: AssignPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
    body: RecomputeIn | None = None,
) -> dict[str, Any]:
    _business(session, principal, bp_id)
    _may_assign(principal, bp_id)
    if session.get(ProjectCriteriaSet, (bp_id, set_id)) is None:
        raise HTTPException(404, "dự án không dùng bộ tiêu chí này")
    since = body.since if body else None
    start = (
        datetime.combine(since, time.min, ZoneInfo(settings.report_tz)).isoformat()
        if since
        else None
    )
    audit(
        session,
        principal.user_id,
        "criteria_set.recompute",
        f"business_project:{bp_id}",
        set_id=set_id,
        since=start,
    )
    session.commit()
    if not _enqueue(queue, bp_id, set_id, start):
        raise HTTPException(503, "không xếp được việc tính lại; thử lại sau")
    return {"status": "queued", "set_id": set_id, "since": start}


# -- summary ------------------------------------------------------------------------------------


def _in_repo(project_id: int) -> Any:
    mr, commit = aliased(MergeRequest), aliased(Commit)
    return exists().where(mr.id == Review.mr_id, mr.project_id == project_id) | exists().where(
        commit.sha == Review.commit_sha, commit.project_id == project_id
    )


def _avg(total: float, weight: float) -> float | None:
    return round(total / weight, 2) if weight else None


@router.get("/criteria/summary")
def summary(
    principal: ViewPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    business_project_id: int | None = None,
    project_id: int | None = None,
    user_id: int | None = None,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
    set_id: int | None = None,
) -> dict[str, Any]:
    """LOC-weighted averages of the final score and of each component (like `avg_score`), over
    the finished reviews `review.view` shows, with a weekly trend. Without `set_id`: the
    dự án's primary set."""
    if business_project_id is not None:
        _business(session, principal, business_project_id)  # a dự án one does not see: 404
    bp_id = business_project_id
    if bp_id is None and project_id is not None:
        repo = session.get(Project, project_id)
        bp_id = repo.business_project_id if repo else None
    # The sets one may pick from: those of the dự án (when it is known and seen).
    sets = (
        [
            {"set_id": s["set_id"], "name": s["name"], "is_primary": s["is_primary"]}
            for s in _project_sets(session, bp_id)
        ]
        if bp_id is not None and principal.can_view_project(bp_id)
        else []
    )
    if set_id is None:
        set_id = next((s["set_id"] for s in sets if s["is_primary"]), None)
    criteria_set = session.get(CriteriaSet, set_id) if set_id is not None else None
    if criteria_set is None:
        if set_id is not None:
            raise HTTPException(404, "không có bộ tiêu chí này")
        return {
            "sets": sets,
            "set_id": None,
            "set_name": None,
            "version": None,
            "reviews": 0,
            "loc": 0,
            "score": None,
            "components": [],
            "weeks": [],
        }
    current = (
        session.get(CriteriaSetVersion, criteria_set.current_version_id)
        if criteria_set.current_version_id
        else None
    )
    query = (
        select(Review.loc_reviewed, Review.counted_at, ReviewCriteriaScore.score)
        .add_columns(ReviewCriteriaScore.breakdown)
        .join(ReviewCriteriaScore, ReviewCriteriaScore.review_id == Review.id)
        .where(ReviewCriteriaScore.set_id == criteria_set.id, Review.status == "done")
    )
    if (allowed := work_scope(principal, "review.view")) is not None:
        query = query.where(allowed)
    if business_project_id is not None:
        query = query.where(reviews_in_projects([business_project_id]))
    if project_id is not None:
        query = query.where(_in_repo(project_id))
    if user_id is not None:
        query = query.where(Review.author_user_id == user_id)
    tz = ZoneInfo(settings.report_tz)
    if date_from:
        query = query.where(Review.counted_at >= datetime.combine(date_from, time.min, tz))
    if date_to:
        end = datetime.combine(date_to + timedelta(days=1), time.min, tz)
        query = query.where(Review.counted_at < end)
    total = weight = 0.0
    loc = count = 0
    parts: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # sum, weight, findings
    names: dict[str, tuple[str, int]] = {}
    weeks: dict[date, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # sum, weight, count
    for loc_reviewed, counted_at, row_score, breakdown in session.execute(query).tuples():
        w = float(max(loc_reviewed or 0, 1))  # docs/specs/08: LOC-weighted
        value = float(row_score if row_score is not None else Decimal(0))
        total += value * w
        weight += w
        loc += loc_reviewed or 0
        count += 1
        for part in (breakdown or {}).get("components", []):
            key = str(part.get("key"))
            acc = parts[key]
            acc[0] += float(part.get("score") or 0) * w
            acc[1] += w
            acc[2] += len(part.get("findings") or [])
            names.setdefault(key, (str(part.get("name") or key), int(part.get("weight") or 0)))
        if counted_at is not None:
            day = counted_at.astimezone(tz).date()
            week = weeks[day - timedelta(days=day.weekday())]
            week[0] += value * w
            week[1] += w
            week[2] += 1
    order = [str(c["key"]) for c in (current.components if current else [])]
    for c in current.components if current else []:
        names[str(c["key"])] = (str(c["name"]), int(c["weight"]))
    # No data: no components either (the page shows its empty state).
    keys = (order + sorted(k for k in parts if k not in order)) if count else []
    return {
        "sets": sets,
        "set_id": criteria_set.id,
        "set_name": criteria_set.name,
        "version": current.version if current else None,
        "reviews": count,
        "loc": loc,
        "score": _avg(total, weight),
        "components": [
            {
                "key": key,
                "name": names.get(key, (key, 0))[0],
                "weight": names.get(key, (key, 0))[1],
                "current": key in order,
                "score": _avg(parts[key][0], parts[key][1]) if key in parts else None,
                "findings": int(parts[key][2]) if key in parts else 0,
            }
            for key in keys
        ],
        "weeks": [
            {"week_start": start, "score": _avg(acc[0], acc[1]), "reviews": int(acc[2])}
            for start, acc in sorted(weeks.items())
        ],
    }
