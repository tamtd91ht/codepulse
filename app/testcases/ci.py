"""Automation results from CI (docs/specs/30a G1, docs/specs/30 §15.4): a report uploaded by a
pipeline (JUnit XML and its xUnit / TestNG-JUnit cousins, TestNG's own XML, xUnit.net v2 XML,
Playwright's JSON reporter, Cucumber JSON) becomes the results of a test run.

Each test of the report maps to cases in this order: a `C<id>` key in its name, tags or
properties (@C12, [C12], C12:) of a case in a suite of the dự án; else the case of the suite
whose `automation_id` is "classname.name" (or the bare name); else, with `auto_create`, a new
case (kind automated, draft, source "import") in the folder "Automation/Unmapped"; else it is
listed as unmapped. A skipped test writes no result (TestRail: no status) and is counted apart.

An automation_id matching only an obsolete case is not mapped (listed as "case lỗi thời") and
not created again (automation_id is unique in a suite, whatever the status). Auto-created cases
are inserted with ON CONFLICT DO NOTHING and selected again, so two imports at once share them.

A run gets one CI result per test per commit: uploading again into the same run (run_id) for the
same commit updates that result instead of adding another one (each status seen is kept in
`ci.attempts`, and per test identity in `ci.identities`). The job works in a constant number of
queries per chunk of tests (cases loaded by key and automation_id up front, results written in
bulk), and saves its progress after each chunk. Every transaction that writes into a run first
takes the run's advisory lock (RUN_LOCK, run id) and re-reads the run: CI writers of one run take
turns, and a run closed meanwhile stops the job (results already saved stay).

A case is flaky when one test identity (automation_id, plus the Playwright project: chromium and
webkit are two identities) both passed and failed on the same (non-null) commit, or when the
case's verdict flipped at least FLAKY_FLIPS times in its last FLAKY_WINDOW CI results that carry
a commit (oldest first).

XML is parsed with the standard library after refusing any DOCTYPE (no entity can then be
declared: no "billion laughs", no external entity).
"""

import io
import itertools
import json
import logging
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import case as sa_case
from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import (
    Project,
    TestCase,
    TestCiImport,
    TestResult,
    TestRun,
    TestRunTest,
    TestSuite,
)
from app.testcases import content, events
from app.testcases import fields as tf
from app.testcases import runs as rs
from app.testcases import service as tc

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TESTS = rs.RUN_TESTS_MAX  # tests of one report (and of one run)
CHUNK = 500  # tests written between two progress saves
JOB_TIMEOUT_S = 600
LOG_MAX = 10_000
STACK_MAX = 20_000
NAME_MAX = 500
ATTACHMENTS_MAX = 20
UNMAPPED_MAX = 500
ATTEMPTS_MAX = 10
FLAKY_WINDOW = 10
FLAKY_FLIPS = 3
IDENTITIES_MAX = 20  # test identities kept per result (ci.identities)
RUN_LOCK = 30_151  # pg_advisory_xact_lock(RUN_LOCK, run id): CI writers of one run take turns
OUTCOMES = frozenset({"passed", "failed"})
UNMAPPED_SECTION = "Automation > Unmapped"
FORMATS = ("junit", "testng", "xunit", "playwright", "cucumber")

# A case key in a test's name or tags: C12, @C12, [C12], C12: (not inside a longer word).
CASE_KEY = re.compile(r"(?<![A-Za-z0-9])C(\d{1,9})(?![0-9A-Za-z])")
# The prolog of an XML file: declaration, comments, processing instructions.
_PROLOG = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*(?:(?:<\?.*?\?>|<!--.*?-->)\s*)*", re.S)
_ATTACHMENT = re.compile(r"\[\[ATTACHMENT\|([^\]\n]{1,500})\]\]")


class ReportError(ValueError):
    """A report that cannot be read; the message is shown (Vietnamese)."""


@dataclass
class CiTest:
    """One test of a report."""

    name: str
    classname: str = ""
    file: str | None = None
    status: str | None = None  # passed | failed | None (skipped)
    duration_s: float | None = None
    message: str | None = None
    stack: str | None = None
    attachments: list[dict[str, str]] = field(default_factory=list)
    keys: list[int] = field(default_factory=list)  # C<id> found in the name, tags, properties
    attempts: list[str] = field(default_factory=list)  # statuses of the retries, oldest first
    project: str | None = None  # Playwright project (browser): part of the test's identity

    @property
    def automation_id(self) -> str:
        name = self.name.strip()
        return f"{self.classname.strip()}.{name}"[:NAME_MAX] if self.classname.strip() else name

    @property
    def identity(self) -> str:
        """One test as flakiness sees it: its automation id, and the Playwright project."""
        return f"{self.automation_id} [{self.project}]" if self.project else self.automation_id

    @property
    def skipped(self) -> bool:
        return self.status is None


def case_keys(*texts: str | None) -> list[int]:
    out: list[int] = []
    for t in texts:
        for m in CASE_KEY.finditer(t or ""):
            n = int(m.group(1))
            if n not in out:
                out.append(n)
    return out


def _cut(value: Any, limit: int) -> str | None:
    out = rs.CONTROL.sub("", str(value or "")).strip()
    return out[:limit] or None


def _seconds(value: Any, scale: float = 1.0) -> float | None:
    try:
        n = float(str(value).replace(",", "")) * scale
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


# -- XML -------------------------------------------------------------------------------------------


