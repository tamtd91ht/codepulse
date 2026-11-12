"""Test cases in and out of spreadsheets (docs/specs/30 §4, §5, §13.7): the team's Google Sheet
(read through its .xlsx export, links in cells kept), an uploaded .xlsx or .csv, a TestRail CSV
(several rows a case: one per step); written back as .xlsx / .csv with the columns a test run on
Google Sheet needs, or as a TestRail CSV.

Reading guesses which row holds the headers and which column is which field from the header
words (accents folded); the person corrects the guess before importing.
"""

import csv
import io
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assistant import importers
from app.assistant.knowledge import fold
from app.models import TestMilestone, TestSharedStep
from app.testcases import content
from app.testcases import service as tc

MAX_ROWS = importers.MAX_SHEET_ROWS
MAX_COLS = importers.MAX_SHEET_COLS
MAX_SHEETS = 30
MAX_FILE_BYTES = importers.MAX_FILE_BYTES
HEADER_SCAN = 10
# The fields a column can be (docs/specs/30 §4, §14.1), in the order shown on the mapping screen.
MAP_FIELDS = (
    "case_id",
    "code",
    "section",
    "title",
    "template",
    "type",
    "priority",
    "estimate",
    "milestone",
    "preconditions",
    "steps",
    "expected",
    "step_content",
    "step_expected",
    "test_data",
    "kind",
    "tags",
    "jira_keys",
    "notes",
    "automation",
    "code_paths",
)
FIELD_LABEL = {
    "case_id": "ID",
    "code": "Mã gốc",
    "section": "Thư mục",
    "title": "Tiêu đề",
    "template": "Template",
    "type": "Type",
    "priority": "Ưu tiên",
    "estimate": "Ước tính",
    "milestone": "Milestone",
    "preconditions": "Tiền điều kiện",
    "steps": "Các bước",
    "expected": "Kết quả mong đợi",
    "step_content": "Bước (mỗi dòng một bước)",
    "step_expected": "Kết quả của bước (mỗi dòng một bước)",
    "test_data": "Dữ liệu test",
    "kind": "Loại",
    "tags": "Nhãn",
    "jira_keys": "Jira",
    "notes": "Ghi chú",
    "automation": "Test tự động",
    "code_paths": "Vùng code",
    "status": "Trạng thái",
}
# A test run's columns: left out on import, empty on export (a run on Google Sheet).
RUN_COLUMNS = ("Kết quả thực tế", "Trạng thái chạy", "Người test", "Ngày test")
# TestRail's CSV (docs/specs/30 §13.7), "multi-row": a steps case writes one row per step.
TESTRAIL_COLUMNS = (
    "ID",
    "Title",
    "Section",
    "Template",
    "Type",
    "Priority",
    "Estimate",
    "References",
    "Milestone",
    "Preconditions",
    "Steps",
    "Expected Result",
    "Steps (Step)",
    "Steps (Expected Result)",
)
_TESTRAIL_FIELD = dict(
    zip(
        TESTRAIL_COLUMNS,
        (
            "case_id",
            "title",
            "section",
            "template",
            "type",
            "priority",
            "estimate",
            "jira_keys",
            "milestone",
            "preconditions",
            "steps",
            "expected",
            "step_content",
            "step_expected",
        ),
        strict=True,
    )
)
TESTRAIL_PRIORITY = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}

