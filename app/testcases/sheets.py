"""Test cases in and out of spreadsheets (docs/specs/30 §4, §5): the team's Google Sheet (read
through its .xlsx export, links in cells kept), an uploaded .xlsx or .csv; written back as .xlsx /
.csv with the columns a test run on Google Sheet needs.

Reading guesses which row holds the headers and which column is which field from the header
words (accents folded); the person corrects the guess before importing.
"""

import csv
import io
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.assistant import importers
from app.assistant.knowledge import fold
from app.testcases import service as tc

MAX_ROWS = importers.MAX_SHEET_ROWS
MAX_COLS = importers.MAX_SHEET_COLS
MAX_SHEETS = 30
MAX_FILE_BYTES = importers.MAX_FILE_BYTES
HEADER_SCAN = 10
# The fields a column can be (docs/specs/30 §4), in the order shown on the mapping screen.
MAP_FIELDS = (
    "code",
    "section",
    "title",
    "preconditions",
    "steps",
    "test_data",
    "expected",
    "priority",
    "kind",
    "tags",
    "jira_keys",
    "notes",
    "automation",
    "code_paths",
)
FIELD_LABEL = {
    "code": "Mã",
    "section": "Chức năng",
    "title": "Tiêu đề",
    "preconditions": "Tiền điều kiện",
    "steps": "Các bước",
    "test_data": "Dữ liệu test",
    "expected": "Kết quả mong đợi",
    "priority": "Ưu tiên",
    "kind": "Loại",
    "tags": "Nhãn",
    "jira_keys": "Jira",
    "notes": "Ghi chú",
    "automation": "Test tự động",
    "code_paths": "Vùng code",
    "status": "Trạng thái",
}
# A test run's columns: left out on import (CodePulse does not run tests), empty on export.
RUN_COLUMNS = ("Kết quả thực tế", "Trạng thái chạy", "Người test", "Ngày test")

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
            r"ma testcase|tc ?code|test ?id)$",
            "code",
        ),
        (r"ket qua (mong|du kien|ky vong|can dat)|expected|ket qua$", "expected"),
        (r"dieu kien|pre-? ?condition|prerequisite", "preconditions"),
        (r"buoc|thao tac|steps?\b|procedure|cach thuc hien|cac thuc hien", "steps"),
        (r"du lieu|test data|^data$|^input", "test_data"),
        (r"uu tien|muc do|priority|severity", "priority"),
        (r"file test|script|automation (path|file|test)|duong dan test|test tu dong", "automation"),
        (r"vung code|code ?paths?|duong dan code|file code", "code_paths"),
        (r"tu dong|automat|^auto\b|manual", "kind"),
        (r"nhan|tag|label|^loai|type", "tags"),
        (r"jira|story|issue|ticket|yeu cau|requirement", "jira_keys"),
        (r"ghi chu|note|comment|chu thich", "notes"),
        (
            r"chuc nang|module|man hinh|nhom|hang muc|feature|function|category|tinh nang|menu",
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


def priority_of(value: str) -> str:
    v = fold(value).strip()
    for pattern, name in _PRIORITY:
        if pattern.search(v):
            return name
    return "medium"


def kind_of(value: str) -> str:
    return "automated" if _AUTO.match(fold(value).strip()) else "manual"


def _lone(row: list[str]) -> int | None:
    """The column of the only filled cell of a short row, else None."""
    filled = [i for i, c in enumerate(row) if c.strip()]
    return filled[0] if len(filled) == 1 and len(row[filled[0]]) < 120 else None


@dataclass
class Mapped:
    cases: list[dict[str, Any]]
    skipped: int = 0  # rows with content but no title


def to_cases(rows: list[list[str]], header_row: int, mapping: dict[str, int]) -> Mapped:
    """Cleaned case values of the data rows. An empty section takes the one above (merged cells);
    a row naming only a section is a group row."""
    if "title" not in mapping:
        raise SheetError("chưa chọn cột Tiêu đề")
    out = Mapped(cases=[])
    section = ""

    def get(row: list[str], name: str) -> str:
        i = mapping.get(name)
        return row[i].strip() if i is not None and 0 <= i < len(row) else ""

    used = set(mapping.values())
    for row in rows[header_row + 1 :]:
        if not any(c.strip() for c in row):
            continue
        sec = get(row, "section")
        title = get(row, "title")
        others = [c for i, c in enumerate(row) if c.strip() and i in used]
        if not title:
            if sec and len(others) == 1:
                section = sec  # a group row
            elif not sec and (lone := _lone(row)) is not None and (lone == 0 or lone not in used):
                section = row[lone].strip()[: tc.SECTION_MAX]  # a merged group title row
            else:
                out.skipped += 1
            continue
        if sec:
            section = sec
        # Only the columns mapped: importing again never resets what the sheet does not hold.
        values: dict[str, Any] = {"code": get(row, "code"), "title": title}
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
        kind = get(row, "kind")
        if kind and not (_AUTO.match(fold(kind)) or _MANUAL.match(fold(kind))):
            # "Loại" holding Smoke / Regression: a tag, not the kind.
            values["tags"] = list(tc._list(kind, tc.TAG_MAX))
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
        try:
            out.cases.append(tc.clean(values))
        except tc.FieldError:
            out.skipped += 1
    return out


# -- writing ---------------------------------------------------------------------------------------

EXPORT_FIELDS = (
    "code",
    "section",
    "title",
    "preconditions",
    "steps",
    "test_data",
    "expected",
    "priority",
    "kind",
    "tags",
    "jira_keys",
    "status",
    "notes",
    "automation",
    "code_paths",
)


def _value(case: Any, name: str) -> str:
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
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _sheet_name(name: str, taken: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:28] or "Testcase"
    out, n = base, 1
    while out.lower() in taken:
        n += 1
        out = f"{base[:25]} ({n})"
    taken.add(out.lower())
    return out


def write_xlsx(groups: list[tuple[str, list[Any]]], extra: dict[int, str] | None = None) -> bytes:
    """One tab per suite; `extra` adds a "Lý do" column (case id -> why it is listed). Every
    cell is written as text: nothing in it runs as a formula."""
    from openpyxl import Workbook
    from openpyxl.cell.cell import TYPE_STRING
    from openpyxl.styles import Alignment, Font, PatternFill

    book = Workbook()
    book.remove(book.active)  # type: ignore[arg-type]
    taken: set[str] = set()
    headers = [FIELD_LABEL[f] for f in EXPORT_FIELDS]
    if extra is not None:
        headers.append("Lý do cần test lại")
    headers += list(RUN_COLUMNS)
    widths = (
        [12, 22, 48, 30, 48, 24, 40, 12, 11, 16, 14, 11, 24, 24, 24]
        + ([36] if extra is not None else [])
        + [30, 16, 16, 12]
    )
    for name, cases in groups or [("Testcase", [])]:
        ws = book.create_sheet(_sheet_name(name, taken))
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="1F3A5F")
            c.alignment = Alignment(vertical="center", wrap_text=True)
        for r, case in enumerate(cases, start=2):
            row = [_value(case, f) for f in EXPORT_FIELDS]
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


def write_csv(groups: list[tuple[str, list[Any]]], extra: dict[int, str] | None = None) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    head = ["Bộ testcase", *(FIELD_LABEL[f] for f in EXPORT_FIELDS)]
    if extra is not None:
        head.append("Lý do cần test lại")
    w.writerow(head + list(RUN_COLUMNS))
    for name, cases in groups:
        for case in cases:
            row = [_safe(name), *(_safe(_value(case, f)) for f in EXPORT_FIELDS)]
            if extra is not None:
                row.append(_safe(extra.get(case.id, "")))
            w.writerow(row + [""] * len(RUN_COLUMNS))
    return buf.getvalue().encode("utf-8-sig")
