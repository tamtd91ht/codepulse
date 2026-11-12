"""BDD import / export (.feature) and exploratory sessions (docs/specs/30 §15.12, §15.16, G8).

- `POST /testcases/bdd/check`: the editor's live check of a Gherkin text.
- `POST /testcases/import/feature`: a .feature file into a suite: its Feature is a section, each
  Scenario / Scenario Outline a BDD case (a `@C<id>` tag of a case of the suite updates it).
- `GET /testcases/export/feature`: one .feature per section holding BDD cases (a .zip when more
  than one), each scenario tagged `@C<id>`; importing it back into the suite changes nothing.
- `POST /testcases/tests/{id}/session` and `/session/notes`: the timed session of an exploratory
  test (whoever may record the test's result).
"""

import io
import zipfile
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_session
from app.api.locks import busy_as_409, short_lock_wait
from app.api.security import CurrentPrincipal, Principal
from app.models import TestCase, TestRun, TestRunTest, TestSection, TestSuite, User
from app.testcases import approval, content, exploratory, gherkin
from app.testcases import fields as tf
from app.testcases import service as tc
from app.testcases import statuses as tst

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]
FEATURE_MAX_BYTES = 2 * 1024 * 1024
CHECK_MAX = 200_000
SCENARIOS_MAX = 1_000  # scenarios (cases) one .feature import may hold
TOO_LONG = f"kịch bản BDD tối đa {tc.BDD_MAX} ký tự"
CLOSED_RUN = "test run đã đóng, không sửa hay ghi kết quả được nữa"
BUSY = "test đang được người khác cập nhật; thử lại sau vài giây"


# -- check -----------------------------------------------------------------------------------------


class CheckIn(BaseModel):
    text: str = Field(max_length=CHECK_MAX)
    # case: one scenario, no Feature line (a case's editor); feature: a whole .feature file.
    mode: Literal["case", "feature"] = "case"


class CheckError(BaseModel):
    line: int
    message: str


class CheckOut(BaseModel):
    ok: bool
    errors: list[CheckError]
    scenarios: int  # how many Scenario / Scenario Outline
    outline_columns: list[str]  # the Examples' columns (the case's parameter table)
    steps: list[dict[str, str]] = Field(default_factory=list)  # as a run shows them


@router.post("/testcases/bdd/check", response_model=CheckOut)
def check(body: CheckIn, principal: CurrentPrincipal) -> CheckOut:
    if not principal.can("tests.view"):
        raise HTTPException(403, "bạn không có quyền xem testcase")
    try:
        feature = gherkin.check_case(body.text) if body.mode == "case" else gherkin.parse(body.text)
    except gherkin.GherkinError as exc:
        return CheckOut(
            ok=False,
            errors=[CheckError(line=exc.line, message=exc.message)],
            scenarios=0,
            outline_columns=[],
        )
    if body.mode == "feature" and not feature.line:
        return CheckOut(
            ok=False,
            errors=[CheckError(line=1, message="file .feature cần dòng Feature:")],
            scenarios=len(feature.scenarios),
            outline_columns=[],
        )
    columns: list[str] = []
    for sc in feature.scenarios:
        for name in gherkin.outline_params(sc.examples).get("columns", []):
            if name not in columns:
                columns.append(name)
    return CheckOut(
        ok=True,
        errors=[],
        scenarios=len(feature.scenarios),
        outline_columns=columns,
        steps=gherkin.scenario_steps(body.text) if body.mode == "case" else [],
    )


# -- import ----------------------------------------------------------------------------------------


class ImportError_(BaseModel):
    line: int
    scenario: str
    message: str


class FeatureImportOut(BaseModel):
    created: int
    updated: int
    unchanged: int
    sections: int  # sections made
    section_id: int | None  # the Feature's section (None: not needed, nothing new)
    errors: list[ImportError_]


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "file .feature phải là văn bản UTF-8") from None


def _section_named(
    session: Session, suite: TestSuite, parent_id: int | None, name: str
) -> TestSection | None:
    query = select(TestSection).where(TestSection.suite_id == suite.id)
    query = query.where(
        TestSection.parent_id.is_(None) if parent_id is None else TestSection.parent_id == parent_id
    )
    wanted = " ".join(name.split()).casefold()
    for s in session.scalars(query.order_by(TestSection.position, TestSection.id)):
        if " ".join(s.name.split()).casefold() == wanted:
            return s
    return None


