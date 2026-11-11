"""Review rules imported as files (docs/specs/17 §Tệp quy tắc, user 2026-11-11).

Next to the rules typed on the screen, a dự án keeps up to MAX_FILES imported files (.md,
.markdown, .txt, .doc, .docx). Each upload is one file in the request body (raw bytes, like
Stetho's): its text is converted to Markdown (app/assistant/importers.py) and kept, never the
file. A second upload with the same name replaces that file's text (a new version of the same
rules); several files are imported by uploading them one after another. Every file switched on
goes into every review prompt of the dự án as its own source, cited by name; the screen shows
how much of the rules budget (app/review/rules.MAX_RULES_CHARS) they use.

Same permission as the dự án's typed rules: `admin.projects`.
"""

import hashlib
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.audit import audit
from app.api.deps import get_session
from app.api.security import Principal, require
from app.assistant import importers
from app.models import BusinessProject, ReviewRuleFile
from app.review.rules import MAX_FILE_CHARS, MAX_RULES_CHARS

router = APIRouter(tags=["rule-files"])
SessionDep = Annotated[Session, Depends(get_session)]
ProjectsAdmin = Annotated[Principal, require("admin.projects")]

MAX_FILES = 20
MAX_BYTES = importers.MAX_FILE_BYTES
NAME_MAX = 200


class RuleFileOut(BaseModel):
    id: int
    name: str
    chars: int
    enabled: bool
    position: int
    clipped: bool  # longer than one file may put in a prompt: only its beginning is used
    updated_at: str | None
    created_at: str | None


class RuleFileDetail(RuleFileOut):
    body: str


class RuleFilesOut(BaseModel):
    files: list[RuleFileOut]
    typed_chars: int  # the rules typed on the screen
    files_chars: int  # files switched on, each counted up to MAX_FILE_CHARS
    budget: int  # MAX_RULES_CHARS, shared with the repo's own rules and rule files
    file_max_chars: int
    max_files: int
    max_mb: int
    kinds: list[str]


class RuleFilePatch(BaseModel):
    enabled: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    position: int | None = Field(default=None, ge=0, le=1000)


class UploadOut(BaseModel):
    file: RuleFileOut
    replaced: bool


def _bp(session: Session, bp_id: int) -> BusinessProject:
    bp = session.get(BusinessProject, bp_id)
    if bp is None:
        raise HTTPException(404, "dự án not found")
    return bp


def _file(session: Session, bp_id: int, file_id: int) -> ReviewRuleFile:
    row = session.get(ReviewRuleFile, file_id)
    if row is None or row.business_project_id != bp_id:
        raise HTTPException(404, "rule file not found")
    return row