# Columns of our own export that are not fields (docs/specs/30 §5): skipped on import.
_EXPORT_ONLY = ("Bộ testcase", "Lý do cần test lại")
# Header words (folded) -> field, the first rule that matches wins; None: a column to skip.
_RULES: tuple[tuple[re.Pattern[str], str | None], ...] = tuple(
    (re.compile(p), f)
    for p, f in (
        (r"^(stt|no|so tt|#)$", None),
        (
            r"thuc te|actual|ket qua test|test result|^pass|^fail|nguoi (test|thuc hien|kiem tra)"
            r"|tester|ngay|date|trang thai|^status|run status|build|version|phien ban",
            None,
        ),
        (
            r"^(id|ma|ma tc|ma test ?case|tc ?id|test ?case ?id|tc ?no|case ?id|ma kich ban|"
            r"ma testcase|tc ?code|test ?id|ma goc)$",
            "code",
        ),
        (r"^steps? (step|content|noi dung)$|^buoc tung dong|^noi dung buoc", "step_content"),
        (r"^steps? (expected|ket qua)|^ket qua (cua |tung )?buoc", "step_expected"),
        (r"^template$|^mau testcase|^dang testcase", "template"),
        (r"^(type|case type|test type|loai test ?case|kieu test ?case)$", "type"),
        (r"estimate|uoc tinh|thoi gian du kien", "estimate"),
        (r"milestone|cot moc", "milestone"),
        (r"ket qua (mong|du kien|ky vong|can dat)|expected|ket qua$", "expected"),
        (r"dieu kien|pre-? ?condition|prerequisite", "preconditions"),
        (r"buoc|thao tac|steps?\b|procedure|cach thuc hien|cac thuc hien", "steps"),
        (r"du lieu|test data|^data$|^input", "test_data"),
        (r"uu tien|muc do|priority|severity", "priority"),
        (r"file test|script|automation (path|file|test)|duong dan test|test tu dong", "automation"),
        (r"vung code|code ?paths?|duong dan code|file code", "code_paths"),
        (r"tu dong|automat|^auto\b|manual", "kind"),
        (r"nhan|tag|label|^loai|type", "tags"),
        (r"jira|story|issue|ticket|yeu cau|requirement|^references?$|^refs?$", "jira_keys"),
        (r"ghi chu|note|comment|chu thich", "notes"),
        (
            r"chuc nang|module|man hinh|nhom|hang muc|feature|function|category|tinh nang|menu"
            r"|section|thu muc|folder",
            "section",
        ),
        (
            r"ten|tieu de|mo ta|muc dich|kich ban|test ?case|title|summary|scenario|description"
            r"|noi dung|objective|truong hop",
            "title",
        ),
    )
)


class SheetError(ValueError):
    """Shown to the user (Vietnamese)."""


@dataclass
class Sheet:
    name: str
    rows: list[list[str]]
    header_row: int = -1  # -1: no header row found, every row is data
    mapping: dict[str, int] = field(default_factory=dict)


# -- reading ---------------------------------------------------------------------------------------


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return tc.CONTROL.sub("", text).strip()


def _trim(rows: list[list[str]]) -> list[list[str]]:
    """Without empty rows at the end and empty columns on the right."""
    while rows and not any(rows[-1]):
        rows.pop()
    width = max((max((i + 1 for i, v in enumerate(r) if v), default=0) for r in rows), default=0)
    return [(r + [""] * width)[:width] for r in rows]


def read_xlsx(data: bytes) -> list[Sheet]:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    try:
        importers._zip_ok(data, ".xlsx")
    except importers.ImportError_ as exc:
        raise SheetError(str(exc)) from None
    links = importers._xlsx_links(data)
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several kinds for a bad file
        raise SheetError("không phải file .xlsx hợp lệ") from exc
    out: list[Sheet] = []
    try:
        for sheet in book.worksheets[:MAX_SHEETS]:
            linked = links.get(sheet.title, {})
            rows: list[list[str]] = []
            for r, row in enumerate(
                sheet.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True), start=1
            ):
                cells = [_cell(v) for v in row]
                if linked:
                    cells = [
                        importers.md_link(v, linked.get(f"{get_column_letter(c)}{r}")) if v else v
                        for c, v in enumerate(cells, start=1)
                    ]
                rows.append(cells)
            rows = _trim(rows)
            if rows:
                out.append(Sheet(name=sheet.title[:200], rows=rows))
    finally:
        book.close()
    return out


def read_csv(data: bytes, name: str = "CSV") -> list[Sheet]:
    for encoding in ("utf-8-sig", "cp1258", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:5000]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    try:
        for i, row in enumerate(csv.reader(io.StringIO(text), dialect)):
            if i >= MAX_ROWS:
                break
            rows.append([_unguard(_cell(v)) for v in row[:MAX_COLS]])
    except csv.Error as exc:
        raise SheetError(f"file CSV không đọc được ({exc})") from None
    rows = _trim(rows)
    return [Sheet(name=name[:200], rows=rows)] if rows else []


def _unguard(text: str) -> str:
    """A CSV cell written by `_safe` ("'=…") as it was."""
    return text[1:] if text[:1] == "'" and text[1:2] in ("=", "+", "-", "@") else text


