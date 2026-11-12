"""Custom fields of cases and results (docs/specs/30 §15.8, G5).

A dự án defines fields (TestField) for its cases or its results; their values live in
`TestCase.custom` / `TestResult.custom` as `{key: value}`. The key is a slug of the name, made once
and never changed. Archiving a field hides it from forms, filters and columns; its values stay.

Values are checked by type (`value_of`): text ≤ 2 000 characters, textarea ≤ 20 000, number a
number, date `YYYY-MM-DD`, dropdown one of its options, multiselect a list of them, checkbox a
bool, user the id of an active user, url http(s). Unknown keys are dropped. Required fields block
a save through the API (`clean_custom(enforce_required=True)`); imports, the AI and CI only fill
the defaults. A suite shared by several dự án uses the fields of its home dự án.
"""

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.assistant.knowledge import fold
from app.models import TestCase, TestField, User
from app.models.testcases import FIELD_TYPES, TEMPLATES
from app.testcases import service as tc

KEY_MAX = 40
NAME_MAX = 100
OPTION_MAX = 100  # characters of one option
OPTIONS_MAX = 100  # options of one field
FIELDS_MAX = 50  # fields of one dự án and entity, archived included
TEXT_MAX = {"text": 2_000, "textarea": 20_000}
URL_MAX = 2_000
CHOICE_TYPES = ("dropdown", "multiselect")
_TRUE = {"1", "x", "true", "yes", "y", "co", "on", "checked", "dung"}
_FALSE = {"0", "false", "no", "n", "khong", "off", ""}
FORMULA_LEAD = ("=", "+", "-", "@")


def slug(name: str) -> str:
    """ "Môi trường test" -> "moi_truong_test": lower case, no accents, words joined by "_"."""
    folded = unicodedata.normalize("NFD", name.casefold().replace("đ", "d"))
    plain = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    out = "_".join(re.findall(r"[a-z0-9]+", plain))[:KEY_MAX].strip("_")
    if out and not out[0].isalpha():
        out = f"f_{out}"[:KEY_MAX].strip("_")
    return out or "field"


def rows(
    session: Session, bp_id: int, entity: str, *, archived: bool | None = False
) -> list[TestField]:
    """The fields of a dự án for `entity`, in order. `archived`: False live ones only, True
    archived ones only, None both."""
    query = select(TestField).where(
        TestField.business_project_id == bp_id, TestField.entity == entity
    )
    if archived is not None:
        query = query.where(
            TestField.archived_at.is_not(None) if archived else TestField.archived_at.is_(None)
        )
    return list(session.scalars(query.order_by(TestField.position, TestField.id)))


def unique_key(session: Session, bp_id: int, entity: str, name: str) -> str:
    base = slug(name)
    taken = set(
        session.scalars(
            select(TestField.key).where(
                TestField.business_project_id == bp_id, TestField.entity == entity
            )
        )
    )
    key, n = base, 1
    while key in taken:
        n += 1
        suffix = f"_{n}"
        key = f"{base[: KEY_MAX - len(suffix)]}{suffix}"
    return key


def applicable(
    fields: Iterable[TestField], suite_id: int | None, template: str | None
) -> list[TestField]:
    """The live fields a case (or a result of a case) of this suite and template has."""
    out = []
    for f in fields:
        if f.archived_at is not None:
            continue
        if f.suite_ids and (suite_id is None or suite_id not in f.suite_ids):
            continue
        if f.templates and (template or "text") not in f.templates:
            continue
        out.append(f)
    return out


# -- definitions -----------------------------------------------------------------------------------


def clean_name(value: Any) -> str:
    name = " ".join(tc.CONTROL.sub("", str(value or "")).split())[:NAME_MAX]
    if not name:
        raise tc.FieldError("trường cần tên")
    if name[0] in FORMULA_LEAD:
        # A header cell of the exports: a spreadsheet would read it as a formula.
        raise tc.FieldError("tên trường không được bắt đầu bằng = + - @")
    return name


def clean_options(type_: str, values: Any) -> list[str]:
    if type_ not in CHOICE_TYPES:
        return []
    if not isinstance(values, list):
        raise tc.FieldError("danh sách lựa chọn không hợp lệ")
    out: list[str] = []
    for v in values:
        if not isinstance(v, str | int | float) or isinstance(v, bool):
            raise tc.FieldError("lựa chọn không hợp lệ")
        item = " ".join(tc.CONTROL.sub("", str(v)).split())
        if len(item) > OPTION_MAX:
            raise tc.FieldError(f"mỗi lựa chọn tối đa {OPTION_MAX} ký tự")
        if item and item.casefold() not in (o.casefold() for o in out):
            out.append(item)
    if not out:
        raise tc.FieldError("trường dạng danh sách cần ít nhất một lựa chọn")
    if len(out) > OPTIONS_MAX:
        raise tc.FieldError(f"tối đa {OPTIONS_MAX} lựa chọn")
    return out


