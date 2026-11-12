"""Gherkin for BDD test cases (docs/specs/30 §15.12, 30a G8): our own small parser, no library.

English keywords only (`# language:` is read as a comment): `Feature` (with a description),
`Background`, `Scenario` / `Example`, `Scenario Outline` / `Scenario Template` with one or more
`Examples` / `Scenarios` tables, the steps `Given / When / Then / And / But / *`, data tables
`| a | b |`, docstrings (`\"\"\"` or ```` ``` ````), `@tags` and `#` comments. `Rule` is not
supported. Every error names its line (1-based).

A BDD case keeps one scenario (an optional `Background` first, no `Feature` line); a `.feature`
file holds a Feature with many scenarios, one case each (app/api/routers/test_bdd.py).

Steps of a run (`scenario_steps`): every step line is a step of its own, in order (Background
first), with one exception so that results read like the Steps template: the Then lines that come
right after a When (a `Then` and the `And` / `But` / `*` lines continuing it) become the expected
result of that When step instead of steps of their own. A Then with no When right before it (after
a Given, or first) stays a step of its own with an empty expected result.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

STEP_KEYWORDS = ("Given", "When", "Then", "And", "But", "*")
_SCENARIO = ("Scenario Outline", "Scenario Template", "Scenario", "Example")
_OUTLINE = ("Scenario Outline", "Scenario Template")
_EXAMPLES = ("Examples", "Scenarios")
# Order matters: the longer keyword first ("Scenario Outline" before "Scenario", "Examples" before
# "Example").
_BLOCK_KEYWORDS = (
    "Feature",
    "Background",
    "Scenario Outline",
    "Scenario Template",
    "Scenario",
    "Examples",
    "Scenarios",
    "Example",
    "Rule",
)
LINES_MAX = 50_000  # lines one parsed text may have (a 2 MB .feature of short lines is refused)
_PLACEHOLDER = re.compile(r"<([A-Za-z_][A-Za-z0-9_ ]{0,39})>")
_TAG = re.compile(r"^@[^\s@]+$")


class GherkinError(ValueError):
    """A syntax error at `line` (1-based); the message is shown as is (Vietnamese)."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"dòng {line}: {message}")
        self.line = line
        self.message = message


@dataclass
class Step:
    keyword: str  # as written: Given, When, Then, And, But, *
    kind: str  # given | when | then (And / But / * take the kind of the step before)
    text: str
    line: int
    table: list[list[str]] = field(default_factory=list)
    docstring: str | None = None
    docstring_delim: str = '"""'

    def render(self) -> str:
        """The step as one block of text: its line, then its table or docstring."""
        out = [f"{self.keyword} {self.text}".strip()]
        if self.table:
            out += [format_row(row) for row in self.table]
        if self.docstring is not None:
            out += [self.docstring_delim, self.docstring, self.docstring_delim]
        return "\n".join(out)


@dataclass
class Examples:
    name: str
    line: int
    tags: list[str] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Background:
    name: str
    line: int
    end_line: int
    description: str = ""
    description_lines: list[int] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    keyword: str
    line: int  # the keyword line
    end_line: int  # its last line (steps, tables, docstrings, Examples), comments after left out
    outline: bool = False
    tags: list[str] = field(default_factory=list)
    description: str = ""
    description_lines: list[int] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    examples: list[Examples] = field(default_factory=list)


@dataclass
class Feature:
    name: str = ""
    line: int = 0  # 0: the text has no Feature line (a case's scenario)
    tags: list[str] = field(default_factory=list)
    description: str = ""
    background: Background | None = None
    scenarios: list[Scenario] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # the text's lines, for `block_text`


def _keyword(stripped: str) -> tuple[str | None, str]:
    """("Scenario", "name") for "Scenario: name"; (None, "") when the line is no block keyword."""
    for kw in _BLOCK_KEYWORDS:
        if stripped.startswith(kw):
            rest = stripped[len(kw) :].lstrip()
            if rest.startswith(":"):
                return kw, rest[1:].strip()
    return None, ""