def read_file(name: str, data: bytes) -> list[Sheet]:
    if len(data) > MAX_FILE_BYTES:
        raise SheetError(f"file lớn hơn {MAX_FILE_BYTES // (1024 * 1024)} MB")
    lower = name.lower()
    if lower.endswith(".xlsx"):
        return read_xlsx(data)
    if lower.endswith((".csv", ".tsv", ".txt")):
        return read_csv(data, name.rsplit(".", 1)[0] or "CSV")
    raise SheetError("chỉ nhận file .xlsx, .csv hoặc link Google Sheets")


def fetch_google(url: str, allowed_hosts: tuple[str, ...]) -> tuple[str, list[Sheet]]:
    """(the spreadsheet's name, its tabs) of a Google Sheets link shared publicly."""
    google = importers.google_export(url)
    if google is None or google[1] != ".xlsx":
        raise SheetError("chỉ nhận link Google Sheets (docs.google.com/spreadsheets/…)")
    http = importers._client(allowed_hosts)
    deadline = time.monotonic() + importers.URL_DEADLINE_S
    try:
        final, kind, body, headers = importers._fetch(
            http, google[0], importers.MAX_EXPORT_BYTES, deadline
        )
    except httpx.HTTPError as exc:
        cause = exc.__cause__ or exc.__context__
        if isinstance(cause, importers.ImportError_):
            raise SheetError(_private(str(cause))) from None
        raise SheetError(f"không tải được ({exc.__class__.__name__})") from exc
    except importers.ImportError_ as exc:
        raise SheetError(_private(str(exc))) from None
    finally:
        http.close()
    if urlsplit(final).hostname in importers._SIGN_IN or kind.startswith("text/html"):
        raise SheetError(importers._GOOGLE_PRIVATE)
    return importers._export_name(headers, "Google Sheets")[:200], read_xlsx(body)


def _private(message: str) -> str:
    return importers._GOOGLE_PRIVATE if re.search(r"\b(401|403|404)\b", message) else message


# -- guessing the columns --------------------------------------------------------------------------


_EXACT: dict[str, str | None] = {
    fold(label): name for name, label in FIELD_LABEL.items() if name != "status"
}
_EXACT |= {fold(label): name for label, name in _TESTRAIL_FIELD.items()}
_EXACT |= {fold("Mã"): "code", fold("Chức năng"): "section"}  # our exports before §13
_EXACT |= {fold(label): None for label in (*_EXPORT_ONLY, FIELD_LABEL["status"], *RUN_COLUMNS)}


def field_of(header: str) -> str | None:
    exact = fold(" ".join(header.split()))
    if exact in _EXACT:
        return _EXACT[exact]
    h = re.sub(r"[^a-z0-9#\- ]+", " ", fold(header)).strip()
    h = re.sub(r"\s+", " ", h)
    if not h:
        return None
    for pattern, name in _RULES:
        if pattern.search(h):
            return name
    return None