def _case_texts(feature: gherkin.Feature) -> list[str | None]:
    """Each scenario's case text (Background first), None for one longer than a case may keep.
    The Background is rendered once for the whole file and a case text is only built when it
    fits, so the work stays linear in the file; 422 when no scenario fits (every case would be
    refused, e.g. a Background longer than a case may keep)."""
    background = gherkin.background_text(feature)
    extra = len(tc.CONTROL.sub("", background)) + 2 if background is not None else 0
    if extra - 2 > tc.BDD_MAX:
        raise HTTPException(422, f"Background dài quá: {TOO_LONG}")
    out: list[str | None] = []
    for sc in feature.scenarios:
        body = gherkin.scenario_text(feature, sc)
        fits = extra + len(tc.CONTROL.sub("", body)) <= tc.BDD_MAX
        out.append(gherkin.join_case(background, body) if fits else None)
    if all(t is None for t in out):
        raise HTTPException(422, f"mọi Scenario (cùng Background) dài quá: {TOO_LONG}")
    return out


@router.post("/testcases/import/feature", response_model=FeatureImportOut)
def import_feature(
    principal: CurrentPrincipal,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    suite_id: Annotated[int, Form()],
    section_id: Annotated[int | None, Form()] = None,
) -> FeatureImportOut:
    """One .feature (≤ 2 MB) into the suite: the Feature is the section of that name under
    `section_id` (or the suite's root), made when a new case needs it; each scenario a BDD case
    titled by its name, its Background written first. A `@C<id>` tag naming a case of this suite
    updates that case (it stays in its section); other tags (the Feature's too) become tags."""
    suite = tc.suite_for(session, principal, suite_id, edit=True)
    data = file.file.read(FEATURE_MAX_BYTES + 1)
    if len(data) > FEATURE_MAX_BYTES:
        raise HTTPException(413, f"file lớn hơn {FEATURE_MAX_BYTES // (1024 * 1024)} MB")
    if not data.strip():
        raise HTTPException(422, "file rỗng")
    text = _decode(data)
    try:
        feature = gherkin.parse(text)
    except gherkin.GherkinError as exc:
        raise HTTPException(422, f"file .feature {exc}") from None
    if not feature.line:
        raise HTTPException(422, "file .feature cần dòng Feature:")
    if not feature.scenarios:
        raise HTTPException(422, "file .feature không có Scenario nào")
    if len(feature.scenarios) > SCENARIOS_MAX:
        raise HTTPException(
            422, f"file .feature có {len(feature.scenarios)} Scenario, tối đa {SCENARIOS_MAX}"
        )
    texts = _case_texts(feature)
    if section_id is not None:
        parent = session.get(TestSection, section_id)
        if parent is None or parent.suite_id != suite.id:
            raise HTTPException(422, "thư mục không thuộc bộ testcase này")
    # Parsed and checked: only now one import (and its section) at a time.
    session.refresh(suite, with_for_update=True)
    stem = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    section_title = " ".join((feature.name or stem or "Feature").split())[: tc.SECTION_MAX]
    section = _section_named(session, suite, section_id, section_title)
    made_sections = 0
    rule = approval.approval_on(session, suite.business_project_id)
    approver = approval.may_approve(principal, suite.business_project_id)
    case_fields = tf.rows(session, suite.business_project_id, "case")
    position = tc.next_position(session, suite.id)
    feature_tags = [t for t in feature.tags if content.case_id_of(t) is None]
    created = updated = unchanged = 0
    errors: list[ImportError_] = []
    seen: set[int] = set()
    for n, sc in enumerate(feature.scenarios, start=1):
        title = " ".join(sc.name.split()) or f"Scenario {n}"
        bdd = texts[n - 1]
        if bdd is None:
            errors.append(ImportError_(line=sc.line, scenario=title[:200], message=TOO_LONG))
            continue
        ids = [i for t in sc.tags if (i := content.case_id_of(t)) is not None]
        tags = list(
            dict.fromkeys([*feature_tags, *(t for t in sc.tags if not content.case_id_of(t))])
        )
        target = None
        for case_id in ids:
            found = session.get(TestCase, case_id)
            if found is not None and found.suite_id == suite.id and found.id not in seen:
                target = found
                break
        raw: dict[str, Any] = {"title": title, "template": "bdd", "bdd": bdd}
        try:
            if target is not None:
                session.refresh(target, with_for_update=True)
                raw["tags"] = [*(target.tags or []), *tags]
                values = tc.clean(raw, partial=True, current=target)
                before = target.status
                changed = any(
                    k not in tc.NOT_CONTENT and getattr(target, k) != v for k, v in values.items()
                )
                status = approval.guard_status(
                    session,
                    principal,
                    suite.business_project_id,
                    before=before,
                    wanted=None,
                    content_changed=changed,
                    rule=rule,
                    approver=approver,
                )
                if status is not None:
                    values["status"] = status
                seen.add(target.id)
                if tc.change(session, target, values, user_id=principal.user_id, via="import"):
                    updated += 1
                else:
                    unchanged += 1
                continue
            raw["tags"] = tags
            raw["status"] = "draft"
            values = tc.clean(raw)
        except tc.FieldError as exc:
            errors.append(ImportError_(line=sc.line, scenario=title[:200], message=str(exc)))
            continue
        if section is None:
            try:
                section = tc.create_section(
                    session,
                    suite,
                    parent_id=section_id,
                    name=section_title,
                    description=feature.description or None,
                )
            except tc.FieldError as exc:
                raise HTTPException(422, str(exc)) from None
            made_sections += 1
        values["section_id"] = section.id
        status = approval.guard_status(
            session,
            principal,
            suite.business_project_id,
            before=None,
            wanted=values.get("status"),
            content_changed=False,
            rule=rule,
            approver=approver,
        )
        if status is not None:
            values["status"] = status
        new = tc.create(
            session,
            suite,
            values,
            user_id=principal.user_id,
            source="import",
            position=position,
            fields=case_fields,
        )
        seen.add(new.id)
        position += 1
        created += 1
    suite.updated_by = principal.user_id
    audit(
        session,
        principal.user_id,
        "testcase.import.feature",
        f"test_suite:{suite.id}",
        business_project_id=suite.business_project_id,
        file=(file.filename or "")[:200],
        created=created,
        updated=updated,
        errors=len(errors),
    )
    session.commit()
    return FeatureImportOut(
        created=created,
        updated=updated,
        unchanged=unchanged,
        sections=made_sections,
        section_id=section.id if section is not None else None,
        errors=errors,
    )


