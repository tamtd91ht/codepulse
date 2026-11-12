"""Jira bugs from test results and the dự án's test settings (docs/specs/30a G3, 30 §15).

- POST /testcases/tests/{id}/bug: a Jira issue from a failed result (steps, expected vs actual,
  environment, link back); its key goes into the result's defects. Needs Jira writes switched on
  (JIRA_WRITE_ENABLED or Admin > Jira > Kết nối) and a Jira project in the dự án's settings.
- POST /testcases/tests/{id}/bug/link: an existing issue as a defect.
- GET /testcases/bugs/status?business_project_id=: open or closed, from the dự án's synced
  issues, else Jira (cached, capped, rate-limited; only keys in the dự án's defects).
- GET/PUT /testcases/project-settings: the dự án's TestProjectSettings.

Who: recording a result (`statuses.may_record`, so "chỉ người được giao" holds) creates and
links bugs; `tests.view` on the dự án reads states and settings; `tests.manage` changes the
settings. One bug is created per failed result (409 after that).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import security
from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.locks import busy_as_409, short_lock_wait
from app.api.routers.test_runs import BUSY, CLOSED_RUN, TestDetail, _detail, _test_for
from app.api.security import CurrentPrincipal, Principal
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.jira.client import JiraClient, JiraError
from app.jira.settings import JiraConnection, connection
from app.models import AuditLog, TestProjectSettings, TestResult, TestRun, TestRunTest
from app.testcases import bugs, content
from app.testcases import runs as rs
from app.testcases import service as tc
from app.testcases import statuses as tst

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
BoxDep = Annotated[SecretBox, Depends(get_secret_box)]

WRITE_OFF = (
    "chưa bật tạo bug lên Jira: bật 'Cho phép tạo bug' ở Admin > Jira > Kết nối (hoặc biến "
    "môi trường JIRA_WRITE_ENABLED=true); token Jira cần quyền tạo issue"
)
NOT_CONFIGURED = "chưa cấu hình kết nối Jira (Admin > Jira > Kết nối) hoặc tích hợp Jira đang tắt"
NO_PROJECT = (
    "dự án chưa chọn project Jira nhận bug: vào Testcase > Thiết lập > Cài đặt dự án để chọn"
)
NO_FAILED = "test chưa có kết quả Failed để tạo bug (ghi kết quả Failed trước)"
NO_RESULT = "kết quả không thuộc test này"
NOT_FAILED = "chỉ tạo bug từ kết quả Failed: kết quả được chọn không phải Failed"
ALREADY = "đã tạo bug {key} từ kết quả này; gắn thêm bug có sẵn nếu cần"
# Bug states: live Jira reads per call, and calls that need Jira per person per minute (past it
# the keys not synced or cached answer "unknown" until the minute ends).
LIVE_READS_MAX = 10
LIVE_CALLS_PER_MINUTE = 20


class BugApi(Protocol):
    def create_issue(self, fields: dict[str, Any]) -> dict[str, Any]: ...

    def get_issue(self, key: str, fields: Any = ...) -> dict[str, Any]: ...

    def browse_url(self, key: str) -> str: ...

    def close(self) -> None: ...


BugFactory = Callable[[JiraConnection], BugApi]


def get_bug_jira_factory() -> BugFactory:
    return JiraClient.from_connection


FactoryDep = Annotated[BugFactory, Depends(get_bug_jira_factory)]


# -- shapes ----------------------------------------------------------------------------------------


class BugIn(BaseModel):
    summary: str | None = Field(default=None, max_length=bugs.SUMMARY_MAX)
    description: str | None = Field(default=None, max_length=10_000)
    issue_type: str | None = Field(default=None, max_length=100)
    components: list[str] | None = Field(default=None, max_length=20)
    labels: list[str] | None = Field(default=None, max_length=20)
    assignee: str | None = Field(default=None, max_length=200)  # Jira username
    result_id: int | None = None


class LinkIn(BaseModel):
    key: str = Field(min_length=3, max_length=30)
    result_id: int | None = None


class BugOut(BaseModel):
    key: str
    web_url: str | None
    verified: bool = True
    test: TestDetail


class BugState(BaseModel):
    key: str
    state: str  # opened | closed | unknown
    status: str | None = None  # Jira's status name (live reads only)
    title: str | None = None
    web_url: str | None = None
    source: str | None = None  # sync | jira | None


class ProjectSettingsOut(BaseModel):
    business_project_id: int
    assigned_only: bool
    require_approval: bool
    jira_project_key: str | None
    jira_issue_type: str
    jira_components: list[str]
    jira_labels: list[str]
    jira_configured: bool
    jira_write_enabled: bool
    jira_base_url: str | None
    can_edit: bool


class ProjectSettingsIn(BaseModel):
    business_project_id: int
    assigned_only: bool | None = None
    require_approval: bool | None = None
    jira_project_key: str | None = Field(default=None, max_length=20)
    jira_issue_type: str | None = Field(default=None, max_length=100)
    jira_components: list[str] | None = Field(default=None, max_length=20)
    jira_labels: list[str] | None = Field(default=None, max_length=20)


# -- helpers ---------------------------------------------------------------------------------------


def _recorder(session: Session, principal: Principal, test: TestRunTest, run: TestRun) -> None:
    if run.completed_at is not None:
        raise HTTPException(409, CLOSED_RUN)
    if not tst.may_record(
        session, principal, run.business_project_id, [test.assigned_to, run.assigned_to]
    ):
        raise HTTPException(
            403, "chỉ người được giao test hoặc người ghi được kết quả mới tạo / gắn bug"
        )


def _jira_call[T](conn: JiraConnection, factory: BugFactory, work: Callable[[BugApi], T]) -> T:
    try:
        client = factory(conn)
    except Exception as exc:  # e.g. a CA ssl refuses; never anything of the token
        raise HTTPException(502, f"không gọi được Jira: {exc.__class__.__name__}") from None
    try:
        return work(client)
    except JiraError as exc:
        raise HTTPException(502, f"Jira báo lỗi: {exc.message}") from None
    finally:
        client.close()


def _settings_out(
    principal: Principal, row: TestProjectSettings, conn: JiraConnection
) -> ProjectSettingsOut:
    return ProjectSettingsOut(
        business_project_id=row.business_project_id,
        assigned_only=bool(row.assigned_only),
        require_approval=bool(row.require_approval),
        jira_project_key=row.jira_project_key,
        jira_issue_type=row.jira_issue_type or "Bug",
        jira_components=list(row.jira_components or []),
        jira_labels=list(row.jira_labels or []),
        jira_configured=conn.configured,
        jira_write_enabled=conn.write_enabled,
        jira_base_url=conn.base_url or None,
        can_edit=tc.may_manage(principal, row.business_project_id),
    )


# -- bugs ------------------------------------------------------------------------------------------


@router.post("/testcases/tests/{test_id:int}/bug", response_model=BugOut, status_code=201)
@busy_as_409(BUSY)
def create_bug(
    test_id: int,
    body: BugIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    factory: FactoryDep,
) -> BugOut:
    test, run = _test_for(session, principal, test_id)
    _recorder(session, principal, test, run)
    conn = connection(session, settings, box)
    if not conn.write_enabled:
        raise HTTPException(409, WRITE_OFF)
    if not conn.configured:
        raise HTTPException(409, NOT_CONFIGURED)
    prefs = bugs.project_settings(session, run.business_project_id)
    if not prefs.jira_project_key:
        raise HTTPException(422, NO_PROJECT)
    # One bug per failed result: the test and the result stay locked while Jira makes the
    # issue, so a second click (or a second person) waits, then sees the bug (409), or gives
    # up waiting (409 BUSY) - never a duplicate issue on Jira.
    short_lock_wait(session)
    test, run = _test_for(session, principal, test_id, lock=True)
    _recorder(session, principal, test, run)
    if body.result_id is not None:
        result = bugs.target_result(session, test, body.result_id, failed_only=False)
        if result is None:
            raise HTTPException(422, NO_RESULT)
        if result.status != "failed":
            raise HTTPException(422, NOT_FAILED)
    else:
        result = bugs.target_result(session, test, None, failed_only=True)
        if result is None:
            raise HTTPException(422, NO_FAILED)
    result = (
        session.get(TestResult, result.id, with_for_update=True, populate_existing=True) or result
    )
    made_before = session.scalar(
        select(AuditLog)
        .where(AuditLog.action == "testresult.bug", AuditLog.target == f"test_result:{result.id}")
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    if made_before is not None:
        raise HTTPException(409, ALREADY.format(key=(made_before.details or {}).get("key") or ""))
    shown = rs.views(session, run, [test])[test.id]
    case_key = shown.get("case_key") or content.key("case", test.case_id)
    fields: dict[str, Any] = {
        "project": {"key": prefs.jira_project_key},
        "summary": rs.line(body.summary, bugs.SUMMARY_MAX)
        or bugs.summary(case_key, str(shown.get("title") or test.title)),
        "issuetype": {"name": rs.line(body.issue_type, 100) or prefs.jira_issue_type or "Bug"},
        "description": bugs.description(
            shown,
            result,
            run,
            test,
            dashboard_url=settings.dashboard_url,
            extra=rs.text(body.description),
        ),
    }
    components = bugs.clean_list(
        body.components if body.components is not None else prefs.jira_components or []
    )
    labels = bugs.clean_list(
        body.labels if body.labels is not None else prefs.jira_labels or [], labels=True
    )
    if components:
        fields["components"] = [{"name": c} for c in components]
    if labels:
        fields["labels"] = labels
    if body.assignee and body.assignee.strip():
        fields["assignee"] = {"name": rs.line(body.assignee, 200)}

    def work(api: BugApi) -> tuple[str, str]:
        made = api.create_issue(fields)
        key = str(made.get("key") or "")
        if not bugs.ISSUE_KEY.match(key):
            raise JiraError(None, "Jira không trả về mã issue")
        return key, api.browse_url(key)

    key, url = _jira_call(conn, factory, work)
    bugs.remember(key, {"state": "opened", "title": fields["summary"], "web_url": url})
    bugs.add_defect(result, key)  # still locked since before the Jira call
    audit(
        session,
        principal.user_id,
        "testresult.bug",
        f"test_result:{result.id}",
        key=key,
        test_id=test.id,
        run_id=run.id,
    )
    session.commit()
    return BugOut(key=key, web_url=url, test=_detail(session, principal, test, run))


@router.post("/testcases/tests/{test_id:int}/bug/link", response_model=BugOut)
@busy_as_409(BUSY)
def link_bug(
    test_id: int,
    body: LinkIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    factory: FactoryDep,
) -> BugOut:
    key = body.key.strip().upper()
    if not bugs.ISSUE_KEY.match(key):
        raise HTTPException(422, "mã Jira không đúng dạng (ví dụ ABC-123)")
    test, run = _test_for(session, principal, test_id)
    _recorder(session, principal, test, run)
    if (
        body.result_id is not None
        and bugs.target_result(session, test, body.result_id, failed_only=False) is None
    ):
        raise HTTPException(422, NO_RESULT)
    synced = bugs.synced_states(session, [key])
    url = synced[key]["web_url"] if key in synced else None
    verified = key in synced
    conn = connection(session, settings, box)
    if not verified and conn.configured:
        try:
            client = factory(conn)
        except Exception:  # a broken CA: link without checking
            client = None
        if client is not None:
            try:
                url = client.browse_url(key)
                bugs.remember(key, bugs.from_jira(client.get_issue(key), url))
                verified = True
            except JiraError as exc:
                if exc.status_code == 404:
                    raise HTTPException(422, f"không tìm thấy issue {key} trên Jira") from None
            finally:
                client.close()
    short_lock_wait(session)
    test, run = _test_for(session, principal, test_id, lock=True)
    _recorder(session, principal, test, run)
    result = bugs.target_result(session, test, body.result_id, failed_only=False)
    if result is None:
        result = rs.add_result(
            session, test, user_id=principal.user_id, now=datetime.now(UTC), defects=[key]
        )
        session.flush()
    else:
        result = (
            session.get(TestResult, result.id, with_for_update=True, populate_existing=True)
            or result
        )
        bugs.add_defect(result, key)
    audit(
        session,
        principal.user_id,
        "testresult.bug_link",
        f"test_result:{result.id}",
        key=key,
        test_id=test.id,
        verified=verified,
    )
    session.commit()
    return BugOut(
        key=key, web_url=url, verified=verified, test=_detail(session, principal, test, run)
    )


@router.get("/testcases/bugs/status", response_model=list[BugState])
def bug_status(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    factory: FactoryDep,
    business_project_id: int,
    keys: Annotated[str, Query(max_length=3_000)],
) -> list[BugState]:
    """`keys`: comma-separated Jira keys (at most 100), of the dự án `business_project_id`
    (tests.view there). Synced issues of the dự án answer first; a key written in the defects
    of one of its results is read from the cache, else from Jira (at most LIVE_READS_MAX per
    call, LIVE_CALLS_PER_MINUTE such calls per person, each kept 5 minutes). Any other key
    stays "unknown": this never reads an issue the dự án has nothing to do with."""
    bp = tc.project_for(session, principal, business_project_id)
    wanted = [k for k in dict.fromkeys(k.strip().upper() for k in keys.split(",")) if k][
        : bugs.STATUS_KEYS_MAX
    ]
    wanted = [k for k in wanted if bugs.ISSUE_KEY.match(k)]
    found: dict[str, dict[str, Any]] = bugs.synced_states(session, wanted, [bp.id])
    ours = bugs.project_defect_keys(session, bp.id, [k for k in wanted if k not in found])
    missing = []
    for k in wanted:
        if k in found or k not in ours:
            continue
        hit = bugs.cached(k)
        if hit is not None:
            found[k] = hit
        else:
            missing.append(k)
    missing = missing[:LIVE_READS_MAX]
    if (
        missing
        and security.token_rate.hit(f"cp:jira:bugstatus:{principal.user_id}", LIVE_CALLS_PER_MINUTE)
        is not None
    ):
        missing = []
    conn = connection(session, settings, box) if missing else None
    session.commit()  # nothing to write: end the transaction before waiting on Jira
    if missing and conn is not None and conn.configured:
        try:
            client = factory(conn)
        except Exception:
            client = None
        if client is not None:
            try:
                for k in missing:
                    url = client.browse_url(k)
                    try:
                        state = bugs.from_jira(client.get_issue(k), url)
                    except JiraError as exc:
                        if exc.status_code != 404:
                            break  # Jira is down or refuses: the rest stay unknown
                        state = {"state": "unknown", "web_url": None, "source": "jira"}
                    bugs.remember(k, state)
                    found[k] = state
            finally:
                client.close()
    return [BugState(key=k, **{"state": "unknown", **found.get(k, {})}) for k in wanted]


# -- project settings ------------------------------------------------------------------------------


@router.get("/testcases/project-settings", response_model=ProjectSettingsOut)
def get_project_settings(
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
    business_project_id: int,
) -> ProjectSettingsOut:
    bp = tc.project_for(session, principal, business_project_id)
    conn = connection(session, settings, box)
    return _settings_out(principal, bugs.project_settings(session, bp.id), conn)


@router.put("/testcases/project-settings", response_model=ProjectSettingsOut)
def put_project_settings(
    body: ProjectSettingsIn,
    principal: CurrentPrincipal,
    session: SessionDep,
    settings: SettingsDep,
    box: BoxDep,
) -> ProjectSettingsOut:
    bp = tc.project_for(session, principal, body.business_project_id, manage=True)
    row = session.get(TestProjectSettings, bp.id)
    if row is None:
        row = bugs.project_settings(session, bp.id)
        session.add(row)
    given = body.model_fields_set
    if body.assigned_only is not None:
        row.assigned_only = body.assigned_only
    if body.require_approval is not None:
        row.require_approval = body.require_approval
    if "jira_project_key" in given:
        key = (body.jira_project_key or "").strip().upper()
        if key and not bugs.PROJECT_KEY.match(key):
            raise HTTPException(422, "mã project Jira không đúng dạng (ví dụ OMX)")
        row.jira_project_key = key or None
    if "jira_issue_type" in given:
        row.jira_issue_type = rs.line(body.jira_issue_type, 100) or "Bug"
    if body.jira_components is not None:
        row.jira_components = bugs.clean_list(body.jira_components)
    if body.jira_labels is not None:
        row.jira_labels = bugs.clean_list(body.jira_labels, labels=True)
    row.updated_by = principal.user_id
    session.flush()
    audit(
        session,
        principal.user_id,
        "testsettings.update",
        f"business_project:{bp.id}",
        changed=sorted(given - {"business_project_id"}),
    )
    session.commit()
    return _settings_out(principal, row, connection(session, settings, box))