def _mapping(row: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for i, cell in enumerate(row):
        if not cell or len(cell) > 80:
            continue
        name = field_of(cell)
        if name and name not in out:
            out[name] = i
    return out


def guess(sheet: Sheet) -> Sheet:
    """The header row (of the first HEADER_SCAN, the one naming most fields, at least two of
    them) and its mapping; none found: every row is data, nothing mapped."""
    best, best_map = -1, dict[str, int]()
    for i, row in enumerate(sheet.rows[:HEADER_SCAN]):
        found = _mapping(row)
        if len(found) >= 2 and len(found) > len(best_map):
            best, best_map = i, found
    sheet.header_row, sheet.mapping = best, best_map
    return sheet


# -- values ----------------------------------------------------------------------------------------

_PRIORITY = (
    (re.compile(r"critical|blocker|p0|rat cao|khan cap|nghiem trong|highest|urgent"), "critical"),
    (re.compile(r"high|^cao|p1|quan trong"), "high"),
    (re.compile(r"low|thap|p3|p4|lowest|minor"), "low"),
)
_AUTO = re.compile(r"^(auto|automation|automated|tu dong|yes|y|x|co|true|1|done|da tu dong)\b")
_MANUAL = re.compile(r"^(manual|thu cong|no|khong)\b")
_TYPE_WORDS = {
    **{k: k for k in content.TYPE_LABEL},
    **{fold(v): k for k, v in content.TYPE_LABEL.items()},
    "sanity": "smoke",
    "smoke test": "smoke",
    "chuc nang": "functional",
    "hieu nang": "performance",
    "bao mat": "security",
    "hoi quy": "regression",
    "chap nhan": "acceptance",
    "tuong thich": "compatibility",
    "kha dung": "usability",
    "giao dien": "usability",
    "ui": "usability",
    "uat": "acceptance",
    "khac": "other",
}


def priority_of(value: str) -> str:
    v = fold(value).strip()
    for pattern, name in _PRIORITY:
        if pattern.search(v):
            return name
    return "medium"


def kind_of(value: str) -> str:
    return "automated" if _AUTO.match(fold(value).strip()) else "manual"


def type_of(value: str) -> str | None:
    """A TestRail case type from its name ("Smoke & Sanity", "Functional", "bảo mật"); None:
    not one."""
    v = " ".join(fold(value).split())
    return _TYPE_WORDS.get(v) or _TYPE_WORDS.get(v.split(" ")[0] if v else "")


def template_of(value: str) -> str | None:
    v = fold(value)
    if "step" in v or "buoc" in v:
        return "steps"
    if "text" in v or "chu" in v or "van ban" in v:
        return "text"
    return None


_NUMBER = re.compile(r"^\s*(?:(?:buoc|bước|step)\s*)?(\d{1,3})[.):]\s?(.*)$", re.I)


def numbered(text: str | None) -> list[tuple[int, str]]:
    """(number, text) of "1. a\\n2. b"; lines without a number go on with the item above; a text
    without any number is one item (1)."""
    items: list[tuple[int, str]] = []
    for line in (text or "").strip().split("\n"):
        m = _NUMBER.match(line)
        if m:
            items.append((int(m.group(1)), m.group(2).strip()))
        elif items:
            n, prev = items[-1]
            items[-1] = (n, f"{prev}\n{line.rstrip()}")
        elif line.strip():
            items.append((1, line.strip()))
    return [(n, t.strip()) for n, t in items]


def steps_of_numbered(steps: str | None, expected: str | None) -> list[dict[str, str]]:
    """A steps template written as numbered texts (our own export): the expected result of
    step n is the item numbered n (a text without numbers goes with the last step)."""
    items = numbered(steps)
    if not items:
        return []
    results = numbered(expected)
    out = [{"content": t, "expected": ""} for _, t in items]
    if results and not _NUMBER.match((expected or "").strip().split("\n")[0]):
        out[-1]["expected"] = (expected or "").strip()
        return out
    position = {n: i for i, (n, _) in enumerate(items)}
    for n, t in results:
        if n in position:
            out[position[n]]["expected"] = t
    return out


def render_steps(steps: list[dict[str, Any]]) -> tuple[str, str]:
    """(steps, expected) as numbered texts; a step without an expected result has no line."""
    lines = [f"{i}. {s.get('content') or ''}" for i, s in enumerate(steps, start=1)]
    results = [
        f"{i}. {s.get('expected')}" for i, s in enumerate(steps, start=1) if s.get("expected")
    ]
    return "\n".join(lines), "\n".join(results)


def _lone(row: list[str]) -> int | None:
    """The column of the only filled cell of a short row, else None."""
    filled = [i for i, c in enumerate(row) if c.strip()]
    return filled[0] if len(filled) == 1 and len(row[filled[0]]) < 120 else None


@dataclass
class Mapped:
    """Cleaned case values (service.clean) plus what the import resolves: `case_id` (a "C123"
    in the ID column), `section` (a path "A > B"; "" = the suite's root), `milestone` (a name)."""

    cases: list[dict[str, Any]]
    skipped: int = 0  # rows with content but no title


def to_cases(rows: list[list[str]], header_row: int, mapping: dict[str, int]) -> Mapped:
    """Case values of the data rows. An empty section takes the one above (merged cells) unless
    the sheet is an export (a Template column, or C<id> keys in its ID column); a row naming
    only a section is a group row; a row with only step columns adds a step to the case above
    (TestRail)."""
    if "title" not in mapping:
        raise SheetError("chưa chọn cột Tiêu đề")
    out = Mapped(cases=[])
    section = ""
    pending: list[tuple[dict[str, Any], list[dict[str, str]], str]] = []

    def get(row: list[str], name: str) -> str:
        i = mapping.get(name)
        return row[i].strip() if i is not None and 0 <= i < len(row) else ""

    machine = "template" in mapping or any(
        content.case_id_of(get(r, "case_id")) is not None for r in rows[header_row + 1 :]
    )
    used = set(mapping.values())
    for row in rows[header_row + 1 :]:
        if not any(c.strip() for c in row):
            continue
        sec = get(row, "section")
        title = get(row, "title")
        step = (get(row, "step_content"), get(row, "step_expected"))
        if not title and pending and any(step) and not get(row, "case_id"):
            pending[-1][1].append({"content": step[0], "expected": step[1]})
            continue
        others = [c for i, c in enumerate(row) if c.strip() and i in used]
        if not title:
            if sec and len(others) == 1:
                section = sec  # a group row
            elif not sec and (lone := _lone(row)) is not None and (lone == 0 or lone not in used):
                section = row[lone].strip()[: tc.SECTION_MAX]  # a merged group title row
            else:
                out.skipped += 1
            continue
        if sec or machine:
            section = sec
        # Only the columns mapped: importing again never resets what the sheet does not hold.
        values: dict[str, Any] = {"title": title}
        for column in ("case_id", "code"):
            raw = get(row, column)
            if (case_id := content.case_id_of(raw)) is not None:
                values["case_id"] = case_id
            elif raw and (column == "code" or "code" not in mapping):
                values["code"] = raw
            elif column == "code":
                values.setdefault("code", "")
        if "section" in mapping or section:
            values["section"] = section
        for name in (
            "preconditions",
            "steps",
            "test_data",
            "expected",
            "notes",
            "automation",
            "jira_keys",
            "code_paths",
        ):
            if name in mapping:
                values[name] = get(row, name)
        if "priority" in mapping:
            values["priority"] = priority_of(get(row, "priority"))
        if "type" in mapping and get(row, "type"):
            values["type"] = type_of(get(row, "type")) or "other"
        if "estimate" in mapping:
            values["estimate_s"] = content.parse_estimate(get(row, "estimate"))
        if "milestone" in mapping:
            values["milestone"] = get(row, "milestone")[: tc.NAME_MAX]
        kind = get(row, "kind")
        if kind and not (_AUTO.match(fold(kind)) or _MANUAL.match(fold(kind))):
            # "Loại" holding Smoke / Regression: a tag (and the type), not the kind.
            values["tags"] = list(tc._list(kind, tc.TAG_MAX))
            if "type" not in values and (found := type_of(kind)):
                values["type"] = found
            kind = ""
        if "tags" in mapping:
            tags = list(tc._list(get(row, "tags"), tc.TAG_MAX))
            # "Loại" holding Auto / Manual is the kind, not a tag.
            for t in list(tags):
                if _AUTO.match(fold(t)) or _MANUAL.match(fold(t)):
                    kind = kind or t
                    tags.remove(t)
            values["tags"] = list(dict.fromkeys([*values.get("tags", []), *tags]))
        if values.get("automation"):
            values["kind"] = "automated"
        elif kind or "kind" in mapping:
            values["kind"] = kind_of(kind) if kind else "manual"
        steps = [{"content": step[0], "expected": step[1]}] if any(step) else []
        pending.append((values, steps, get(row, "template")))

    for values, steps, template in pending:
        chosen = template_of(template) if template else None
        if steps:
            values["template"] = "steps"
            values["steps_list"] = steps
            for name in ("steps", "expected"):
                if not values.get(name):
                    values.pop(name, None)  # TestRail leaves them empty for a steps case
        elif chosen == "steps":
            values["template"] = "steps"
            values["steps_list"] = steps_of_numbered(
                values.pop("steps", None), values.pop("expected", None)
            )
        elif (
            chosen is None
            and (pairs := content.steps_from_text(values.get("steps"), values.get("expected")))
            and len(content.split_numbered(values.get("expected"))) == len(pairs)
        ):
            values["template"] = "steps"
            values["steps_list"] = pairs
            values.pop("steps", None)
            values.pop("expected", None)
        elif chosen == "text":
            values["template"] = "text"
        extras: dict[str, Any] = {
            k: values.pop(k) for k in ("case_id", "section", "milestone") if k in values
        }
        try:
            cleaned = tc.clean(values)
        except tc.FieldError:
            out.skipped += 1
            continue
        out.cases.append({**cleaned, **extras})
    return out


# -- writing ---------------------------------------------------------------------------------------

EXPORT_FIELDS = (
    "case_id",
    "code",
    "section",
    "title",
    "template",
    "type",
    "preconditions",
    "steps",
    "test_data",
    "expected",
    "priority",
    "estimate",
    "milestone",
    "kind",
    "tags",
    "jira_keys",
    "status",
    "notes",
    "automation",
    "code_paths",
)
_WIDTHS = {
    "case_id": 9,
    "code": 12,
    "section": 26,
    "title": 48,
    "template": 18,
    "type": 14,
    "preconditions": 30,
    "steps": 48,
    "test_data": 24,
    "expected": 40,
    "priority": 12,
    "estimate": 10,
    "milestone": 16,
    "kind": 11,
    "tags": 16,
    "jira_keys": 14,
    "status": 11,
    "notes": 24,
    "automation": 24,
    "code_paths": 24,
}


@dataclass
class Context:
    """What writing a case needs besides the case: section paths, shared steps, milestones."""

    paths: dict[int, str] = field(default_factory=dict)
    shared: dict[int, TestSharedStep] = field(default_factory=dict)
    milestones: dict[int, str] = field(default_factory=dict)

    def steps(self, case: Any) -> list[dict[str, Any]]:
        return content.expand_steps(case.steps_list, self.shared)

    def section(self, case: Any) -> str:
        return self.paths.get(case.section_id, "") if case.section_id else ""


def context_for(session: Session, cases: Iterable[Any]) -> Context:
    listed = list(cases)
    milestone_ids = {c.milestone_id for c in listed if c.milestone_id}
    return Context(
        paths=content.section_paths(session, {c.suite_id for c in listed}),
        shared=content.shared_map(
            session, [i for c in listed for i in content.shared_ids(c.steps_list)]
        ),
        milestones=dict(
            session.execute(
                select(TestMilestone.id, TestMilestone.name).where(
                    TestMilestone.id.in_(milestone_ids or {0})
                )
            ).all()
        ),
    )


def _value(case: Any, name: str, ctx: Context) -> str:
    if name == "case_id":
        return content.key("case", case.id) or ""
    if name == "section":
        return ctx.section(case)
    if name == "template":
        return content.TEMPLATE_LABEL.get(case.template or "text", "")
    if name == "type":
        return content.TYPE_LABEL.get(case.type or "functional", str(case.type))
    if name == "estimate":
        return content.format_estimate(case.estimate_s) or ""
    if name == "milestone":
        return ctx.milestones.get(case.milestone_id, "") if case.milestone_id else ""
    if name in ("steps", "expected") and case.template == "steps":
        steps, expected = render_steps(ctx.steps(case))
        return steps if name == "steps" else expected
    value: Any = getattr(case, name)
    if name == "priority":
        return tc.PRIORITY_LABEL.get(value, str(value))
    if name == "kind":
        return tc.KIND_LABEL.get(value, str(value))
    if name == "status":
        return tc.STATUS_LABEL.get(value, str(value))
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value or "")