# -- export ----------------------------------------------------------------------------------------


def _file_name(name: str, taken: set[str]) -> str:
    base = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name).strip() or "feature"
    base = "_".join(base.split())[:80]
    out, n = base, 1
    while out.casefold() in taken:
        n += 1
        out = f"{base}_{n}"
    taken.add(out.casefold())
    return f"{out}.feature"


def _attachment(data: bytes, name: str, mime: str, ext: str) -> Response:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_").strip() or "feature"
    return Response(
        data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="feature.{ext}"; '
            f"filename*=UTF-8''{quote(safe)}.{ext}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def feature_files(
    session: Session, suite: TestSuite, section_id: int | None = None
) -> list[tuple[str, str]]:
    """[(file name, .feature text)]: one per section holding BDD cases (not obsolete), in the
    order of the tree; the cases at the suite's root make a Feature named after the suite."""
    sections = content.section_rows(session, [suite.id])
    query = select(TestCase).where(
        TestCase.suite_id == suite.id, TestCase.template == "bdd", TestCase.status != "obsolete"
    )
    if section_id is not None:
        query = query.where(TestCase.section_id.in_(content.descendants(sections, section_id)))
    cases = list(session.scalars(query.order_by(TestCase.position, TestCase.id)))
    by_section: dict[int | None, list[TestCase]] = {}
    for c in cases:
        by_section.setdefault(c.section_id, []).append(c)
    order: list[tuple[int | None, str, str | None]] = []
    if None in by_section:
        order.append((None, suite.name, suite.description))
    order += [(s.id, s.name, s.description) for s, _ in content.tree_order(sections)]
    taken: set[str] = set()
    out = []
    for sid, name, about in order:
        group = by_section.get(sid)
        if not group:
            continue
        scenarios = [
            (
                [
                    content.key("case", c.id) or "",
                    *(gherkin.tag_name(t) for t in c.tags or [] if gherkin.tag_name(t)),
                ],
                c.bdd or "",
                c.title,
            )
            for c in group
        ]
        out.append((_file_name(name, taken), gherkin.format_feature(name, about, scenarios)))
    return out