def _step_keyword(stripped: str) -> tuple[str | None, str]:
    for kw in STEP_KEYWORDS:
        if stripped == kw:
            return kw, ""
        if stripped.startswith(kw + " ") or stripped.startswith(kw + "\t"):
            return kw, stripped[len(kw) :].strip()
    return None, ""


def _cells(stripped: str, line: int) -> list[str]:
    """The cells of "| a | b \\| c |" (escapes: \\| \\\\ \\n)."""
    if not stripped.endswith("|") or len(stripped) < 2:
        raise GherkinError(line, "dòng bảng phải kết thúc bằng |")
    cells: list[str] = []
    cell: list[str] = []
    i = 1
    body = stripped
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            cell.append({"|": "|", "\\": "\\", "n": "\n"}.get(nxt, "\\" + nxt))
            i += 2
            continue
        if ch == "|":
            cells.append("".join(cell).strip())
            cell = []
        else:
            cell.append(ch)
        i += 1
    return cells


def format_row(cells: Sequence[str]) -> str:
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n")

    return "| " + " | ".join(esc(c) for c in cells) + " |"


def _tags(stripped: str, line: int) -> list[str]:
    text = stripped
    cut = re.search(r"\s#", text)  # "@a @b # a comment"
    if cut:
        text = text[: cut.start()]
    out = []
    for token in text.split():
        if not _TAG.match(token):
            raise GherkinError(line, f"tag “{token[:40]}” không hợp lệ (dạng @ten, không dấu cách)")
        out.append(token[1:])
    return out


def var_name(name: str) -> str:
    """A column / placeholder name as kept: inner blanks become "_" ("user name" -> "user_name")."""
    return "_".join(name.split())