def _safe(text: str) -> str:
    """A CSV cell a spreadsheet will not run as a formula."""
    text = tc.CONTROL.sub("", text)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _sheet_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:28] or "Testcase"
    out, n = base, 1
    while out.lower() in taken:
        n += 1
        out = f"{base[:25]} ({n})"
    taken.add(out.lower())
    return out


def write_xlsx(
    groups: list[tuple[str, list[Any]]],
    extra: dict[int, str] | None = None,
    ctx: Context | None = None,
) -> bytes:
    """One tab per suite; `extra` adds a "Lý do" column (case id -> why it is listed). Every
    cell is written as text: nothing in it runs as a formula."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import TYPE_STRING
    from openpyxl.styles import Alignment, Font, PatternFill

    ctx = ctx or Context()
    book = Workbook()
    book.remove(book.active)  # type: ignore[arg-type]
    taken: set[str] = set()
    headers = [FIELD_LABEL[f] for f in EXPORT_FIELDS]
    if extra is not None:
        headers.append("Lý do cần test lại")
    headers += list(RUN_COLUMNS)
    widths = (
        [_WIDTHS[f] for f in EXPORT_FIELDS] + ([36] if extra is not None else []) + [30, 16, 16, 12]
    )
    for name, cases in groups or [("Testcase", [])]:
        ws = book.create_sheet(_sheet_name(name, taken))
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F3A5F")
            c.alignment = Alignment(vertical="center", wrap_text=True)
        for r, case in enumerate(cases, start=2):
            row = [_value(case, f, ctx) for f in EXPORT_FIELDS]
            if extra is not None:
                row.append(extra.get(case.id, ""))
            for col, value in enumerate(row, start=1):
                cell = ws.cell(r, col)
                cell.value = tc.CONTROL.sub("", value)
                cell.data_type = TYPE_STRING
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


def write_csv(
    groups: list[tuple[str, list[Any]]],
    extra: dict[int, str] | None = None,
    ctx: Context | None = None,
) -> bytes:
    ctx = ctx or Context()
    buf = io.StringIO()
    w = csv.writer(buf)
    head = ["Bộ testcase", *(FIELD_LABEL[f] for f in EXPORT_FIELDS)]
    if extra is not None:
        head.append("Lý do cần test lại")
    w.writerow(head + list(RUN_COLUMNS))
    for name, cases in groups:
        for case in cases:
            row = [_safe(name), *(_safe(_value(case, f, ctx)) for f in EXPORT_FIELDS)]
            if extra is not None:
                row.append(_safe(extra.get(case.id, "")))
            w.writerow(row + [""] * len(RUN_COLUMNS))
    return buf.getvalue().encode("utf-8-sig")


def write_testrail(groups: list[tuple[str, list[Any]]], ctx: Context | None = None) -> bytes:
    """TestRail's CSV: a steps case writes its first step on its own row, each next step on a
    row of its own with the case's columns empty. Several suites: the suite is the top section."""
    ctx = ctx or Context()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TESTRAIL_COLUMNS)
    several = len(groups) > 1
    blank = [""] * (len(TESTRAIL_COLUMNS) - 2)
    for name, cases in groups:
        for case in cases:
            section = ctx.section(case)
            if several:
                section = content.PATH_SEP.join(p for p in (name, section) if p)
            steps = ctx.steps(case) if case.template == "steps" else []
            first = steps[0] if steps else {}
            row = [
                content.key("case", case.id) or "",
                case.title,
                section,
                content.TEMPLATE_LABEL.get(case.template or "text", ""),
                content.TYPE_LABEL.get(case.type or "functional", ""),
                TESTRAIL_PRIORITY.get(case.priority, "Medium"),
                content.format_estimate(case.estimate_s) or "",
                ", ".join(case.jira_keys or []),
                ctx.milestones.get(case.milestone_id, "") if case.milestone_id else "",
                case.preconditions or "",
                "" if steps else case.steps or "",
                "" if steps else case.expected or "",
                str(first.get("content") or ""),
                str(first.get("expected") or ""),
            ]
            w.writerow([_safe(v) for v in row])
            for step in steps[1:]:
                w.writerow(
                    [
                        *blank,
                        _safe(str(step.get("content") or "")),
                        _safe(str(step.get("expected") or "")),
                    ]
                )
    return buf.getvalue().encode("utf-8-sig")


