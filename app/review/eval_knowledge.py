"""Per-project eval generated from the review knowledge (docs/specs/19 §5; task 8.6).

For every invariant of a project's active knowledge, one LLM call produces a small violating diff
and a clean diff. Both are cached under `tests/eval/generated/<project>/<invariant>-<sha8>/` so a
rerun (new prompt, rubric or model) costs nothing to regenerate, and then reviewed through the
same path as the common eval (`app.review.eval.run_case`). The report gives recall per invariant
(a finding tagged `rk:<id>` or on the expected lines counts) and precision over the severe
findings, mostly false positives raised on the clean diffs. Thresholds are those of docs/specs/12.

    python -m app.review.eval --project 7            # real provider, active knowledge from the DB
    python -m app.review.eval --project 7 --fake     # harness check, no LLM cost
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from app.review.eval import (
    PRECISION_TARGET,
    RECALL_TARGET,
    Case,
    CaseResult,
    Expected,
    overlaps,
    patch_to_entries,
    run_case,
)
from app.review.prepare import parse_diff
from app.review.providers.base import LLMProvider, ReviewPrompt

log = logging.getLogger(__name__)

GENERATED_DIR = Path("tests/eval/generated")
RESULTS_DIR = Path("tests/eval/results")
MAX_PATCH_CHARS = 6000


class InvariantDict(TypedDict):
    """One invariant of `review_knowledge.data` (docs/specs/19 §3.1); the subset the eval needs."""

    id: str
    statement: str
    severity: str
    signals: list[str]


class KnowledgeDict(TypedDict):
    invariants: list[InvariantDict]
    language: NotRequired[str]  # main language of the project, e.g. "python"


def case_key(invariant: InvariantDict) -> str:
    """Directory name: the invariant id plus 8 hex chars of its statement, so an edited statement
    gets fresh cases while the old ones stay on disk."""
    digest = hashlib.sha256(invariant["statement"].encode("utf-8")).hexdigest()[:8]
    return f"{invariant['id']}-{digest}"


def render_invariants(knowledge: KnowledgeDict) -> str:
    """The team rules as the reviewer sees them: `rk:<id>` is the rule_id it should answer."""
    return "\n".join(
        f"- [rk:{inv['id']}] ({inv['severity']}) {inv['statement']}"
        for inv in knowledge["invariants"]
    )


# ---- generation -------------------------------------------------------------------------------

GENERATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file_path": {"type": "string"},
        "violating_patch": {"type": "string"},
        "violating_line_start": {"type": "integer"},
        "violating_line_end": {"type": "integer"},
        "clean_patch": {"type": "string"},
    },
    "required": [
        "file_path",
        "violating_patch",
        "violating_line_start",
        "violating_line_end",
        "clean_patch",
    ],
}

GENERATOR_SYSTEM = (
    "You write small, realistic code changes to test a code reviewer. Answer with JSON only, "
    "matching the schema. Each patch is a complete `git diff` for exactly one file "
    "(`diff --git a/<path> b/<path>`, `new file mode 100644`, `--- /dev/null`, `+++ b/<path>`, "
    "one `@@ -0,0 +1,N @@` hunk, every line prefixed with `+`), at most 40 lines of code."
)

GENERATOR_USER = """Language: {language}

Team rule [{id}] (severity {severity}):
{statement}
Signals that the rule applies: {signals}

Produce:
1. `violating_patch`: a plausible change that breaks this rule in one obvious place, with
   `violating_line_start`/`violating_line_end` giving the line numbers of the new file where the
   violation is (1-based, within the hunk).
2. `clean_patch`: a change of similar size and topic that follows the rule correctly, so a
   reviewer should raise nothing about this rule.
Use `file_path` for both patches. Do not mention the rule or the word "violation" in the code."""


class GenerationError(Exception):
    """The generator's answer could not be turned into a reviewable case."""


# Returns the generated JSON (dict) for one invariant; the default asks the provider.
Generator = Callable[[InvariantDict, str], dict[str, Any]]


def llm_generator(provider: LLMProvider) -> Generator:
    def generate(invariant: InvariantDict, language: str) -> dict[str, Any]:
        prompt = ReviewPrompt(
            system=GENERATOR_SYSTEM,
            user=GENERATOR_USER.format(
                language=language,
                id=invariant["id"],
                severity=invariant["severity"],
                statement=invariant["statement"],
                signals=", ".join(invariant.get("signals") or []) or "(none)",
            ),
            json_schema=GENERATOR_SCHEMA,
        )
        output = provider.review(prompt).output
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except json.JSONDecodeError as exc:
                raise GenerationError(f"generator answered non-JSON: {exc}") from exc
        if not isinstance(output, dict):
            raise GenerationError("generator answered no JSON object")
        return output

    return generate