def parse(text: str) -> Feature:
    """The Feature of `text` (no Feature line is fine: then `line` is 0). Raises GherkinError."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) > LINES_MAX:
        raise GherkinError(LINES_MAX + 1, f"tối đa {LINES_MAX} dòng")
    feature = Feature(lines=lines)
    # Description lines, joined once at the end (joining as they come is quadratic).
    feature_desc: list[str] = []
    block_desc: dict[int, tuple[Background | Scenario, list[str]]] = {}
    tags: list[str] = []
    tags_line = 0
    block: Background | Scenario | None = None
    step: Step | None = None
    examples: Examples | None = None
    # What the last line was: feature, background, scenario, step, table, docstring, examples.
    last = ""
    i = 0

    def touch(n: int) -> None:
        if block is not None:
            block.end_line = n

    while i < len(lines):
        n = i + 1
        raw = lines[i]
        s = raw.strip()
        i += 1
        if not s or s.startswith("#"):
            continue
        if s.startswith("@"):
            if not tags:
                tags_line = n
            tags += _tags(s, n)
            continue
        if s.startswith('"""') or s.startswith("```"):
            if step is None or last not in ("step",):
                raise GherkinError(n, "docstring phải ngay sau một bước")
            if tags:
                raise GherkinError(tags_line, "tag phải đứng trước Feature, Scenario hoặc Examples")
            delim = s[:3]
            indent = len(raw) - len(raw.lstrip())
            body: list[str] = []
            closed = False
            while i < len(lines):
                inner = lines[i]
                i += 1
                if inner.strip() == delim:
                    closed = True
                    break
                lead = len(inner) - len(inner.lstrip())
                body.append(inner[min(indent, lead) :].rstrip())
            if not closed:
                raise GherkinError(n, f"docstring {delim} chưa đóng")
            step.docstring = "\n".join(body)
            step.docstring_delim = delim
            last = "docstring"
            touch(i)
            continue
        if s.startswith("|"):
            if tags:
                raise GherkinError(tags_line, "tag phải đứng trước Feature, Scenario hoặc Examples")
            row = _cells(s, n)
            if last in ("step", "table") and step is not None:
                if step.table and len(row) != len(step.table[0]):
                    raise GherkinError(n, f"bảng cần đúng {len(step.table[0])} cột như dòng đầu")
                step.table.append(row)
                last = "table"
            elif last in ("examples", "examples_table") and examples is not None:
                if not examples.header:
                    if any(not c for c in row):
                        raise GherkinError(n, "tên cột của Examples không được trống")
                    examples.header = row
                else:
                    if len(row) != len(examples.header):
                        raise GherkinError(
                            n, f"dòng Examples cần đúng {len(examples.header)} giá trị"
                        )
                    examples.rows.append(row)
                last = "examples_table"
            else:
                raise GherkinError(n, "bảng phải ngay sau một bước hoặc Examples")
            touch(n)
            continue
        kw, rest = _keyword(s)
        if kw == "Feature":
            if feature.line:
                raise GherkinError(n, "mỗi file chỉ có một Feature")
            if block is not None:
                raise GherkinError(n, "Feature phải đứng đầu")
            feature.name, feature.line, feature.tags = rest, n, tags
            tags = []
            last = "feature"
            continue
        if kw == "Rule":
            raise GherkinError(n, "Rule chưa được hỗ trợ")
        if kw == "Background":
            if tags:
                raise GherkinError(tags_line, "Background không có tag")
            if feature.background is not None:
                raise GherkinError(n, "chỉ có một Background")
            if feature.scenarios:
                raise GherkinError(n, "Background phải đứng trước các Scenario")
            feature.background = Background(name=rest, line=n, end_line=n)
            block, step, examples = feature.background, None, None
            last = "background"
            continue
        if kw in _SCENARIO:
            scenario = Scenario(
                name=rest, keyword=kw, line=n, end_line=n, outline=kw in _OUTLINE, tags=tags
            )
            tags = []
            feature.scenarios.append(scenario)
            block, step, examples = scenario, None, None
            last = "scenario"
            continue
        if kw in _EXAMPLES:
            if not isinstance(block, Scenario):
                raise GherkinError(n, "Examples phải nằm trong Scenario Outline")
            if not block.outline:
                raise GherkinError(n, "Examples chỉ dùng với Scenario Outline")
            examples = Examples(name=rest, line=n, tags=tags)
            tags = []
            block.examples.append(examples)
            step = None
            last = "examples"
            touch(n)
            continue
        if tags:
            raise GherkinError(tags_line, "tag phải đứng trước Feature, Scenario hoặc Examples")
        word, rest = _step_keyword(s)
        if word is not None:
            if block is None:
                raise GherkinError(n, "bước phải nằm trong Scenario hoặc Background")
            if examples is not None:
                raise GherkinError(n, "bước không được đứng sau Examples")
            if not rest:
                raise GherkinError(n, f"bước {word} chưa có nội dung")
            kind = word.lower()
            if word in ("And", "But", "*"):
                kind = block.steps[-1].kind if block.steps else "given"
            step = Step(keyword=word, kind=kind, text=rest, line=n)
            block.steps.append(step)
            last = "step"
            touch(n)
            continue
        # Free text: a description right under Feature, Background, Scenario or Examples.
        if last == "feature" and block is None:
            feature_desc.append(s)
            continue
        if last in ("background", "scenario") and block is not None and not block.steps:
            block_desc.setdefault(id(block), (block, []))[1].append(s)
            block.description_lines.append(n)
            touch(n)
            continue
        if last == "examples" and examples is not None and not examples.header:
            touch(n)
            continue
        raise GherkinError(n, f"không hiểu dòng “{s[:60]}” (thiếu từ khoá Given/When/Then…?)")
    if tags:
        raise GherkinError(tags_line, "tag phải đứng trước Feature, Scenario hoặc Examples")
    feature.description = "\n".join(feature_desc)
    for owner, texts in block_desc.values():
        owner.description = "\n".join(texts)
    for scenario in feature.scenarios:
        _check_outline(scenario)
    return feature