def template_file(fmt: str) -> bytes:
    """A sample to fill in and import (GET /testcases/import/template)."""
    from app.models import TestCase

    ctx = Context(paths={1: "Đăng nhập > Web", 2: "Đăng nhập"})
    samples = [
        TestCase(
            section_id=1,
            title="Đăng nhập với tài khoản hợp lệ",
            template="steps",
            steps_list=[
                {"content": "Mở trang đăng nhập", "expected": "Hiện form đăng nhập"},
                {"content": "Nhập email và mật khẩu đúng, bấm Đăng nhập", "expected": ""},
                {"content": "Quan sát trang sau khi đăng nhập", "expected": "Vào trang chủ"},
            ],
            type="functional",
            priority="high",
            estimate_s=120,
            preconditions="Có tài khoản đang hoạt động",
            jira_keys=["ABC-1"],
            tags=["smoke"],
            kind="manual",
            status="draft",
            code_paths=[],
        ),
        TestCase(
            section_id=2,
            title="Đăng nhập sai mật khẩu",
            template="text",
            steps_list=[],
            type="functional",
            priority="medium",
            preconditions=None,
            steps="1. Nhập mật khẩu sai\n2. Bấm Đăng nhập",
            expected="Báo lỗi sai mật khẩu, không đăng nhập",
            jira_keys=[],
            tags=[],
            kind="manual",
            status="draft",
            code_paths=[],
        ),
    ]
    if fmt == "testrail":
        return write_testrail([("Testcase", samples)], ctx)
    return write_xlsx([("Testcase", samples)], None, ctx)
