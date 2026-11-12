"""AI on test cases (docs/specs/30 §6-§8.2): ask for cases written from sources, a review of a
suite, or the cases a merge request calls for; follow the run; add what it proposes.

Who: whoever may edit the dự án's test cases (`docs.edit`). Limits: RUNS_PER_DAY a person
(retries count), ACTIVE_PER_USER at once, and the month's LLM budget.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin_settings import monthly_budget_usd
from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.routers.doc_drafts import FileIn
from app.api.routers.project_docs import _iso
from app.api.routers.testcases import CaseFields, CaseOut, case_outs
from app.api.security import CurrentPrincipal, Principal
from app.assistant import importers
from app.config import Settings, get_settings
from app.docs import service as docs
from app.docs import synthesis
from app.models import AuditLog, MergeRequest, Project, TestAiRun, TestSuite, User
from app.testcases import ai, content
from app.testcases import service as tc
from app.workers.knowledge_jobs import month_spend_usd
from app.workers.queues import review_queue

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
QueueDep = Annotated[Queue, Depends(get_queue)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LIST_DAYS = 14


class RunIn(BaseModel):
    kind: Literal["generate", "audit", "impact"]
    business_project_id: int
    suite_id: int | None = None
    merge_request_id: int | None = None
    doc_ids: list[int] = Field(default_factory=list, max_length=ai.MAX_SOURCES)
    jira_keys: list[str] = Field(default_factory=list, max_length=ai.MAX_JIRA)
    urls: list[str] = Field(default_factory=list, max_length=ai.MAX_SOURCES)
    files: list[FileIn] = Field(default_factory=list, max_length=ai.MAX_SOURCES)
    instruction: str | None = Field(default=None, max_length=ai.INSTRUCTION_MAX)
    count: int = Field(default=ai.DEFAULT_COUNT, ge=1, le=ai.MAX_COUNT)


class RunOut(BaseModel):
    id: int
    kind: str
    business_project_id: int
    suite_id: int | None
    suite_name: str | None
    merge_request_id: int | None
    mr_label: str | None
    status: str
    running: bool
    error: str | None
    input: dict[str, Any]
    sources: list[dict[str, Any]] | None
    result: dict[str, Any] | None
    model: str | None
    cost_usd: float | None
    applied_count: int | None
    created_at: str | None
    finished_at: str | None


class RunList(BaseModel):
    runs: list[RunOut]
    per_day: int
    used_today: int
    max_sources: int
    max_count: int


class ApplyItem(CaseFields):
    index: int = Field(ge=0, le=ai.MAX_PROPOSED)
    # The proposal's section path edited ("A > B"; "" = the suite's root); its folders are made.
    section: str | None = Field(default=None, max_length=1_000)


class ApplyIn(BaseModel):
    items: list[ApplyItem] = Field(min_length=1, max_length=ai.MAX_PROPOSED)
    suite_id: int | None = None  # where they go (the run's suite by default)
    status: Literal["draft", "approved"] = "draft"


class ApplyOut(BaseModel):
    added: list[CaseOut]


def _out(session: Session, run: TestAiRun, now: datetime) -> RunOut:
    suite = session.get(TestSuite, run.suite_id) if run.suite_id else None
    mr = session.get(MergeRequest, run.merge_request_id) if run.merge_request_id else None
    data = dict(run.input or {})
    # The texts of pasted sources are not sent back: their names are enough.
    data["files"] = [
        {"name": f.get("name"), "kind": f.get("kind")} for f in data.get("files") or []
    ]
    return RunOut(
        id=run.id,
        kind=run.kind,
        business_project_id=run.business_project_id,
        suite_id=run.suite_id,
        suite_name=suite.name if suite else None,
        merge_request_id=run.merge_request_id,
        mr_label=f"!{mr.iid} {mr.title or ''}".strip() if mr else None,
        status=run.status,
        running=ai.running(run, now),
        error=run.error,
        input=data,
        sources=run.sources,
        result=run.result,
        model=run.model,
        cost_usd=run.cost_usd,
        applied_count=run.applied_count,
        created_at=_iso(run.created_at),
        finished_at=_iso(run.finished_at),
    )


def _run(session: Session, principal: Principal, run_id: int, *, lock: bool = False) -> TestAiRun:
    query = select(TestAiRun).where(TestAiRun.id == run_id)
    if lock:
        query = query.with_for_update()
    run = session.scalars(query).first()
    if run is None or not tc.may_view(principal, run.business_project_id):
        raise HTTPException(404, "không tìm thấy lượt AI")
    if not tc.may_edit(principal, run.business_project_id):
        raise HTTPException(403, "bạn không có quyền sửa testcase của dự án này")
    return run


def _used_today(session: Session, user_id: int, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.action == ai.REQUEST_ACTION,
                AuditLog.actor_user_id == user_id,
                AuditLog.at >= now - timedelta(days=1),
            )
        )
        or 0
    )


def _check_limits(
    session: Session, principal: Principal, settings: Settings, now: datetime
) -> None:
    session.get(User, principal.user_id, with_for_update=True)
    if _used_today(session, principal.user_id, now) >= ai.RUNS_PER_DAY:
        raise HTTPException(429, f"đã dùng {ai.RUNS_PER_DAY} lượt AI testcase trong 24 giờ")
    mine = session.scalars(
        select(TestAiRun).where(
            TestAiRun.created_by == principal.user_id, TestAiRun.status.in_(ai.ACTIVE)
        )
    ).all()
    if sum(1 for r in mine if ai.running(r, now)) >= ai.ACTIVE_PER_USER:
        raise HTTPException(
            429, f"đang có {ai.ACTIVE_PER_USER} lượt AI đang chạy; đợi xong rồi thử tiếp"
        )
    budget = monthly_budget_usd(session, settings)
    if budget > 0 and month_spend_usd(session) >= budget:
        raise HTTPException(409, "đã dùng hết ngân sách LLM của tháng")


def _enqueue(session: Session, queue: Queue, run: TestAiRun) -> None:
    try:
        review_queue(queue, asked=True).enqueue(
            "app.testcases.ai.ai_job",
            run.id,
            run.requested_at.isoformat(),
            job_id=f"test-ai-{run.id}-{int(run.requested_at.timestamp() * 1000)}",
            job_timeout=ai.JOB_TIMEOUT_S,
        )
    except Exception:
        run.status = "failed"
        run.error = "Không xếp hàng được; thử lại."
        run.finished_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(503, "could not queue the AI run") from None


@router.get("/testcases/ai", response_model=RunList)
def list_runs(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    kind: Literal["generate", "audit", "impact"] | None = None,
    suite_id: int | None = None,
) -> RunList:
    bp = tc.project_for(session, principal, business_project_id, edit=True)
    now = datetime.now(UTC)
    query = (
        select(TestAiRun)
        .where(
            TestAiRun.business_project_id == bp.id,
            TestAiRun.status.in_(("queued", "fetching", "writing", "ready", "failed"))
            | (
                (TestAiRun.status == "applied")
                & (TestAiRun.created_at >= now - timedelta(days=LIST_DAYS))
            ),
        )
        .order_by(TestAiRun.created_at.desc())
        .limit(30)
    )
    if kind:
        query = query.where(TestAiRun.kind == kind)
    if suite_id is not None:
        query = query.where(TestAiRun.suite_id == suite_id)
    return RunList(
        runs=[_out(session, r, now) for r in session.scalars(query)],
        per_day=ai.RUNS_PER_DAY,
        used_today=_used_today(session, principal.user_id, now),
        max_sources=ai.MAX_SOURCES,
        max_count=ai.MAX_COUNT,
    )


@router.post("/testcases/ai", response_model=RunOut, status_code=201)
def create_run(
    body: RunIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
) -> RunOut:
    bp = tc.project_for(session, principal, body.business_project_id, edit=True)
    data: dict[str, Any] = {"instruction": (body.instruction or "").strip() or None}
    suite_id = mr_id = None
    if body.kind in ("generate", "audit"):
        if body.suite_id is None:
            raise HTTPException(422, "chọn bộ testcase")
        suite = tc.suite_for(session, principal, body.suite_id, edit=True)
        if bp.id not in tc.projects_of(session, suite):
            raise HTTPException(422, "bộ testcase không thuộc dự án này")
        suite_id = suite.id
    if body.kind == "generate":
        keys = tc.jira_keys(body.jira_keys)
        try:
            urls = (
                synthesis.clean_urls(body.urls, files=len(body.files) + len(body.doc_ids) + 1)
                if body.urls
                else []
            )
        except synthesis.LinkError as exc:
            raise HTTPException(422, str(exc)) from None
        count = len(urls) + len(body.files) + len(body.doc_ids) + (1 if keys else 0)
        if count == 0:
            raise HTTPException(422, "chọn ít nhất một nguồn: tài liệu, Jira, link hoặc văn bản")
        if count > ai.MAX_SOURCES:
            raise HTTPException(422, f"tối đa {ai.MAX_SOURCES} nguồn mỗi lần (Jira tính là một)")
        for doc_id in body.doc_ids:
            doc = docs.document_for(session, principal, doc_id)
            if bp.id not in docs.projects_of(session, doc):
                raise HTTPException(422, "tài liệu không thuộc dự án này")
        kinds = (*importers.DOCUMENT_KINDS, synthesis.PASTE_KIND)
        if any(f.kind not in kinds for f in body.files):
            raise HTTPException(422, "loại nguồn không hợp lệ")
        data |= {
            "doc_ids": list(dict.fromkeys(body.doc_ids)),
            "jira_keys": keys,
            "urls": urls,
            "files": [f.model_dump() for f in body.files],
            "count": body.count,
        }
    if body.kind == "impact":
        mr = session.get(MergeRequest, body.merge_request_id) if body.merge_request_id else None
        repo = session.get(Project, mr.project_id) if mr and mr.project_id else None
        if mr is None or repo is None or repo.business_project_id != bp.id:
            raise HTTPException(422, "merge request không thuộc dự án này")
        if repo.allow_external_llm is False:
            raise HTTPException(409, "repo không cho phép gửi dữ liệu ra LLM ngoài")
        mr_id = mr.id
    now = datetime.now(UTC)
    _check_limits(session, principal, settings, now)
    run = TestAiRun(
        kind=body.kind,
        business_project_id=bp.id,
        suite_id=suite_id,
        merge_request_id=mr_id,
        input=data,
        status="queued",
        created_by=principal.user_id,
        created_at=now,
        requested_at=now,
        requested_by=principal.user_id,
    )
    session.add(run)
    session.flush()
    audit(
        session,
        principal.user_id,
        ai.REQUEST_ACTION,
        f"test_ai:{run.id}",
        kind=body.kind,
        business_project=bp.id,
        suite=suite_id,
        merge_request=mr_id,
    )
    session.commit()
    _enqueue(session, queue, run)
    return _out(session, run, now)


@router.get("/testcases/ai/{run_id}", response_model=RunOut)
def get_run(run_id: int, principal: CurrentPrincipal, session: SessionDep) -> RunOut:
    return _out(session, _run(session, principal, run_id), datetime.now(UTC))


@router.post("/testcases/ai/{run_id}/retry", response_model=RunOut)
def retry_run(
    run_id: int,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    queue: QueueDep,
) -> RunOut:
    run = _run(session, principal, run_id, lock=True)
    now = datetime.now(UTC)
    if ai.running(run, now):
        raise HTTPException(409, "lượt AI đang chạy")
    if run.status in ("applied", "discarded"):
        raise HTTPException(409, "lượt AI đã dùng hoặc đã bỏ: tạo yêu cầu mới")
    _check_limits(session, principal, settings, now)
    run.status = "queued"
    run.error = run.model = None
    run.result = None
    run.sources = None
    run.cost_usd = None
    run.finished_at = run.started_at = None
    run.requested_at = now
    run.requested_by = principal.user_id
    audit(
        session,
        principal.user_id,
        ai.REQUEST_ACTION,
        f"test_ai:{run.id}",
        kind=run.kind,
        retry=True,
    )
    session.commit()
    _enqueue(session, queue, run)
    return _out(session, run, now)


@router.post("/testcases/ai/{run_id}/apply", response_model=ApplyOut)
def apply_run(
    run_id: int, body: ApplyIn, principal: CurrentPrincipal, session: SessionDep
) -> ApplyOut:
    """Add the chosen proposals (edited on the screen if need be) to a suite."""
    run = _run(session, principal, run_id, lock=True)
    if run.status not in ("ready", "applied") or not run.result:
        raise HTTPException(409, "lượt AI chưa có kết quả")
    proposals = (
        run.result.get("missing") if run.kind == "impact" else run.result.get("cases")
    ) or []
    suite_id = body.suite_id or run.suite_id
    if suite_id is None:
        raise HTTPException(422, "chọn bộ testcase nhận testcase mới")
    suite = tc.suite_for(session, principal, suite_id, edit=True)
    if run.business_project_id not in tc.projects_of(session, suite):
        raise HTTPException(422, "bộ testcase không thuộc dự án này")
    session.refresh(suite, with_for_update=True)  # one apply (and its folders) at a time
    folders: dict[tuple[int | None, str], int] = {}
    applied = set(run.result.get("applied") or [])
    added = []
    position = tc.next_position(session, suite.id)
    for item in body.items:
        if item.index >= len(proposals) or item.index in applied:
            continue
        base = dict(proposals[item.index])
        base.update(item.model_dump(exclude={"index"}, exclude_none=True))
        base["status"] = body.status
        try:
            values = tc.clean(base)
            values.pop("code", None)
            if "section_id" not in values:
                values["section_id"] = content.section_for_path(
                    session, suite.id, str(base.get("section") or ""), folders
                )
            tc.check_refs(session, suite, values)
        except tc.FieldError as exc:
            raise HTTPException(422, f"đề xuất {item.index + 1}: {exc}") from None
        case = tc.create(
            session,
            suite,
            values,
            user_id=principal.user_id,
            source="ai",
            position=position,
        )
        position += 1
        applied.add(item.index)
        added.append(case)
    if not added:
        raise HTTPException(409, "không có đề xuất nào mới để thêm")
    run.result = {**run.result, "applied": sorted(applied)}
    run.applied_count = len(applied)
    run.status = "applied"
    audit(
        session,
        principal.user_id,
        "testcase.ai.apply",
        f"test_ai:{run.id}",
        suite=suite.id,
        added=len(added),
    )
    session.commit()
    return ApplyOut(added=case_outs(session, added))


@router.delete("/testcases/ai/{run_id}", status_code=204)
def discard_run(run_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    run = _run(session, principal, run_id, lock=True)
    run.status = "discarded"
    run.finished_at = run.finished_at or datetime.now(UTC)
    input_ = dict(run.input or {})
    input_.pop("files", None)  # pasted texts are not kept once dropped
    run.input = input_
    session.commit()