def _single_file_patch(patch: str, what: str) -> str:
    entries = patch_to_entries(patch)
    if len(entries) != 1:
        raise GenerationError(f"{what}: expected one file in the patch, got {len(entries)}")
    if len(patch) > MAX_PATCH_CHARS:
        raise GenerationError(f"{what}: patch too long ({len(patch)} chars)")
    entry = entries[0]
    if not parse_diff(entry["new_path"], entry["diff"], entry["new_file"]).added_lines:
        raise GenerationError(f"{what}: patch adds no lines")
    return patch if patch.endswith("\n") else patch + "\n"


def validate_generated(invariant: InvariantDict, answer: dict[str, Any]) -> dict[str, Any]:
    """Check the answer before it is cached: both patches parse and the violation sits on added
    lines of the violating patch. Returns the `expected.json` content."""
    try:
        violating = _single_file_patch(str(answer["violating_patch"]), "violating_patch")
        clean = _single_file_patch(str(answer["clean_patch"]), "clean_patch")
        start, end = int(answer["violating_line_start"]), int(answer["violating_line_end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GenerationError(f"generator answer incomplete: {exc!r}") from exc
    entry = patch_to_entries(violating)[0]
    added = parse_diff(entry["new_path"], entry["diff"], entry["new_file"]).added_lines
    start, end = min(start, end), max(start, end)
    if start not in added or end not in added:
        raise GenerationError(f"violating lines {start}-{end} are not added lines of the patch")
    return {
        "invariant_id": invariant["id"],
        "statement": invariant["statement"],
        "violating_patch": violating,
        "clean_patch": clean,
        "findings": [
            {
                "file_path": entry["new_path"],
                "line_start": start,
                "line_end": end,
                "severity": invariant["severity"],
                "category": "maintainability",
            }
        ],
    }


@dataclass
class GeneratedCase:
    invariant: InvariantDict
    directory: Path
    violating: Case
    clean: Case
    generated_now: bool


def _write_case(directory: Path, spec: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "violating.patch").write_text(spec["violating_patch"], encoding="utf-8")
    (directory / "clean.patch").write_text(spec["clean_patch"], encoding="utf-8")
    expected = {k: v for k, v in spec.items() if k not in {"violating_patch", "clean_patch"}}
    (directory / "expected.json").write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _read_case(invariant: InvariantDict, directory: Path) -> GeneratedCase:
    spec = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
    title = f"[{invariant['id']}] {invariant['statement'][:120]}"
    return GeneratedCase(
        invariant=invariant,
        directory=directory,
        violating=Case(
            id=f"{directory.name}/violating",
            description=f"{title} (violating)",
            patch=(directory / "violating.patch").read_text(encoding="utf-8"),
            expected=[Expected(**f) for f in spec["findings"]],
        ),
        clean=Case(
            id=f"{directory.name}/clean",
            description=f"{title} (clean)",
            patch=(directory / "clean.patch").read_text(encoding="utf-8"),
            expected=[],
        ),
        generated_now=False,
    )


def generate_cases(
    knowledge: KnowledgeDict,
    project_id: str,
    generator: Generator,
    base_dir: Path = GENERATED_DIR,
) -> tuple[list[GeneratedCase], dict[str, str]]:
    """Cases for every invariant, from the cache when present; returns (cases, errors by id)."""
    language = knowledge.get("language") or "python"
    cases: list[GeneratedCase] = []
    errors: dict[str, str] = {}
    for invariant in knowledge["invariants"]:
        directory = base_dir / project_id / case_key(invariant)
        if not (directory / "expected.json").exists():
            try:
                _write_case(
                    directory, validate_generated(invariant, generator(invariant, language))
                )
            except Exception as exc:  # GenerationError or a provider failure: report, never stop
                errors[invariant["id"]] = f"{exc.__class__.__name__}: {exc}"
                continue
            case = _read_case(invariant, directory)
            case.generated_now = True
        else:
            case = _read_case(invariant, directory)
        cases.append(case)
    return cases, errors


# ---- scoring ----------------------------------------------------------------------------------


@dataclass
class InvariantResult:
    invariant_id: str
    severity: str
    violating: CaseResult | None = None
    clean: CaseResult | None = None
    error: str | None = None

    @property
    def matched_by(self) -> str | None:
        """How the violation was found: by the rule id, by the lines, or not at all."""
        if self.violating is None:
            return None
        tag = f"rk:{self.invariant_id}"
        if any(f.rule_id == tag for f in self.violating.findings):
            return "rule_id"
        if any(
            overlaps(f, e) for f in self.violating.findings for e in self.violating.case.expected
        ):
            return "lines"
        return None

    @property
    def recalled(self) -> bool:
        return self.matched_by is not None

    @property
    def severe_true_positives(self) -> int:
        if self.violating is None:
            return 0
        tag = f"rk:{self.invariant_id}"
        expected = self.violating.case.expected
        return sum(
            f.rule_id == tag or any(overlaps(f, e) for e in expected) for f in self.violating.severe
        )

    @property
    def severe_predicted(self) -> int:
        return sum(len(r.severe) for r in (self.violating, self.clean) if r is not None)

    @property
    def clean_false_positives(self) -> int:
        return len(self.clean.severe) if self.clean is not None else 0

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in (self.violating, self.clean) if r is not None)


@dataclass
class KnowledgeReport:
    project_id: str
    results: list[InvariantResult] = field(default_factory=list)
    generated: int = 0  # invariants whose cases were produced in this run (not cached)

    @property
    def recall(self) -> float:
        scored = [r for r in self.results if r.violating is not None]
        return sum(r.recalled for r in scored) / len(scored) if scored else 1.0

    @property
    def precision(self) -> float:
        predicted = sum(r.severe_predicted for r in self.results)
        return sum(r.severe_true_positives for r in self.results) / predicted if predicted else 1.0

    @property
    def passed(self) -> bool:
        no_errors = all(
            r.error is None
            and (r.violating is None or r.violating.error is None)
            and (r.clean is None or r.clean.error is None)
            for r in self.results
        )
        return no_errors and self.precision >= PRECISION_TARGET and self.recall >= RECALL_TARGET

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "recall_per_invariant": round(self.recall, 3),
            "precision_critical_high": round(self.precision, 3),
            "passed": self.passed,
            "cost_usd": round(sum(r.cost_usd for r in self.results), 4),
            "generated_now": self.generated,
            "invariants": [
                {
                    "id": r.invariant_id,
                    "severity": r.severity,
                    "recalled": r.recalled,
                    "matched_by": r.matched_by,
                    "clean_false_positives": r.clean_false_positives,
                    "error": r.error
                    or (r.violating.error if r.violating else None)
                    or (r.clean.error if r.clean else None),
                    "findings": [
                        [which, f.severity, f.rule_id, f"{f.file_path}:{f.line_start}", f.title]
                        for which, res in (("violating", r.violating), ("clean", r.clean))
                        if res is not None
                        for f in res.findings
                    ],
                }
                for r in self.results
            ],
        }