def _refuse_doctype(data: bytes) -> None:
    # UTF-16 / UTF-32 would hide a DOCTYPE from the byte check below: UTF-8 only.
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in data[:8]:
        raise ReportError("file XML phải mã hoá UTF-8")
    head = _PROLOG.match(data)
    rest = data[head.end() if head else 0 :]
    if rest[:2] == b"<!":
        raise ReportError("file XML có khai báo DOCTYPE / ENTITY: không nhận (vì an toàn)")


def _xml(data: bytes) -> ET.Element:
    """Parse XML that declares no DOCTYPE (refused: entities could only be declared there)."""
    _refuse_doctype(data)
    try:
        return ET.fromstring(data)  # noqa: S314 (DOCTYPE refused above)
    except ET.ParseError as exc:
        raise ReportError(f"file XML không đọc được: {exc}") from None


def _root_tag(data: bytes) -> str:
    """The root element's name, read without parsing the whole file (DOCTYPE refused first)."""
    _refuse_doctype(data)
    try:
        for _event, node in ET.iterparse(io.BytesIO(data), events=("start",)):  # noqa: S314
            return _local(node.tag)
    except ET.ParseError as exc:
        raise ReportError(f"file XML không đọc được: {exc}") from None
    raise ReportError("file XML không có phần tử nào")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(node: ET.Element, name: str) -> Iterator[ET.Element]:
    return (c for c in node if _local(c.tag) == name)


def _child(node: ET.Element, name: str) -> ET.Element | None:
    return next(_children(node, name), None)