def clean_templates(values: Any) -> list[str]:
    out = []
    for v in values or []:
        if v not in TEMPLATES:
            raise tc.FieldError("template không hợp lệ")
        if v not in out:
            out.append(v)
    return out


def check_type(type_: str) -> str:
    if type_ not in FIELD_TYPES:
        raise tc.FieldError("kiểu trường không hợp lệ")
    return type_


# -- values ----------------------------------------------------------------------------------------


def _bad(field: TestField, why: str) -> tc.FieldError:
    return tc.FieldError(f"trường “{field.name}”: {why}")


def _active_user(session: Session, user_id: int) -> bool:
    return bool(
        session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.id == user_id, or_(User.active.is_(None), User.active.is_(True)))
        )
    )


def value_of(session: Session, field: TestField, raw: Any) -> Any:
    """The value kept for `raw` (None: no value, the key is dropped); FieldError naming the
    field when it is not a value of its type."""
    if raw is None:
        return None
    t = field.type
    if t in ("text", "textarea"):
        if not isinstance(raw, str | int | float) or isinstance(raw, bool):
            raise _bad(field, "phải là chữ")
        text = tc.CONTROL.sub("", str(raw).replace("\r\n", "\n").replace("\r", "\n")).strip()
        if t == "text":
            text = " ".join(text.split())
        if len(text) > TEXT_MAX[t]:
            raise _bad(field, f"tối đa {TEXT_MAX[t]} ký tự")
        return text or None
    if t == "number":
        if isinstance(raw, bool) or not isinstance(raw, int | float):
            raise _bad(field, "phải là số")
        if isinstance(raw, float) and not math.isfinite(raw):
            raise _bad(field, "phải là số")
        return int(raw) if isinstance(raw, float) and raw.is_integer() else raw
    if t == "date":
        if not isinstance(raw, str):
            raise _bad(field, "ngày dạng YYYY-MM-DD")
        if not raw.strip():
            return None
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw.strip()):
                raise ValueError
            return date.fromisoformat(raw.strip()).isoformat()
        except ValueError:
            raise _bad(field, "ngày dạng YYYY-MM-DD") from None
    if t == "dropdown":
        if not isinstance(raw, str):
            raise _bad(field, "chọn một giá trị trong danh sách")
        if not raw.strip():
            return None
        found = _option(field, raw)
        if found is None:
            raise _bad(field, f"“{raw[:60]}” không có trong danh sách")
        return found
    if t == "multiselect":
        if not isinstance(raw, list):
            raise _bad(field, "phải là danh sách giá trị")
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                raise _bad(field, "phải là danh sách giá trị")
            found = _option(field, item)
            if found is None:
                raise _bad(field, f"“{item[:60]}” không có trong danh sách")
            if found not in out:
                out.append(found)
        return out or None
    if t == "checkbox":
        if not isinstance(raw, bool):
            raise _bad(field, "phải là true / false")
        return raw
    if t == "user":
        if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
            raise _bad(field, "phải là id người dùng")
        if not _active_user(session, raw):
            raise _bad(field, "người dùng không tồn tại hoặc đã nghỉ")
        return raw
    if t == "url":
        if not isinstance(raw, str):
            raise _bad(field, "phải là đường dẫn http(s)")
        url = raw.strip()
        if not url:
            return None
        parts = urlsplit(url)
        if len(url) > URL_MAX or parts.scheme not in ("http", "https") or not parts.netloc:
            raise _bad(field, "phải là đường dẫn http(s)")
        if tc.CONTROL.search(url) or any(c.isspace() for c in url):
            raise _bad(field, "phải là đường dẫn http(s)")
        return url
    raise _bad(field, "kiểu trường không hợp lệ")


def _option(field: TestField, raw: str) -> str | None:
    wanted = " ".join(raw.split()).casefold()
    return next((o for o in field.options or [] if o.casefold() == wanted), None)


def coerce(session: Session, field: TestField, raw: str) -> Any:
    """A spreadsheet cell as a value of the field; None when empty or not readable (an import
    is never blocked by a value)."""
    cell = (raw or "").strip()
    if not cell:
        return None
    try:
        if field.type == "number":
            number = float(cell.replace(" ", "").replace(",", "."))
            return value_of(session, field, number)
        if field.type == "checkbox":
            folded = fold(cell).strip()
            if folded in _TRUE:
                return True
            return False if folded in _FALSE else None
        if field.type == "multiselect":
            items = [p for p in re.split(r"[,;\n]", cell) if p.strip()]
            return value_of(session, field, [i.strip() for i in items])
        if field.type == "date":
            return value_of(session, field, cell[:10])
        if field.type == "user":
            if cell.isdigit():
                return value_of(session, field, int(cell))
            uid = session.scalar(
                select(User.id)
                .where(
                    or_(
                        func.lower(User.primary_email) == cell.lower(),
                        func.lower(User.full_name) == cell.lower(),
                    ),
                    or_(User.active.is_(None), User.active.is_(True)),
                )
                .order_by(User.id)
                .limit(1)
            )
            return uid
        return value_of(session, field, cell)
    except (tc.FieldError, ValueError):
        return None