def run_knowledge_eval(
    provider: LLMProvider,
    knowledge: KnowledgeDict,
    project_id: str,
    *,
    generator: Generator | None = None,
    base_dir: Path = GENERATED_DIR,
    on_result: Callable[[InvariantResult], None] = lambda r: None,
) -> KnowledgeReport:
    """Generate (or load) the cases of every invariant and review them with the invariants
    rendered as the prompt's PROJECT KNOWLEDGE block."""
    cases, errors = generate_cases(
        knowledge, project_id, generator or llm_generator(provider), base_dir
    )
    rendered = render_invariants(knowledge)
    report = KnowledgeReport(project_id, generated=sum(c.generated_now for c in cases))
    for inv_id, error in errors.items():
        severity = next(i["severity"] for i in knowledge["invariants"] if i["id"] == inv_id)
        result = InvariantResult(inv_id, severity, error=error)
        report.results.append(result)
        on_result(result)
    for case in cases:
        result = InvariantResult(
            case.invariant["id"],
            case.invariant["severity"],
            violating=run_case(provider, case.violating, knowledge=rendered),
            clean=run_case(provider, case.clean, knowledge=rendered),
        )
        report.results.append(result)
        on_result(result)
    return report


def write_report(report: KnowledgeReport, results_dir: Path = RESULTS_DIR) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    out = results_dir / f"knowledge-{report.project_id}-{stamp}.json"
    out.write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out


# ---- harness: deterministic generator + oracle reviewer -------------------------------------


def template_generator(invariant: InvariantDict, language: str) -> dict[str, Any]:
    """Deterministic cases for the harness (`--fake`) and tests: no LLM involved."""
    path = f"app/generated/{invariant['id'].replace('-', '_')}.py"
    header = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
    violating = header + (
        "@@ -0,0 +1,4 @@\n+def handler(payload: dict) -> None:\n"
        f"+    # breaks: {invariant['statement'][:60]}\n"
        "+    process(payload)\n+    return None\n"
    )
    clean = header + (
        "@@ -0,0 +1,4 @@\n+def handler(payload: dict) -> None:\n"
        "+    validated = validate(payload)\n+    process(validated)\n+    return None\n"
    )
    return {
        "file_path": path,
        "violating_patch": violating,
        "violating_line_start": 3,
        "violating_line_end": 3,
        "clean_patch": clean,
    }


def oracle_provider(cases: list[GeneratedCase]) -> LLMProvider:
    """Flags exactly the expected lines of each violating diff, tagged `rk:<id>`, and nothing on
    the clean one: proves the harness scores a perfect reviewer 1.0 / 1.0."""
    from app.review.providers import FakeLLMProvider

    def respond(prompt: ReviewPrompt) -> dict[str, Any]:
        for case in cases:
            if f"MR: {case.violating.description}" in prompt.user:
                return {
                    "summary": "oracle",
                    "score": 40,
                    "findings": [
                        {
                            **e.__dict__,
                            "title": "breaks team rule",
                            "message": case.invariant["statement"],
                            "confidence": 1.0,
                            "rule_id": f"rk:{case.invariant['id']}",
                        }
                        for e in case.violating.expected
                    ],
                }
        return {"summary": "clean", "score": 100, "findings": []}

    return FakeLLMProvider(respond)