def _attachments(*texts: str | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for t in texts:
        for m in _ATTACHMENT.finditer(t or ""):
            path = m.group(1).strip()
            out.append({"name": path.replace("\\", "/").rsplit("/", 1)[-1], "path": path})
    return out[:ATTACHMENTS_MAX]


def parse_junit(root: ET.Element) -> list[CiTest]:
    """<testsuites>/<testsuite>/<testcase> (JUnit, pytest, Surefire, Jest/Mocha reporters,
    TestNG's JUnit report)."""
    out: list[CiTest] = []
    for case in root.iter():
        if _local(case.tag) != "testcase":
            continue
        name = _cut(case.get("name"), NAME_MAX) or "(không tên)"
        classname = _cut(case.get("classname") or case.get("class"), NAME_MAX) or ""
        failure = _child(case, "failure")
        if failure is None:
            failure = _child(case, "error")
        skipped = _child(case, "skipped")
        if skipped is None:
            skipped = _child(case, "disabled")
        props = [
            f"{p.get('name') or ''}={p.get('value') or p.text or ''}"
            for holder in _children(case, "properties")
            for p in _children(holder, "property")
        ]
        out_text = _child(case, "system-out")
        err_text = _child(case, "system-err")
        sys_out = out_text.text if out_text is not None else None
        sys_err = err_text.text if err_text is not None else None
        attachments = _attachments(sys_out, sys_err)
        attachments += [
            {"name": str(v).rsplit("/", 1)[-1], "path": str(v)}
            for p in props
            if p.lower().startswith("attachment=") and (v := p.split("=", 1)[1])
        ]
        test = CiTest(
            name=name,
            classname=classname,
            file=_cut(case.get("file"), NAME_MAX),
            duration_s=_seconds(case.get("time")),
            attachments=attachments[:ATTACHMENTS_MAX],
            keys=case_keys(name, *props),
        )
        if failure is not None:
            test.status = "failed"
            body = (failure.text or "").strip()
            message = failure.get("message") or body.split("\n", 1)[0] or failure.get("type")
            test.message = _cut(message, LOG_MAX)
            test.stack = _cut(body, STACK_MAX)
            if sys_err:
                test.message = _cut(f"{test.message or ''}\n\n{sys_err}", LOG_MAX)
        elif skipped is not None:
            test.status = None
            test.message = _cut(skipped.get("message") or skipped.text, LOG_MAX)
        else:
            test.status = "passed"
        out.append(test)
    return out


def parse_testng(root: ET.Element) -> list[CiTest]:
    """TestNG's own testng-results.xml: <class name>/<test-method status=PASS|FAIL|SKIP>;
    configuration methods (is-config) are not tests."""
    out: list[CiTest] = []
    for klass in root.iter():
        if _local(klass.tag) != "class":
            continue
        classname = _cut(klass.get("name"), NAME_MAX) or ""
        for method in _children(klass, "test-method"):
            if (method.get("is-config") or "").lower() == "true":
                continue
            name = _cut(method.get("name"), NAME_MAX) or "(không tên)"
            status = (method.get("status") or "").upper()
            desc = method.get("description") or ""
            test = CiTest(
                name=name,
                classname=classname,
                duration_s=_seconds(method.get("duration-ms"), 0.001),
                keys=case_keys(name, desc),
                status={"PASS": "passed", "FAIL": "failed"}.get(status),
            )
            exc = _child(method, "exception")
            if exc is not None:
                msg = _child(exc, "message")
                stack = _child(exc, "full-stacktrace")
                test.message = _cut(
                    (msg.text if msg is not None else None) or exc.get("class"), LOG_MAX
                )
                test.stack = _cut(stack.text if stack is not None else None, STACK_MAX)
            out.append(test)
    return out


def parse_xunit(root: ET.Element) -> list[CiTest]:
    """xUnit.net v2: <assemblies>/<assembly>/<collection>/<test name type method result>."""
    out: list[CiTest] = []
    for node in root.iter():
        if _local(node.tag) != "test":
            continue
        full = _cut(node.get("name"), NAME_MAX) or "(không tên)"
        klass = _cut(node.get("type"), NAME_MAX) or ""
        method = _cut(node.get("method"), NAME_MAX)
        traits = [
            f"{t.get('name') or ''}={t.get('value') or ''}"
            for holder in _children(node, "traits")
            for t in _children(holder, "trait")
        ]
        result = (node.get("result") or "").lower()
        test = CiTest(
            name=method if method and full.startswith(f"{klass}.") else full,
            classname=klass,
            duration_s=_seconds(node.get("time")),
            keys=case_keys(full, *traits),
            status={"pass": "passed", "fail": "failed"}.get(result),
        )
        failure = _child(node, "failure")
        if failure is not None:
            msg, stack = _child(failure, "message"), _child(failure, "stack-trace")
            test.message = _cut(msg.text if msg is not None else None, LOG_MAX)
            test.stack = _cut(stack.text if stack is not None else None, STACK_MAX)
        out.append(test)
    return out


# -- JSON ------------------------------------------------------------------------------------------

_PW_FAILED = {"failed", "timedOut", "interrupted"}


def parse_playwright(data: dict[str, Any]) -> list[CiTest]:
    """Playwright's JSON reporter: suites (files, then describe blocks) of specs; each spec has a
    test per project (browser). The last attempt decides; earlier ones are kept as attempts."""
    out: list[CiTest] = []

    def walk(suite: dict[str, Any], file: str | None, path: list[str]) -> None:
        file = suite.get("file") or file
        title = str(suite.get("title") or "")
        here = [*path, title] if title and title != file else path
        for spec in suite.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            name = _cut(spec.get("title"), NAME_MAX) or "(không tên)"
            tags = [str(t) for t in spec.get("tags") or []]
            for t in spec.get("tests") or []:
                if not isinstance(t, dict):
                    continue
                notes = [
                    f"{a.get('type') or ''}={a.get('description') or ''}"
                    for a in t.get("annotations") or []
                    if isinstance(a, dict)
                ]
                results = [r for r in t.get("results") or [] if isinstance(r, dict)]
                last = results[-1] if results else {}
                raw = str(last.get("status") or "skipped")
                status = "passed" if raw == "passed" else "failed" if raw in _PW_FAILED else None
                if t.get("status") == "skipped":
                    status = None
                raw_error = last.get("error")
                error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
                atts = [
                    {
                        "name": str(a.get("name") or ""),
                        "path": str(a.get("path") or ""),
                        "content_type": str(a.get("contentType") or ""),
                    }
                    for r in results
                    for a in r.get("attachments") or []
                    if isinstance(a, dict) and a.get("path")
                ]
                out.append(
                    CiTest(
                        name=name,
                        classname=" > ".join(p for p in [file or "", *here] if p),
                        file=file,
                        status=status,
                        duration_s=_seconds(last.get("duration"), 0.001),
                        message=_cut(error.get("message"), LOG_MAX),
                        stack=_cut(error.get("stack"), STACK_MAX),
                        attachments=atts[:ATTACHMENTS_MAX],
                        keys=case_keys(name, *tags, *notes, *here),
                        attempts=[
                            "passed" if r.get("status") == "passed" else "failed"
                            for r in results
                            if r.get("status") != "skipped"
                        ][-ATTEMPTS_MAX:],
                        project=_cut(t.get("projectName") or t.get("projectId"), NAME_MAX),
                    )
                )
        for child in suite.get("suites") or []:
            if isinstance(child, dict):
                walk(child, file, here)

    for top in data.get("suites") or []:
        if isinstance(top, dict):
            walk(top, None, [])
    return out


def parse_cucumber(data: list[Any]) -> list[CiTest]:
    """Cucumber JSON: features with scenarios (elements) of steps. A scenario failed when a step
    (or its background) failed; skipped / pending / undefined without failure: no result."""
    out: list[CiTest] = []
    for feature in data:
        if not isinstance(feature, dict):
            continue
        fname = _cut(feature.get("name"), NAME_MAX) or ""
        ftags = [str(t.get("name") or "") for t in feature.get("tags") or [] if isinstance(t, dict)]
        background: list[dict[str, Any]] = []
        for el in feature.get("elements") or []:
            if not isinstance(el, dict):
                continue
            steps = [s for s in el.get("steps") or [] if isinstance(s, dict)]
            if el.get("type") == "background":
                background = steps
                continue
            steps = background + steps
            background = []
            name = _cut(el.get("name"), NAME_MAX) or "(không tên)"
            tags = [str(t.get("name") or "") for t in el.get("tags") or [] if isinstance(t, dict)]
            statuses = [str((s.get("result") or {}).get("status") or "") for s in steps]
            failed = next(
                (s for s in steps if (s.get("result") or {}).get("status") == "failed"), None
            )
            if failed is not None:
                status: str | None = "failed"
            elif statuses and all(s == "passed" for s in statuses):
                status = "passed"
            else:
                status = None
            nanos = sum(
                float((s.get("result") or {}).get("duration") or 0)
                for s in steps
                if isinstance((s.get("result") or {}).get("duration"), int | float)
            )
            error = str((failed.get("result") or {}).get("error_message") or "") if failed else ""
            head, _, rest = error.partition("\n")
            out.append(
                CiTest(
                    name=name,
                    classname=fname,
                    file=_cut(feature.get("uri"), NAME_MAX),
                    status=status,
                    duration_s=nanos / 1e9 if nanos else None,
                    message=_cut(
                        f"{failed.get('keyword') or ''}{failed.get('name') or ''}: {head}"
                        if failed
                        else None,
                        LOG_MAX,
                    ),
                    stack=_cut(rest, STACK_MAX),
                    keys=case_keys(name, *tags, *ftags),
                )
            )
    return out


def detect(data: bytes) -> str:
    """The report's format from its content, never its name."""
    head = data.lstrip(b"\xef\xbb\xbf \t\r\n")[:1]
    if head == b"<":
        tag = _root_tag(data)
        if tag in ("testsuites", "testsuite"):
            return "junit"
        if tag == "testng-results":
            return "testng"
        if tag in ("assemblies", "assembly"):
            return "xunit"
        raise ReportError(f"không nhận ra định dạng XML (thẻ gốc <{tag[:40]}>)")
    if head in (b"{", b"["):
        doc = _json(data)
        if isinstance(doc, dict) and "suites" in doc:
            return "playwright"
        if isinstance(doc, list) and all(isinstance(f, dict) and "elements" in f for f in doc):
            return "cucumber"
        raise ReportError("không nhận ra định dạng JSON (cần Playwright hoặc Cucumber JSON)")
    raise ReportError("file không phải báo cáo JUnit / TestNG / xUnit / Playwright / Cucumber")


def _json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ReportError("file JSON không đọc được") from None


def parse(data: bytes, fmt: str | None = None) -> tuple[str, list[CiTest]]:
    """(format, tests) of a report; refused when unreadable or larger than MAX_TESTS tests."""
    if not data.strip():
        raise ReportError("file rỗng")
    if len(data) > MAX_FILE_BYTES:
        raise ReportError(f"file lớn hơn {MAX_FILE_BYTES // (1024 * 1024)} MB")
    fmt = fmt or detect(data)
    if fmt in ("junit", "testng", "xunit"):
        root = _xml(data)
        tests = {"junit": parse_junit, "testng": parse_testng, "xunit": parse_xunit}[fmt](root)
    elif fmt == "playwright":
        doc = _json(data)
        if not isinstance(doc, dict):
            raise ReportError("báo cáo Playwright phải là một object JSON")
        tests = parse_playwright(doc)
    elif fmt == "cucumber":
        doc = _json(data)
        if not isinstance(doc, list):
            raise ReportError("báo cáo Cucumber phải là một mảng JSON")
        tests = parse_cucumber(doc)
    else:
        raise ReportError("định dạng không hỗ trợ")
    if len(tests) > MAX_TESTS:
        raise ReportError(f"báo cáo có {len(tests)} test, tối đa {MAX_TESTS}")
    return fmt, tests


# -- mapping ---------------------------------------------------------------------------------------


@dataclass
class Merged:
    """What one case gets from the tests of a report mapped to it."""

    status: str | None = None
    elapsed: float = 0.0
    messages: list[str] = field(default_factory=list)
    stacks: list[str] = field(default_factory=list)
    attachments: list[dict[str, str]] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    file: str | None = None
    attempts: list[str] = field(default_factory=list)
    # test identity -> its attempts: flakiness compares a test with itself only.
    identities: dict[str, list[str]] = field(default_factory=dict)

    def add(self, t: CiTest) -> None:
        # The worst wins: failed over passed over skipped.
        if t.status == "failed" or (t.status == "passed" and self.status is None):
            self.status = t.status
        self.elapsed += t.duration_s or 0.0
        if t.message:
            self.messages.append(t.message)
        if t.stack:
            self.stacks.append(t.stack)
        self.attachments.extend(t.attachments)
        self.names.append(t.automation_id)
        self.file = self.file or t.file
        tried = t.attempts or ([t.status] if t.status else [])
        self.attempts.extend(tried)
        if tried:
            self.identities.setdefault(t.identity, []).extend(tried)


@dataclass
class Mapping:
    by_case: dict[int, Merged]
    unmapped: list[str]
    created: int
    mapped: int  # tests of the report mapped to a case
    skipped: int


def _live_cases(session: Session, bp_id: int, ids: Iterable[int]) -> dict[int, TestCase]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    return {
        c.id: c
        for c in session.scalars(
            select(TestCase)
            .join(TestSuite, TestSuite.id == TestCase.suite_id)
            .where(
                TestCase.id.in_(wanted),
                tc.suite_in([bp_id]),
                TestCase.status != "obsolete",
            )
        )
    }


def _by_automation_id(
    session: Session, bp_id: int, suite_id: int | None, ids: Iterable[str]
) -> dict[str, TestCase]:
    """automation id -> its case, whatever the status (the id is unique in a suite, obsolete
    cases included); across suites a live case wins over an obsolete one."""
    wanted = sorted({i for i in ids if i})
    if not wanted:
        return {}
    query = (
        select(TestCase)
        .join(TestSuite, TestSuite.id == TestCase.suite_id)
        .where(TestCase.automation_id.in_(wanted), tc.suite_in([bp_id]))
        .order_by(sa_case((TestCase.status == "obsolete", 1), else_=0), TestCase.id)
    )
    if suite_id is not None:
        query = query.where(TestCase.suite_id == suite_id)
    out: dict[str, TestCase] = {}
    for c in session.scalars(query):
        if c.automation_id:
            out.setdefault(c.automation_id, c)
    return out


def _create_cases(
    session: Session, bp_id: int, suite: TestSuite, tests: list[CiTest], user_id: int | None
) -> tuple[dict[str, TestCase], int]:
    """One draft automated case per automation id, in Automation > Unmapped, inserted at once
    with ON CONFLICT DO NOTHING (another import may make the same ones meanwhile), then selected
    again. Returns (automation id -> case, how many this call inserted)."""
    section_id = content.section_for_path(session, suite.id, UNMAPPED_SECTION)
    top = tc.next_position(session, suite.id)
    # §15.8: the defaults of the home dự án's case fields, as `tc.create` gives a new case; the
    # same for every case made here (one suite, template "text").
    defaults = tf.with_defaults(
        session, suite.business_project_id, "case", {}, suite_id=suite.id, template="text"
    )
    rows: dict[str, dict[str, Any]] = {}
    for t in tests:
        aid = t.automation_id
        if aid in rows:
            continue
        values: dict[str, Any] = dict(
            suite_id=suite.id,
            section_id=section_id,
            title=_cut(t.name, tc.TITLE_MAX) or aid[: tc.TITLE_MAX],
            template="text",
            type="automated",
            kind="automated",
            status="draft",
            source="import",
            automation_id=aid,
            automation=_cut(t.file, tc.AUTOMATION_MAX),
            priority="medium",
            tags=[],
            jira_keys=[],
            code_paths=[],
            steps_list=[],
            custom=dict(defaults),
            revision=1,
            position=top + len(rows),
            created_by=user_id,
            updated_by=user_id,
        )
        values["search"] = tc.search_text(TestCase(**values))  # transient: never added
        rows[aid] = values
    if not rows:
        return {}, 0
    session.flush()
    inserted = session.scalars(
        pg_insert(TestCase)
        .on_conflict_do_nothing(
            index_elements=[TestCase.suite_id, TestCase.automation_id],
            index_where=TestCase.automation_id.is_not(None),
        )
        .returning(TestCase.id),
        list(rows.values()),
    ).all()
    return _by_automation_id(session, bp_id, suite.id, rows), len(inserted)


def map_tests(
    session: Session,
    bp_id: int,
    suite: TestSuite | None,
    tests: list[CiTest],
    *,
    auto_create: bool,
    user_id: int | None,
) -> Mapping:
    by_key = _live_cases(session, bp_id, (k for t in tests for k in t.keys))
    unkeyed = [t for t in tests if not any(k in by_key for k in t.keys)]
    by_aid = _by_automation_id(
        session, bp_id, suite.id if suite else None, (t.automation_id for t in unkeyed)
    )
    missing = [t for t in unkeyed if t.automation_id not in by_aid]
    created = 0
    if missing and auto_create and suite is not None:
        made, created = _create_cases(session, bp_id, suite, missing, user_id)
        by_aid.update(made)
    by_case: dict[int, Merged] = {}
    unmapped: dict[str, None] = {}
    mapped = skipped = 0
    for t in tests:
        cases = [by_key[k] for k in t.keys if k in by_key]
        found = by_aid.get(t.automation_id)
        if t.skipped:
            skipped += 1
        if not cases and found is not None and found.status == "obsolete":
            # Its case is obsolete: no result, and no new case (the id stays taken).
            unmapped.setdefault(
                f"{t.automation_id} (case lỗi thời {content.key('case', found.id)})"
            )
            continue
        if not cases and found is not None:
            cases = [found]
        if not cases:
            unmapped.setdefault(t.automation_id)
            continue
        mapped += 1
        for c in cases:
            by_case.setdefault(c.id, Merged()).add(t)
    return Mapping(by_case, list(unmapped), created, mapped, skipped)


# -- the import ------------------------------------------------------------------------------------


def run_name(params: dict[str, Any], now: datetime) -> str:
    if params.get("run_name"):
        return str(params["run_name"])[:200]
    parts = ["CI"]
    if params.get("branch"):
        parts.append(str(params["branch"])[:80])
    if params.get("commit_sha"):
        parts.append(str(params["commit_sha"])[:8])
    if len(parts) == 1:
        parts.append(f"{now:%d/%m/%Y %H:%M}")
    return " ".join(parts)


def ci_facts(params: dict[str, Any], import_id: int) -> dict[str, Any]:
    out = {
        "commit_sha": params.get("commit_sha"),
        "branch": params.get("branch"),
        "mr_iid": params.get("mr_iid"),
        "project": params.get("project"),
        "import_id": import_id,
    }
    return {k: v for k, v in out.items() if v is not None}


def _make_run(session: Session, imp: TestCiImport, now: datetime) -> TestRun:
    params = imp.params or {}
    config_ids = [int(i) for i in params.get("config_ids") or []]
    [(configs, config_name)] = rs.combinations(session, imp.business_project_id, config_ids)
    run = TestRun(
        business_project_id=imp.business_project_id,
        milestone_id=params.get("milestone_id"),
        name=run_name(params, now),
        config_ids=configs,
        config=config_name,
        refs=[],
        source="ci",
        source_meta={"ci": ci_facts(params, imp.id), "suite_id": imp.suite_id},
        created_by=imp.created_by,
    )
    session.add(run)
    session.flush()
    return run


def _save_progress(session: Session, imp: TestCiImport, done: int) -> None:
    imp.done = done
    session.commit()


def lock_run(
    session: Session, imp: TestCiImport, run_id: int, *, exclusive: bool = False
) -> TestRun:
    """Take the run's advisory lock for this transaction (CI writers of one run take turns),
    then read the run again: gone or closed meanwhile, the job stops. `exclusive` also locks the
    row FOR UPDATE (adding tests, closing: as the run's other writers do), else FOR SHARE."""
    session.execute(select(func.pg_advisory_xact_lock(RUN_LOCK, run_id)))
    run = session.scalar(
        select(TestRun)
        .where(TestRun.id == run_id)
        .with_for_update(read=not exclusive)
        .execution_options(populate_existing=True)
    )
    if run is None or run.business_project_id != imp.business_project_id:
        raise ReportError("test run không tồn tại")
    if run.completed_at is not None:
        if imp.done:
            raise ReportError(
                f"test run đã đóng trong lúc nhập: dừng sau {imp.done}/{imp.total} test"
            )
        raise ReportError("test run đã đóng")
    return run


def process(session: Session, import_id: int, now: datetime | None = None) -> TestCiImport:
    """Parse the import's report and write its results (see the module doc). Raises on failure;
    the caller marks the import failed."""
    now = now or datetime.now(UTC)
    imp = session.get(TestCiImport, import_id)
    if imp is None:
        raise ReportError("lượt nhập không tồn tại")
    if imp.status in ("done", "failed"):
        return imp  # already over (marked stale while it waited in the queue, say)
    imp.status = "running"
    session.commit()
    params = dict(imp.params or {})
    fmt, tests = parse(imp.data or b"", imp.format if imp.format in FORMATS else None)
    imp.format = fmt
    imp.total = len(tests)
    suite = session.get(TestSuite, imp.suite_id) if imp.suite_id else None
    mapping = map_tests(
        session,
        imp.business_project_id,
        suite,
        tests,
        auto_create=bool(params.get("auto_create")),
        user_id=imp.created_by,
    )
    imp.mapped = mapping.mapped
    imp.created_cases = mapping.created
    imp.unmapped = mapping.unmapped[:UNMAPPED_MAX]
    params["summary"] = {
        "skipped": mapping.skipped,
        "passed": sum(1 for t in tests if t.status == "passed"),
        "failed": sum(1 for t in tests if t.status == "failed"),
        "unmapped": len(mapping.unmapped),
    }
    imp.params = params
    if imp.run_id is not None:
        run = lock_run(session, imp, imp.run_id, exclusive=True)
    else:
        run = _make_run(session, imp, now)
        imp.run_id = run.id
        session.execute(select(func.pg_advisory_xact_lock(RUN_LOCK, run.id)))
    run_id = run.id
    cases = list(_live_cases(session, imp.business_project_id, mapping.by_case).values())
    try:
        # Under the run's locks: no other import or edit of the run adds the same cases now.
        rs.add_tests(session, run, cases)
    except HTTPException as exc:
        raise ReportError(str(exc.detail)) from None
    _save_progress(session, imp, 0)
    failed = write_results(session, imp, run_id, mapping.by_case, now)
    update_flaky(session, sorted(mapping.by_case))
    close = bool(params.get("close_run"))
    last = lock_run(session, imp, run_id, exclusive=True) if close else session.get(TestRun, run_id)
    if last is None:
        raise ReportError("test run không tồn tại")
    run = last
    _failed_events(session, run, failed)
    if close:
        rs.close_run(session, run, now)
        events.run_closed(session, run)
    imp.status = "done"
    imp.done = imp.total
    imp.data = None  # the report is not kept once processed
    imp.finished_at = datetime.now(UTC)
    session.commit()
    return imp


def write_results(
    session: Session,
    imp: TestCiImport,
    run_id: int,
    by_case: dict[int, Merged],
    now: datetime,
) -> list[int]:
    """One CI result per test and commit: an existing one (same run, same commit) is updated,
    else a new one is inserted. Each chunk is one transaction under the run's lock (`lock_run`),
    reading the run's tests and the commit's results of its cases again: another writer of the
    run may have committed since the previous chunk. Returns the failed tests' ids."""
    params = imp.params or {}
    facts = ci_facts(params, imp.id)
    commit = facts.get("commit_sha")
    created_by, total = imp.created_by, imp.total
    sha = TestResult.ci["commit_sha"].astext
    items = [(cid, m) for cid, m in by_case.items() if m.status is not None]
    failed: list[int] = []
    result_custom = _result_defaults(session, imp.business_project_id, [cid for cid, _m in items])
    for start in range(0, max(len(items), 1), CHUNK):
        chunk = items[start : start + CHUNK]
        lock_run(session, imp, run_id)
        # Plain values: the progress commits would otherwise reload every ORM object one by one.
        tests_by_case: dict[int, tuple[int, int | None]] = {
            int(case_id or 0): (int(test_id), assigned)
            for test_id, case_id, assigned in session.execute(
                select(TestRunTest.id, TestRunTest.case_id, TestRunTest.assigned_to).where(
                    TestRunTest.run_id == run_id,
                    TestRunTest.case_id.in_(sorted(cid for cid, _m in chunk) or [0]),
                )
            )
        }
        existing: dict[int, tuple[int, dict[str, Any]]] = {}
        if tests_by_case:
            for result_id, test_id, ci in session.execute(
                select(TestResult.id, TestResult.test_id, TestResult.ci)
                .where(
                    TestResult.test_id.in_(sorted(t for t, _a in tests_by_case.values())),
                    TestResult.source == "ci",
                    sha == commit if commit else sha.is_(None),
                )
                .order_by(TestResult.id)
            ):
                existing[test_id] = (result_id, dict(ci or {}))
        new_rows: list[dict[str, Any]] = []
        changed: list[dict[str, Any]] = []
        statuses: list[dict[str, Any]] = []
        for case_id, m in chunk:
            found = tests_by_case.get(case_id)
            if found is None:
                continue  # removed from the run meanwhile
            test_id, assigned = found
            old = existing.get(test_id)
            old_ci = old[1] if old else {}
            attempts = (list(old_ci.get("attempts") or []) + m.attempts)[-ATTEMPTS_MAX:]
            ci = {
                **facts,
                "test_name": m.names[0][:NAME_MAX] if m.names else None,
                "file": m.file,
                "attempts": attempts,
                "identities": _merge_identities(old_ci.get("identities"), m.identities),
            }
            values = {
                "status": m.status,
                "elapsed_s": math.ceil(m.elapsed) if m.elapsed > 0 else None,
                "log": _cut("\n\n".join(m.messages), LOG_MAX),
                "stack": _cut("\n\n".join(m.stacks), STACK_MAX),
                "attachments": m.attachments[:ATTACHMENTS_MAX],
                "ci": ci,
            }
            if old is not None:
                changed.append({"id": old[0], **values, "created_at": now})
            else:
                new_rows.append(
                    {
                        "test_id": test_id,
                        "source": "ci",
                        "defects": [],
                        "step_results": [],
                        "assigned_to": assigned,
                        "created_by": created_by,
                        "created_at": now,
                        "custom": dict(result_custom.get(case_id) or {}),
                        **values,
                    }
                )
            statuses.append({"id": test_id, "status": m.status, "tested_at": now})
            if m.status == "failed":
                failed.append(test_id)
        if new_rows:
            session.execute(insert(TestResult), new_rows)
        if changed:
            session.execute(update(TestResult), sorted(changed, key=lambda r: int(r["id"])))
        if statuses:
            # In id order, as the bulk result endpoint locks them: no deadlock between the two.
            session.execute(update(TestRunTest), sorted(statuses, key=lambda r: int(r["id"])))
        share = min(start + CHUNK, len(items)) / len(items) if items else 1.0
        _save_progress(session, imp, int(total * share))
    # Tests and results already loaded in this session would otherwise keep their old values.
    for obj in list(session.identity_map.values()):
        if isinstance(obj, TestRunTest | TestResult):
            session.expire(obj)
    return failed


def _result_defaults(
    session: Session, bp_id: int, case_ids: list[int]
) -> dict[int, dict[str, Any]]:
    """case id -> the `custom` of a new CI result of it (§15.8): the defaults of the dự án's
    result fields applying to the case's suite and template (a required one is not enforced: CI
    is never blocked by a field). One query for the fields and one for the cases; the values are
    worked out once per (suite, template)."""
    fields = tf.rows(session, bp_id, "result")
    if not fields or not case_ids:
        return {}
    kinds = {
        int(cid): (int(sid), template)
        for cid, sid, template in session.execute(
            select(TestCase.id, TestCase.suite_id, TestCase.template).where(
                TestCase.id.in_(sorted(set(case_ids)))
            )
        )
    }
    per_kind: dict[tuple[int, str | None], dict[str, Any]] = {}
    for kind in set(kinds.values()):
        per_kind[kind] = tf.clean_custom(
            session,
            bp_id,
            "result",
            None,
            suite_id=kind[0],
            template=kind[1],
            enforce_required=False,
            defaults=True,
            fields=fields,
        )
    return {cid: per_kind[kind] for cid, kind in kinds.items() if per_kind[kind]}


def _merge_identities(old: Any, new: dict[str, list[str]]) -> dict[str, list[str]]:
    """The attempts per test identity of a result updated again (same run, same commit)."""
    out: dict[str, list[str]] = {}
    if isinstance(old, dict):
        for ident, tried in old.items():
            if isinstance(tried, list):
                out[str(ident)] = [str(a) for a in tried]
    for ident, tried in new.items():
        out[ident] = (out.get(ident, []) + tried)[-ATTEMPTS_MAX:]
    return dict(itertools.islice(out.items(), IDENTITIES_MAX))


def _failed_events(session: Session, run: TestRun, test_ids: list[int]) -> None:
    """ONE `result.failed` webhook event (§15 G11) for the import: how many failed and the
    first ones (events.FAILED_LISTED), sent after the commit."""
    if not test_ids:
        return
    first = sorted(test_ids)[: events.FAILED_LISTED]
    tests = list(
        session.scalars(
            select(TestRunTest).where(TestRunTest.id.in_(first)).order_by(TestRunTest.id)
        )
    )
    events.results_failed(
        session,
        run,
        tests,
        status="failed",
        comment=None,
        defects=None,
        failed_count=len(set(test_ids)),
    )


def update_flaky(session: Session, case_ids: list[int]) -> None:
    """Recompute `flaky` of these cases from their last FLAKY_WINDOW CI results with a commit."""
    if not case_ids:
        return
    session.flush()
    ranked = (
        select(
            TestRunTest.case_id.label("case_id"),
            TestResult.status.label("status"),
            TestResult.ci.label("ci"),
            func.row_number()
            .over(
                partition_by=TestRunTest.case_id,
                order_by=(TestResult.created_at.desc(), TestResult.id.desc()),
            )
            .label("n"),
        )
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .where(
            TestRunTest.case_id.in_(case_ids),
            TestResult.source == "ci",
            TestResult.status.is_not(None),
            TestResult.ci["commit_sha"].astext.is_not(None),
        )
        .subquery()
    )
    history: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for case_id, status, ci, _n in session.execute(
        select(ranked.c.case_id, ranked.c.status, ranked.c.ci, ranked.c.n)
        .where(ranked.c.n <= FLAKY_WINDOW)
        .order_by(ranked.c.case_id, ranked.c.n.desc())
    ):
        history.setdefault(case_id, []).append((status, ci or {}))
    flaky = {cid for cid, rows in history.items() if is_flaky(rows)}
    session.execute(
        update(TestCase)
        .where(TestCase.id.in_(case_ids), TestCase.flaky.is_not(False))
        .where(TestCase.id.not_in(sorted(flaky) or [0]))
        .values(flaky=False)
        .execution_options(synchronize_session=False)
    )
    if flaky:
        session.execute(
            update(TestCase)
            .where(TestCase.id.in_(sorted(flaky)), TestCase.flaky.is_(False))
            .values(flaky=True)
            .execution_options(synchronize_session=False)
        )
    wanted = set(case_ids)
    for obj in list(session.identity_map.values()):
        if isinstance(obj, TestCase) and obj.id in wanted:
            session.expire(obj, ["flaky"])


def _identities(status: str, ci: dict[str, Any]) -> dict[str, list[str]]:
    """test identity -> its passed / failed attempts in one result. A result written before
    identities were kept counts as one identity (its test name)."""
    raw = ci.get("identities")
    if isinstance(raw, dict) and raw:
        return {
            str(ident): [a for a in tried if a in OUTCOMES]
            for ident, tried in raw.items()
            if isinstance(tried, list)
        }
    attempts = [a for a in ci.get("attempts") or [] if a in OUTCOMES] or [status]
    return {str(ci.get("test_name") or ""): attempts}


def is_flaky(rows: list[tuple[str, dict[str, Any]]]) -> bool:
    """`rows`: (status, ci) oldest first. Flaky when one test identity passed and failed on the
    same non-null commit (across results, or the retries of one result), or when the verdict of
    the results with a commit flipped FLAKY_FLIPS times. Two identities disagreeing (chromium
    passed, webkit failed) is a real failure, not flakiness."""
    seen: dict[tuple[str, str], set[str]] = {}
    sequence: list[str] = []
    for status, ci in rows:
        commit = ci.get("commit_sha")
        if not commit:
            continue
        for ident, tried in _identities(status, ci).items():
            seen.setdefault((str(commit), ident), set()).update(tried)
        sequence.append(status)
    if any(outcomes >= OUTCOMES for outcomes in seen.values()):
        return True
    flips = sum(1 for a, b in itertools.pairwise(sequence) if a != b)
    return flips >= FLAKY_FLIPS


def fail(session: Session, import_id: int, message: str) -> None:
    session.rollback()
    session.execute(
        update(TestCiImport)
        .where(TestCiImport.id == import_id)
        .values(status="failed", error=message[:500], finished_at=datetime.now(UTC), data=None)
    )
    session.commit()


def import_job(import_id: int) -> str:
    """Worker entry point (the default queue)."""
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        try:
            process(session, import_id)
        except ReportError as exc:
            fail(session, import_id, str(exc))
            return "failed"
        except Exception:
            log.exception("CI import failed", extra={"import_id": import_id})
            fail(session, import_id, "Lỗi khi xử lý báo cáo; thử lại sau.")
            raise
    return "done"


# -- reports ---------------------------------------------------------------------------------------

AUTOMATED_DAYS = 30
MR_CASES_MAX = 2_000


def automated_with_ci(session: Session, suite_ids: list[int], now: datetime | None = None) -> int:
    """ "% tự động" (§15 G1): live automated cases of these suites with a CI result in the last
    AUTOMATED_DAYS days."""
    since = (now or datetime.now(UTC)) - timedelta(days=AUTOMATED_DAYS)
    return int(
        session.scalar(
            select(func.count(func.distinct(TestCase.id)))
            .join(TestRunTest, TestRunTest.case_id == TestCase.id)
            .join(TestResult, TestResult.test_id == TestRunTest.id)
            .where(
                TestCase.suite_id.in_(suite_ids or [0]),
                TestCase.kind == "automated",
                TestCase.status != "obsolete",
                TestResult.source == "ci",
                TestResult.created_at >= since,
            )
        )
        or 0
    )


def mr_ci_reasons(session: Session, bp_id: int, mr_iid: int, repo: str | None) -> dict[int, str]:
    """case id -> "CI: Failed (R12, abc12345)": the latest CI result of each case sent with this
    MR's iid in the dự án and this repo. An upload that named no repo (`project`) only counts
    when the dự án has a single repo: with several, an iid alone does not say which MR it was."""
    project = TestResult.ci["project"].astext
    repos = int(
        session.scalar(
            select(func.count()).select_from(Project).where(Project.business_project_id == bp_id)
        )
        or 0
    )
    same_repo = [project == repo] if repo else []
    if repos == 1:
        same_repo.append(project.is_(None))
    if not same_repo:
        return {}
    rows = session.execute(
        select(
            TestRunTest.case_id,
            TestResult.status,
            TestRun.id,
            TestResult.ci["commit_sha"].astext,
        )
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(
            TestRun.business_project_id == bp_id,
            TestResult.source == "ci",
            TestResult.ci["mr_iid"].astext == str(mr_iid),
            TestRunTest.case_id.is_not(None),
            or_(*same_repo),
        )
        .ext(distinct_on(TestRunTest.case_id))
        .order_by(TestRunTest.case_id, TestResult.created_at.desc(), TestResult.id.desc())
        .limit(MR_CASES_MAX)
    ).all()
    out: dict[int, str] = {}
    for case_id, status, run_id, sha in rows:
        label = content.TEST_STATUS_LABEL.get(status or "", status or "")
        where = ", ".join(p for p in (content.key("run", run_id), (sha or "")[:8]) if p)
        out[int(case_id)] = f"CI: {label} ({where})"
    return out