def _check_outline(scenario: Scenario) -> None:
    if not scenario.outline:
        return
    if not scenario.examples:
        raise GherkinError(scenario.line, f"{scenario.keyword} cần bảng Examples")
    columns: set[str] = set()
    for ex in scenario.examples:
        if not ex.header:
            raise GherkinError(ex.line, "Examples cần bảng (dòng đầu là tên cột)")
        columns.update(var_name(h) for h in ex.header)
    for st in scenario.steps:
        texts = [st.text, *(c for row in st.table for c in row), st.docstring or ""]
        for t in texts:
            for m in _PLACEHOLDER.finditer(t):
                if var_name(m.group(1)) not in columns:
                    raise GherkinError(st.line, f"<{m.group(1)}> không có cột trong Examples")


def check_case(text: str) -> Feature:
    """The scenario of a BDD case: no Feature line, exactly one Scenario or Scenario Outline (a
    Background before it is fine), no free description lines under them (in a case's editor such a
    line is nearly always a mistyped step, "Givn ..."; write a comment "# ..." instead). Raises
    GherkinError."""
    feature = parse(text)
    if feature.line:
        raise GherkinError(feature.line, "kịch bản của một testcase không có dòng Feature")
    if len(feature.scenarios) != 1:
        line = feature.scenarios[1].line if len(feature.scenarios) > 1 else 1
        raise GherkinError(line, "mỗi testcase cần đúng một Scenario hoặc Scenario Outline")
    for block in (feature.background, feature.scenarios[0]):
        if block is not None and block.description_lines:
            raise GherkinError(
                block.description_lines[0],
                "không hiểu dòng (thiếu từ khoá Given/When/Then…?); ghi chú thì bắt đầu bằng #",
            )
    return feature


def block_text(feature: Feature, start: int, end: int, commented: Sequence[int] = ()) -> str:
    """Lines `start`..`end` (1-based) of the parsed text, their common indent removed; the lines
    `commented` (descriptions) become "# " comments."""
    marks = set(commented)
    rows = []
    for n in range(start, end + 1):
        line = feature.lines[n - 1].rstrip()
        if n in marks:
            lead = len(line) - len(line.lstrip())
            line = f"{line[:lead]}# {line[lead:]}"
        rows.append(line)
    return textwrap.dedent("\n".join(rows)).strip("\n")


def background_text(feature: Feature) -> str | None:
    """The Feature's Background as a case keeps it (description lines as comments); None: none."""
    bg = feature.background
    if bg is None:
        return None
    return block_text(feature, bg.line, bg.end_line, bg.description_lines)


def scenario_text(feature: Feature, scenario: Scenario) -> str:
    """One scenario of a .feature as written (its tags left out: they become the case's; its
    description lines kept as comments, a case has no description)."""
    return block_text(feature, scenario.line, scenario.end_line, scenario.description_lines)


def join_case(background: str | None, scenario: str) -> str:
    """A case's text: the Background (if any), a blank line, then the scenario."""
    return scenario if background is None else f"{background}\n\n{scenario}"


def case_text(feature: Feature, scenario: Scenario) -> str:
    """The text a case keeps for one scenario of a .feature: the Feature's Background (if any),
    a blank line, then the scenario as written. An import of many scenarios computes
    `background_text` once and calls `join_case` instead."""
    return join_case(background_text(feature), scenario_text(feature, scenario))


def split_case(text: str) -> tuple[str | None, str]:
    """(Background text or None, scenario text) of a case's scenario; raises GherkinError."""
    feature = check_case(text)
    sc = feature.scenarios[0]
    bg = feature.background
    before = block_text(feature, bg.line, bg.end_line) if bg is not None else None
    return before, block_text(feature, sc.line, sc.end_line)