def clean_custom(
    session: Session,
    bp_id: int,
    entity: str,
    values: dict[str, Any] | None,
    *,
    suite_id: int | None,
    template: str | None,
    partial: bool = False,
    enforce_required: bool = True,
    defaults: bool = False,
    current: dict[str, Any] | None = None,
    fields: Sequence[TestField] | None = None,
) -> dict[str, Any]:
    """The `custom` kept: `current` (with `partial`, what the record has now) with `values`
    applied (None removes a key) for the live fields applicable to the suite and template;
    unknown, archived or non-applicable keys are left as they were. `defaults`: missing values
    take the field's default. `enforce_required`: a required field without a value -> FieldError
    naming it."""
    every = list(fields) if fields is not None else rows(session, bp_id, entity, archived=None)
    live = {f.key: f for f in applicable(every, suite_id, template)}
    out: dict[str, Any] = dict(current or {}) if partial else {}
    if not partial and current:
        # Values of archived or non-applicable fields are kept as they were.
        out = {k: v for k, v in current.items() if k not in live}
    if values is not None and not isinstance(values, dict):
        raise tc.FieldError("trường tuỳ biến không hợp lệ")
    for key, raw in (values or {}).items():
        field = live.get(key)
        if field is None:
            continue
        value = value_of(session, field, raw)
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    if defaults:
        for f in live.values():
            if f.key not in out and f.default is not None:
                try:
                    value = value_of(session, f, f.default)
                except tc.FieldError:
                    value = None
                if value is not None:
                    out[f.key] = value
    if enforce_required:
        for f in live.values():
            if f.required and out.get(f.key) is None:
                raise tc.FieldError(f"trường “{f.name}” là bắt buộc")
    return out


def with_defaults(
    session: Session,
    bp_id: int,
    entity: str,
    custom: dict[str, Any] | None,
    *,
    suite_id: int | None,
    template: str | None,
    fields: Sequence[TestField] | None = None,
) -> dict[str, Any]:
    """`custom` with the defaults of the applicable fields it lacks (imports, AI, CI)."""
    every = list(fields) if fields is not None else rows(session, bp_id, entity)
    out = dict(custom or {})
    for f in applicable(every, suite_id, template):
        if f.key not in out and f.default is not None:
            try:
                value = value_of(session, f, f.default)
            except tc.FieldError:
                continue
            if value is not None:
                out[f.key] = value
    return out


def filter_clause(field: TestField, raw: str) -> ColumnElement[bool]:
    """`f.<key>=<value>` on the case list (§15.8): dropdown / multiselect / checkbox / user /
    number / date equal; text, textarea, url contain (accents and case aside for text). Raises
    FieldError for a value the field cannot hold."""
    col = TestCase.custom[field.key]
    text_col: ColumnElement[str] = TestCase.custom[field.key].astext
    value = raw.strip()
    t = field.type
    if t in ("text", "textarea", "url"):
        needle = value.casefold()
        return func.lower(func.coalesce(text_col, "")).contains(needle, autoescape=True)
    if t == "dropdown":
        found = _option(field, value)
        return text_col == (found if found is not None else value)
    if t == "multiselect":
        found = _option(field, value)
        return TestCase.custom.contains({field.key: [found if found is not None else value]})
    if t == "checkbox":
        flag = fold(value).strip() in _TRUE
        if flag:
            return TestCase.custom.contains({field.key: True})
        return or_(col.is_(None), TestCase.custom.contains({field.key: False}))
    if t == "user":
        if not value.isdigit():
            raise tc.FieldError(f"trường “{field.name}”: lọc theo id người dùng")
        return TestCase.custom.contains({field.key: int(value)})
    if t == "number":
        try:
            number = float(value.replace(",", "."))
        except ValueError:
            raise tc.FieldError(f"trường “{field.name}”: phải là số") from None
        if not math.isfinite(number):
            raise tc.FieldError(f"trường “{field.name}”: phải là số")
        n: int | float = int(number) if number.is_integer() else number
        return TestCase.custom.contains({field.key: n})
    return text_col == value


def display(value: Any, field: TestField, people: dict[int, str] | None = None) -> str:
    """A value as an export cell shows it."""
    if value is None:
        return ""
    if field.type == "checkbox":
        return "Có" if value is True else ""
    if field.type == "multiselect" and isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if field.type == "user" and isinstance(value, int):
        return (people or {}).get(value, str(value))
    return str(value)