def _out(row: ReviewRuleFile) -> RuleFileOut:
    return RuleFileOut(
        id=row.id,
        name=row.name,
        chars=row.chars,
        enabled=row.enabled,
        position=row.position,
        clipped=row.chars > MAX_FILE_CHARS,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


def clean_name(raw: str) -> str:
    """The base name of an uploaded file, without folders or control characters."""
    name = unquote(raw).replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    if not name:
        raise HTTPException(422, "tên file trống")
    return name[:NAME_MAX]


@router.get("/business-projects/{bp_id}/rule-files", response_model=RuleFilesOut)
def list_rule_files(bp_id: int, principal: ProjectsAdmin, session: SessionDep) -> RuleFilesOut:
    bp = _bp(session, bp_id)
    rows = session.scalars(
        select(ReviewRuleFile)
        .where(ReviewRuleFile.business_project_id == bp_id)
        .order_by(ReviewRuleFile.position, ReviewRuleFile.id)
    ).all()
    return RuleFilesOut(
        files=[_out(r) for r in rows],
        typed_chars=len(bp.review_rules or ""),
        files_chars=sum(min(r.chars, MAX_FILE_CHARS) for r in rows if r.enabled),
        budget=MAX_RULES_CHARS,
        file_max_chars=MAX_FILE_CHARS,
        max_files=MAX_FILES,
        max_mb=MAX_BYTES // (1024 * 1024),
        kinds=list(importers.RULE_FILE_KINDS),
    )


@router.get("/business-projects/{bp_id}/rule-files/{file_id}", response_model=RuleFileDetail)
def get_rule_file(
    bp_id: int, file_id: int, principal: ProjectsAdmin, session: SessionDep
) -> RuleFileDetail:
    row = _file(session, bp_id, file_id)
    return RuleFileDetail(**_out(row).model_dump(), body=row.body)


@router.post("/business-projects/{bp_id}/rule-files", response_model=UploadOut, status_code=201)
async def upload_rule_file(
    bp_id: int,
    request: Request,
    principal: ProjectsAdmin,
    session: SessionDep,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> UploadOut:
    """One file as the request body (raw bytes). Same name as a file already there: its text
    is replaced (the switch and the order stay)."""
    name = clean_name(filename)
    if not name.lower().endswith(importers.RULE_FILE_KINDS):
        raise HTTPException(422, "chỉ nhận " + ", ".join(importers.RULE_FILE_KINDS))
    await run_in_threadpool(session.commit)  # nothing written yet: frees the connection
    declared = request.headers.get("content-length")
    limit = MAX_BYTES
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"file lớn hơn {limit // (1024 * 1024)} MB")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, f"file lớn hơn {limit // (1024 * 1024)} MB")

    def store() -> UploadOut:
        _bp(session, bp_id)
        # One upload at a time per dự án: the file count and a new name are checked under it.
        session.execute(
            select(BusinessProject.id).where(BusinessProject.id == bp_id).with_for_update()
        )
        try:
            text = importers.from_file(name, bytes(data))
        except importers.ImportError_ as exc:
            raise HTTPException(422, f"Không đọc được {name}: {exc}") from None
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        row = session.scalars(
            select(ReviewRuleFile)
            .where(ReviewRuleFile.business_project_id == bp_id, ReviewRuleFile.name == name)
            .with_for_update()
        ).first()
        replaced = row is not None
        if row is None:
            count = session.scalar(
                select(func.count()).where(ReviewRuleFile.business_project_id == bp_id)
            )
            if int(count or 0) >= MAX_FILES:
                raise HTTPException(
                    409, f"mỗi dự án tối đa {MAX_FILES} tệp quy tắc: xoá bớt tệp không dùng"
                )
            last = session.scalar(
                select(func.max(ReviewRuleFile.position)).where(
                    ReviewRuleFile.business_project_id == bp_id
                )
            )
            row = ReviewRuleFile(
                business_project_id=bp_id,
                name=name,
                position=int(last if last is not None else -1) + 1,
                created_by=principal.user_id,
            )
            session.add(row)
        row.body = text
        row.chars = len(text)
        row.content_hash = digest
        row.updated_by = principal.user_id
        session.flush()
        audit(
            session,
            principal.user_id,
            "business_project.rule_file.replace" if replaced else "business_project.rule_file.add",
            f"business_project:{bp_id}",
            file=name,
            chars=len(text),
        )
        session.commit()
        session.refresh(row)
        return UploadOut(file=_out(row), replaced=replaced)

    return await run_in_threadpool(store)


@router.patch("/business-projects/{bp_id}/rule-files/{file_id}", response_model=RuleFileOut)
def update_rule_file(
    bp_id: int, file_id: int, body: RuleFilePatch, principal: ProjectsAdmin, session: SessionDep
) -> RuleFileOut:
    row = _file(session, bp_id, file_id)
    changes: dict[str, Any] = {}
    if body.enabled is not None and body.enabled != row.enabled:
        row.enabled = changes["enabled"] = body.enabled
    if body.position is not None and body.position != row.position:
        row.position = changes["position"] = body.position
    if body.name is not None:
        name = clean_name(body.name)
        if name != row.name:
            clash = session.scalar(
                select(ReviewRuleFile.id).where(
                    ReviewRuleFile.business_project_id == bp_id,
                    ReviewRuleFile.name == name,
                    ReviewRuleFile.id != row.id,
                )
            )
            if clash is not None:
                raise HTTPException(409, "đã có tệp quy tắc cùng tên")
            row.name = changes["name"] = name
    if changes:
        row.updated_by = principal.user_id
        audit(
            session,
            principal.user_id,
            "business_project.rule_file.update",
            f"business_project:{bp_id}",
            file_id=row.id,
            **changes,
        )
        session.commit()
        session.refresh(row)
    return _out(row)


@router.delete("/business-projects/{bp_id}/rule-files/{file_id}", status_code=204)
def delete_rule_file(
    bp_id: int, file_id: int, principal: ProjectsAdmin, session: SessionDep
) -> None:
    row = _file(session, bp_id, file_id)
    audit(
        session,
        principal.user_id,
        "business_project.rule_file.delete",
        f"business_project:{bp_id}",
        file=row.name,
    )
    session.delete(row)
    session.commit()