def scenario_steps(text: str | None) -> list[dict[str, str]]:
    """The steps a run shows for a BDD case ({"content", "expected"}); see the module docstring
    for the rule. [] when the text is empty or does not parse."""
    if not (text or "").strip():
        return []
    try:
        feature = parse(text or "")
    except GherkinError:
        return []
    steps = list(feature.background.steps) if feature.background else []
    for sc in feature.scenarios:
        steps += sc.steps
    out: list[dict[str, str]] = []
    attach: int | None = None  # the When step taking the Then lines that follow it
    for st in steps:
        if st.kind == "then" and attach is not None:
            before = out[attach]["expected"]
            out[attach]["expected"] = f"{before}\n{st.render()}" if before else st.render()
            continue
        out.append({"content": st.render(), "expected": ""})
        attach = len(out) - 1 if st.kind == "when" else None
    return out


def has_then(text: str | None) -> bool:
    """The scenario checks something (a Then step)."""
    try:
        feature = parse(text or "")
    except GherkinError:
        return False
    steps = list(feature.background.steps) if feature.background else []
    for sc in feature.scenarios:
        steps += sc.steps
    return any(st.kind == "then" for st in steps)


def outline_params(examples: Sequence[Examples]) -> dict[str, Any]:
    """The parameter table of a Scenario Outline (G13): the columns of every Examples table (in
    order, once each, blanks inside a name made "_"), a row per Examples row ("" for a column its
    table lacks). {} when there is no column."""
    columns: list[str] = []
    for ex in examples:
        for h in ex.header:
            name = var_name(h)
            if name not in columns:
                columns.append(name)
    if not columns:
        return {}
    rows: list[list[str]] = []
    for ex in examples:
        index = {var_name(h): i for i, h in enumerate(ex.header)}
        for row in ex.rows:
            rows.append([row[index[c]] if c in index else "" for c in columns])
    return {"columns": columns, "rows": rows}


def _indent(text: str, prefix: str = "  ") -> list[str]:
    return [f"{prefix}{line}" if line.strip() else "" for line in text.split("\n")]


def format_feature(
    name: str,
    description: str | None,
    scenarios: Sequence[tuple[Sequence[str], str, str]],
) -> str:
    """A .feature file: `scenarios` is [(tags, case text, case title)] in order. A Background all
    the cases share is written once at the top; when they differ, each case's Background steps are
    written at the start of its own scenario instead (Gherkin has one Background per Feature). A
    case whose text does not parse is written as an empty scenario named by its title."""
    out = [f"Feature: {' '.join((name or 'Feature').split())}"]
    if (description or "").strip():
        out += _indent((description or "").strip())
    parsed: list[tuple[list[str], str | None, str]] = []
    for tags, text, title in scenarios:
        try:
            before, body = split_case(text or "")
        except GherkinError:
            before, body = None, f"Scenario: {' '.join((title or '').split())}"
        parsed.append(([t for t in tags if t], before, body))
    backgrounds = {before for _, before, _ in parsed}
    shared = backgrounds.pop() if len(backgrounds) == 1 else None
    if shared:
        out.append("")
        out += _indent(shared)
    for tags, before, body in parsed:
        out.append("")
        if tags:
            out.append("  " + " ".join(f"@{t}" for t in tags))
        if before and before != shared:
            body = _inline_background(before, body)
        out += _indent(body)
    return "\n".join(out) + "\n"


def _inline_background(background: str, body: str) -> str:
    """`body` with the Background's steps written first, under the scenario line and its
    description (before the first step)."""
    bg_lines = background.split("\n")[1:]  # without "Background:"
    steps = textwrap.dedent("\n".join(bg_lines)).strip("\n")
    lines = body.split("\n")
    at = len(lines)
    for i, line in enumerate(lines[1:], start=1):
        word, _ = _step_keyword(line.strip())
        kw, _ = _keyword(line.strip())
        if word is not None or kw is not None:
            at = i
            break
    inner = [f"  {line}" if line.strip() else "" for line in steps.split("\n")]
    return "\n".join([*lines[:at], *inner, *lines[at:]])


def tag_name(tag: str) -> str:
    """A case's tag as a Gherkin tag (no blanks, no "@")."""
    return re.sub(r"[\s@]+", "_", tag.strip())
