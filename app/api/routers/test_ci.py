"""CI results (docs/specs/30a G1, docs/specs/30 §15.4): a pipeline uploads its test report; it is
parsed in the background into a run's results (app/testcases/ci.py) while the caller polls the
import's progress.

Who: `tests.run` records results into an existing run (`run_id`); without one a run is made,
which is `tests.manage`; `auto_create` makes cases, which is also `tests.edit`; `close_run` is
`tests.manage` (and a token's `testcases:write` scope). One import at a time per run: a second
upload into a run whose import is still queued or running is refused (409). An import queued or
running for longer than STALE_AFTER is marked failed when the dự án's imports are read or a new
one is sent.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from rq import Queue
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_queue, get_session
from app.api.routers.project_docs import Person, _iso, _people
from app.api.security import CurrentPrincipal, Principal
from app.models import TestCiImport, TestMilestone, TestRun, TestSuite
from app.testcases import ci, content
from app.testcases import runs as rs
from app.testcases import service as tc

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
QueueDep = Annotated[Queue, Depends(get_queue)]
ACTIVE_PER_PROJECT = 5  # imports queued or running at once in a dự án
ACTIVE = ("queued", "running")
# Past this an import still queued / running has lost its job (twice the job's timeout).
STALE_AFTER = timedelta(seconds=2 * ci.JOB_TIMEOUT_S)
STALE_ERROR = "quá thời gian xử lý; gửi lại báo cáo"
LIST_LIMIT = 20
TEXT_MAX = 200


class ImportStarted(BaseModel):
    import_id: int
    status: str


class ImportOut(BaseModel):
    id: int
    business_project_id: int
    suite_id: int | None
    run_id: int | None
    run_key: str | None
    status: str
    format: str | None
    file_name: str | None
    total: int
    done: int
    mapped: int
    created_cases: int
    skipped: int
    unmapped: list[str]
    error: str | None
    commit_sha: str | None
    branch: str | None
    mr_iid: int | None
    close_run: bool
    auto_create: bool
    created_by: Person | None
    created_at: str | None
    finished_at: str | None


def _outs(session: Session, rows: list[TestCiImport]) -> list[ImportOut]:
    people = _people(session, {r.created_by for r in rows})
    out = []
    for r in rows:
        p = r.params or {}
        out.append(
            ImportOut(
                id=r.id,
                business_project_id=r.business_project_id,
                suite_id=r.suite_id,
                run_id=r.run_id,
                run_key=content.key("run", r.run_id),
                status=r.status,
                format=r.format,
                file_name=r.file_name,
                total=r.total,
                done=r.done,
                mapped=r.mapped,
                created_cases=r.created_cases,
                skipped=int((p.get("summary") or {}).get("skipped") or 0),
                unmapped=list(r.unmapped or []),
                error=r.error,
                commit_sha=p.get("commit_sha"),
                branch=p.get("branch"),
                mr_iid=p.get("mr_iid"),
                close_run=bool(p.get("close_run")),
                auto_create=bool(p.get("auto_create")),
                created_by=people.get(r.created_by) if r.created_by else None,
                created_at=_iso(r.created_at),
                finished_at=_iso(r.finished_at),
            )
        )
    return out


def _line(value: str | None) -> str | None:
    return rs.line(value, TEXT_MAX) or None


def _ids(raw: str | None) -> list[int]:
    """ "1,2" or "1 2" -> [1, 2]."""
    out: list[int] = []
    for part in (raw or "").replace(",", " ").split():
        if not part.isdigit():
            raise HTTPException(422, "config_ids phải là các số, cách nhau bởi dấu phẩy")
        out.append(int(part))
    return out[:20]


def _stale_before(now: datetime) -> datetime:
    return now - STALE_AFTER


def _expire_stale(session: Session, bp_id: int, now: datetime) -> None:
    """Imports queued or running for longer than STALE_AFTER: their job died (worker restarted,
    killed past its timeout); marked failed so they neither count nor block the run."""
    session.execute(
        update(TestCiImport)
        .where(
            TestCiImport.business_project_id == bp_id,
            TestCiImport.status.in_(ACTIVE),
            TestCiImport.created_at < _stale_before(now),
        )
        .values(status="failed", error=STALE_ERROR, finished_at=now, data=None)
        .execution_options(synchronize_session=False)
    )


def _check_run(
    session: Session, principal: Principal, bp_id: int, run_id: int, now: datetime
) -> None:
    # FOR UPDATE: two uploads into one run at once take turns, the second sees the first's
    # import below and is refused.
    run = session.scalar(select(TestRun).where(TestRun.id == run_id).with_for_update())
    if run is None or run.business_project_id != bp_id:
        raise HTTPException(404, "test run không tồn tại")
    if run.completed_at is not None:
        raise HTTPException(409, "test run đã đóng: không ghi thêm kết quả được")
    if not (tc.may_run(principal, bp_id) or tc.may_manage(principal, bp_id)):
        raise HTTPException(403, "bạn không có quyền ghi kết quả test của dự án này")
    busy = session.scalar(
        select(TestCiImport.id)
        .where(
            TestCiImport.run_id == run_id,
            TestCiImport.status.in_(ACTIVE),
            TestCiImport.created_at >= _stale_before(now),
        )
        .limit(1)
    )
    if busy is not None:
        raise HTTPException(
            409,
            f"test run này đang có lượt nhập #{busy} chưa xong; chờ lượt đó xong rồi gửi lại",
        )


def _check_close(principal: Principal, bp_id: int) -> None:
    """Closing the run is managing it (as POST /runs/{id}/close): tests.manage, and for a
    token the testcases:write scope (results:write records results only)."""
    if not tc.may_manage(principal, bp_id):
        raise HTTPException(403, "đóng test run cần quyền quản lý test run của dự án này")
    if principal.token_scopes is not None and "testcases:write" not in principal.token_scopes:
        raise HTTPException(403, "đóng test run qua token cần scope testcases:write")


@router.post("/testcases/runs/import-results", response_model=ImportStarted, status_code=202)
def import_results(
    principal: CurrentPrincipal,
    session: SessionDep,
    queue: QueueDep,
    file: Annotated[UploadFile, File()],
    business_project_id: Annotated[int, Form()],
    suite_id: Annotated[int | None, Form()] = None,
    run_name: Annotated[str | None, Form(max_length=TEXT_MAX)] = None,
    run_id: Annotated[int | None, Form()] = None,
    milestone_id: Annotated[int | None, Form()] = None,
    config_ids: Annotated[str | None, Form(max_length=TEXT_MAX)] = None,
    close_run: Annotated[bool, Form()] = False,
    mr_iid: Annotated[int | None, Form(ge=1)] = None,
    commit_sha: Annotated[str | None, Form(max_length=64)] = None,
    branch: Annotated[str | None, Form(max_length=TEXT_MAX)] = None,
    project: Annotated[str | None, Form(max_length=TEXT_MAX)] = None,
    auto_create: Annotated[bool, Form()] = False,
) -> ImportStarted:
    """Queue a CI report (JUnit / TestNG / xUnit XML, Playwright / Cucumber JSON, up to 20 MB)
    to become the results of a run: `run_id` (an open run of the dự án) or a new run."""
    bp = tc.project_for(session, principal, business_project_id)
    now = datetime.now(UTC)
    _expire_stale(session, bp.id, now)
    if run_id is not None:
        _check_run(session, principal, bp.id, run_id, now)
    elif not tc.may_manage(principal, bp.id):
        raise HTTPException(
            403, "tạo test run cần quyền quản lý test run; gửi kèm run_id của một run đang mở"
        )
    if close_run:
        _check_close(principal, bp.id)
    if suite_id is not None:
        found = session.scalar(
            select(TestSuite.id).where(TestSuite.id == suite_id, tc.suite_in([bp.id]))
        )
        if found is None:
            raise HTTPException(404, "bộ testcase không tồn tại")
    if auto_create:
        if suite_id is None:
            raise HTTPException(422, "tự tạo testcase cần chọn bộ testcase (suite_id)")
        if not tc.may_edit(principal, bp.id):
            raise HTTPException(403, "tự tạo testcase cần quyền sửa testcase của dự án này")
    if milestone_id is not None and run_id is None:
        m = session.get(TestMilestone, milestone_id)
        if m is None or m.business_project_id != bp.id:
            raise HTTPException(422, "milestone không tồn tại")
    configs = _ids(config_ids)
    if configs and run_id is None and len(rs.combinations(session, bp.id, configs)) != 1:
        raise HTTPException(422, "mỗi nhóm cấu hình chỉ chọn một cấu hình cho một run CI")
    data = file.file.read(ci.MAX_FILE_BYTES + 1)
    if len(data) > ci.MAX_FILE_BYTES:
        raise HTTPException(413, f"file lớn hơn {ci.MAX_FILE_BYTES // (1024 * 1024)} MB")
    if not data.strip():
        raise HTTPException(422, "file rỗng")
    try:
        fmt = ci.detect(data)
    except ci.ReportError as exc:
        raise HTTPException(422, str(exc)) from None
    active = session.scalar(
        select(func.count()).where(
            TestCiImport.business_project_id == bp.id,
            TestCiImport.status.in_(ACTIVE),
            TestCiImport.created_at >= _stale_before(now),
        )
    )
    if int(active or 0) >= ACTIVE_PER_PROJECT:
        raise HTTPException(429, f"dự án đang có {ACTIVE_PER_PROJECT} lượt nhập chưa xong")
    params: dict[str, Any] = {
        "run_name": _line(run_name),
        "milestone_id": milestone_id if run_id is None else None,
        "config_ids": configs if run_id is None else [],
        "close_run": close_run,
        "mr_iid": mr_iid,
        "commit_sha": _line(commit_sha),
        "branch": _line(branch),
        "project": _line(project),
        "auto_create": auto_create,
    }
    name = (file.filename or "report").replace("\\", "/").rsplit("/", 1)[-1]
    imp = TestCiImport(
        business_project_id=bp.id,
        suite_id=suite_id,
        run_id=run_id,
        status="queued",
        format=fmt,
        file_name=rs.line(name, TEXT_MAX) or "report",
        params={k: v for k, v in params.items() if v not in (None, [])},
        data=data,
        created_by=principal.user_id,
    )
    session.add(imp)
    session.flush()
    audit(
        session,
        principal.user_id,
        "testcase.ci_import",
        f"ci_import:{imp.id}",
        business_project_id=bp.id,
        format=fmt,
        bytes=len(data),
    )
    session.commit()
    try:
        queue.enqueue(
            "app.testcases.ci.import_job",
            imp.id,
            job_id=f"test-ci-import-{imp.id}",
            job_timeout=ci.JOB_TIMEOUT_S,
        )
    except Exception:
        imp.status = "failed"
        imp.error = "Không xếp hàng được; thử lại."
        imp.data = None
        imp.finished_at = datetime.now(UTC)
        session.commit()
        raise HTTPException(503, "could not queue the import") from None
    return ImportStarted(import_id=imp.id, status=imp.status)


@router.get("/testcases/ci-imports/{import_id:int}", response_model=ImportOut)
def get_import(import_id: int, principal: CurrentPrincipal, session: SessionDep) -> ImportOut:
    imp = session.get(TestCiImport, import_id)
    if imp is None or not tc.may_view(principal, imp.business_project_id):
        raise HTTPException(404, "lượt nhập không tồn tại")
    now = datetime.now(UTC)
    if imp.status in ACTIVE and imp.created_at < _stale_before(now):
        imp.status, imp.error, imp.finished_at, imp.data = "failed", STALE_ERROR, now, None
        session.commit()
    return _outs(session, [imp])[0]


@router.get("/testcases/ci-imports", response_model=list[ImportOut])
def list_imports(
    principal: CurrentPrincipal, session: SessionDep, business_project_id: int
) -> list[ImportOut]:
    bp = tc.project_for(session, principal, business_project_id)
    _expire_stale(session, bp.id, datetime.now(UTC))
    session.commit()
    rows = list(
        session.scalars(
            select(TestCiImport)
            .where(TestCiImport.business_project_id == bp.id)
            .order_by(TestCiImport.created_at.desc(), TestCiImport.id.desc())
            .limit(LIST_LIMIT)
        )
    )
    return _outs(session, rows)
