"""Parameterised cases (docs/specs/30 §15.15, 30a G13): a table of values, one test per row.

A case's `params` is {"columns": [name], "rows": [[value]]}. Its text uses {{name}} (a BDD scenario
also <name>); a test of row n shows the text with row n's values.
"""

from __future__ import annotations

import re
from typing import Any

COLUMNS_MAX = 20
ROWS_MAX = 200
VALUE_MAX = 500
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,39}$")
VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]{0,39})\s*\}\}")
OUTLINE_RE = re.compile(r"<([A-Za-z_][A-Za-z0-9_ ]{0,39})>")
# Fields of a case's content that take the values.
TEXT_FIELDS = (
    "title",
    "preconditions",
    "steps",
    "expected",
    "test_data",
    "notes",
    "bdd",
    "charter",
)


def _name(raw: str) -> str:
    """A variable as a column name: "<user name>" is the column "user_name" (a BDD Examples header
    with a blank becomes such a column, app/testcases/gherkin.py)."""
    return "_".join(raw.split())


class ParamError(ValueError):
    """A parameter table or a variable that does not fit; the message is shown as is."""


def clean(value: Any) -> dict[str, Any]:
    """The parameter table checked: {} when there is none (no columns or no rows)."""
    if value in (None, {}, []):
        return {}
    if not isinstance(value, dict):
        raise ParamError("tham số: cần {columns, rows}")
    columns = value.get("columns") or []
    rows = value.get("rows") or []
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise ParamError("tham số: columns và rows là danh sách")
    if not columns:
        if rows:
            raise ParamError("tham số: có dòng nhưng chưa có cột")
        return {}
    if len(columns) > COLUMNS_MAX:
        raise ParamError(f"tham số: tối đa {COLUMNS_MAX} cột")
    names: list[str] = []
    for name in columns:
        if not isinstance(name, str) or not NAME_RE.match(name.strip()):
            raise ParamError(f"tham số: tên cột {name!r} chỉ gồm chữ, số, _ (bắt đầu bằng chữ)")
        if name.strip() in names:
            raise ParamError(f"tham số: cột {name.strip()} bị trùng")
        names.append(name.strip())
    if len(rows) > ROWS_MAX:
        raise ParamError(f"tham số: tối đa {ROWS_MAX} dòng")
    out_rows: list[list[str]] = []
    for i, row in enumerate(rows, 1):
        if not isinstance(row, list) or len(row) != len(names):
            raise ParamError(f"tham số: dòng {i} cần đúng {len(names)} giá trị")
        cells: list[str] = []
        for cell in row:
            text = "" if cell is None else str(cell)
            if len(text) > VALUE_MAX:
                raise ParamError(f"tham số: dòng {i} có giá trị dài quá {VALUE_MAX} ký tự")
            cells.append(text)
        out_rows.append(cells)
    if not out_rows:
        return {"columns": names, "rows": []}
    return {"columns": names, "rows": out_rows}


def variables(*texts: str | None, outline: bool = False) -> set[str]:
    """The variables the texts use ({{name}}; also <name> for a BDD scenario)."""
    found: set[str] = set()
    for text in texts:
        if not text:
            continue
        found.update(m.group(1) for m in VAR_RE.finditer(text))
        if outline:
            found.update(_name(m.group(1)) for m in OUTLINE_RE.finditer(text))
    return found


def check_variables(params: dict[str, Any], *texts: str | None, outline: bool = False) -> None:
    """422 material: a variable with no column (only when the case has a parameter table)."""
    if not params.get("columns"):
        return
    missing = sorted(variables(*texts, outline=outline) - set(params["columns"]))
    if missing:
        raise ParamError("biến không có cột tham số: " + ", ".join(missing))


def rows(params: dict[str, Any] | None) -> list[dict[str, str]]:
    """Each row as {column: value}; [] for a case without parameters."""
    if not params or not params.get("columns") or not params.get("rows"):
        return []
    return [dict(zip(params["columns"], row, strict=False)) for row in params["rows"]]


def fill(text: str | None, values: dict[str, str], *, outline: bool = False) -> str | None:
    """`text` with each known variable replaced by its value (unknown ones stay as written)."""
    if not text or not values:
        return text

    def sub(m: re.Match[str]) -> str:
        return values.get(_name(m.group(1)), m.group(0))

    out = VAR_RE.sub(sub, text)
    return OUTLINE_RE.sub(sub, out) if outline else out


def render(content: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    """A case's content (content.case_content) as the test of one parameter row shows it."""
    if not values:
        return content
    outline = content.get("template") == "bdd"
    out = dict(content)
    for field in TEXT_FIELDS:
        if isinstance(out.get(field), str):
            out[field] = fill(out[field], values, outline=outline)
    if isinstance(out.get("steps_list"), list):
        out["steps_list"] = [
            {
                **step,
                "content": fill(step.get("content"), values, outline=outline),
                "expected": fill(step.get("expected"), values, outline=outline),
            }
            if isinstance(step, dict)
            else step
            for step in out["steps_list"]
        ]
    return out


def label(values: dict[str, str], limit: int = 120) -> str:
    """ "[phone=0901, otp=123]" for a test's title."""
    text = ", ".join(f"{k}={v}" for k, v in values.items())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return f"[{text}]"