@router.get("/testcases/export/feature")
def export_feature(
    principal: CurrentPrincipal,
    session: SessionDep,
    suite_id: int,
    section_id: int | None = None,
) -> Response:
    suite = tc.suite_for(session, principal, suite_id)
    if section_id is not None:
        section = session.get(TestSection, section_id)
        if section is None or section.suite_id != suite.id:
            raise HTTPException(404, "thư mục không thuộc bộ testcase này")
    files = feature_files(session, suite, section_id)
    if not files:
        raise HTTPException(404, "không có testcase BDD nào để xuất")
    if len(files) == 1:
        name, text = files[0]
        return _attachment(
            text.encode("utf-8"), name.rsplit(".", 1)[0], "text/plain; charset=utf-8", "feature"
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in files:
            zf.writestr(name, text)
    return _attachment(buf.getvalue(), suite.name, "application/zip", "zip")


# -- exploratory sessions --------------------------------------------------------------------------


class SessionIn(BaseModel):
    action: Literal["start", "pause", "resume"]


class NoteIn(BaseModel):
    kind: Literal["note", "bug", "question", "idea"]
    text: str = Field(min_length=1, max_length=exploratory.NOTE_MAX)


def _locked_test(session: Session, principal: Principal, test_id: int) -> TestRunTest:
    """The test locked for update (its run first, in share mode: the order recording a result
    takes, so a result and a session wait for each other instead of deadlocking); 404 unseen,
    409 run closed, 403 when the person may not record its result."""
    short_lock_wait(session)
    run_id = session.scalar(select(TestRunTest.run_id).where(TestRunTest.id == test_id))
    run = (
        session.scalar(
            select(TestRun)
            .where(TestRun.id == run_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if run_id is not None
        else None
    )
    test = (
        session.scalar(
            select(TestRunTest)
            .where(TestRunTest.id == test_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is not None
        else None
    )
    if test is None or run is None or test.run_id != run.id:
        raise HTTPException(404, "test không tồn tại")
    if not tc.may_view(principal, run.business_project_id):
        raise HTTPException(404, "test không tồn tại")
    if run.completed_at is not None:
        raise HTTPException(409, CLOSED_RUN)
    if not tst.may_record(
        session, principal, run.business_project_id, [test.assigned_to, run.assigned_to]
    ):
        raise HTTPException(
            403, "chỉ người được giao test hoặc người ghi được kết quả mới dùng phiên"
        )
    return test


@router.post("/testcases/tests/{test_id:int}/session", response_model=dict[str, Any])
@busy_as_409(BUSY)
def session_action(
    test_id: int, body: SessionIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    test = _locked_test(session, principal, test_id)
    try:
        test.session = exploratory.act(test.session, body.action)
    except exploratory.SessionError as exc:
        raise HTTPException(409, str(exc)) from None
    session.commit()
    return dict(test.session)


@router.post("/testcases/tests/{test_id:int}/session/notes", response_model=dict[str, Any])
@busy_as_409(BUSY)
def session_note(
    test_id: int, body: NoteIn, principal: CurrentPrincipal, session: SessionDep
) -> dict[str, Any]:
    test = _locked_test(session, principal, test_id)
    try:
        user = session.get(User, principal.user_id) if principal.user_id else None
        test.session = exploratory.add_note(
            test.session,
            body.kind,
            body.text,
            principal.user_id,
            user_name=(user.full_name or user.primary_email) if user is not None else None,
        )
    except exploratory.SessionError as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    session.commit()
    return dict(test.session)
