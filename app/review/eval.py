"""Reviewer eval: precision/recall on known-bad diffs (docs/specs/06, 12; `make eval`).

Run whenever the prompt, rubric, model or CLI version changes:

    python -m app.review.eval                       # Claude Code CLI, all cases
    python -m app.review.eval --only sec-01-sqli    # one case
    python -m app.review.eval --provider fake       # harness check, no LLM cost

Thresholds (docs/specs/12): precision(critical+high) >= 0.8, recall(critical+high) >= 0.7.
"""

import argparse
import importlib
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.review.postprocess import Finding, postprocess
from app.review.prepare import chunk, prepare
from app.review.prompt import ReviewTarget, build_prompt, review_chunk
from app.review.providers.base import LLMProvider

CASES_DIR = Path("tests/eval/cases")
SEVERE = {"critical", "high"}
LINE_TOLERANCE = 2
PRECISION_TARGET = 0.8
RECALL_TARGET = 0.7

FILE_HEADER = re.compile(r"^diff --git a/(?P<old>\S+) b/(?P<new>\S+)$")


def patch_to_entries(patch: str) -> list[dict[str, Any]]:
    """`git diff` text -> GitLab diff entries ({old_path, new_path, new_file, diff})."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    hunk_lines: list[str] = []
    for line in patch.splitlines():
        if m := FILE_HEADER.match(line):
            if current is not None:
                current["diff"] = "\n".join(hunk_lines) + "\n"
                entries.append(current)
            current, hunk_lines = (
                {"old_path": m["old"], "new_path": m["new"], "new_file": False},
                [],
            )
        elif current is None:
            continue
        elif line.startswith("new file mode"):
            current["new_file"] = True
        elif line.startswith(("--- ", "+++ ", "index ")) and not hunk_lines:
            continue
        else:
            hunk_lines.append(line)
    if current is not None:
        current["diff"] = "\n".join(hunk_lines) + "\n"
        entries.append(current)
    return entries


@dataclass
class Expected:
    file_path: str
    line_start: int
    line_end: int
    severity: str
    category: str


@dataclass
class Case:
    id: str
    description: str
    patch: str
    expected: list[Expected]


def load_cases(directory: Path = CASES_DIR, only: list[str] | None = None) -> list[Case]:
    cases = []
    for case_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        if only and case_dir.name not in only:
            continue
        spec = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
        cases.append(
            Case(
                id=case_dir.name,
                description=spec["description"],
                patch=(case_dir / "diff.patch").read_text(encoding="utf-8"),
                expected=[Expected(**f) for f in spec["findings"]],
            )
        )
    return cases


def overlaps(found: Finding, expected: Expected) -> bool:
    return found.file_path == expected.file_path and (
        found.line_start <= expected.line_end + LINE_TOLERANCE
        and expected.line_start <= found.line_end + LINE_TOLERANCE
    )


@dataclass
class CaseResult:
    case: Case
    findings: list[Finding]
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def severe(self) -> list[Finding]:
        return [f for f in self.findings if f.severity in SEVERE]

    @property
    def true_positives(self) -> int:
        """Severe findings that point at a known problem (of any expected severity)."""
        return sum(any(overlaps(f, e) for e in self.case.expected) for f in self.severe)

    @property
    def severe_expected(self) -> list[Expected]:
        return [e for e in self.case.expected if e.severity in SEVERE]

    @property
    def severe_recalled(self) -> int:
        return sum(any(overlaps(f, e) for f in self.severe) for e in self.severe_expected)

    @property
    def detected(self) -> int:
        """Expected problems hit by a finding of any severity."""
        return sum(any(overlaps(f, e) for f in self.findings) for e in self.case.expected)


@dataclass
class Report:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def precision(self) -> float:
        predicted = sum(len(r.severe) for r in self.results)
        return sum(r.true_positives for r in self.results) / predicted if predicted else 1.0

    @property
    def recall(self) -> float:
        expected = sum(len(r.severe_expected) for r in self.results)
        return sum(r.severe_recalled for r in self.results) / expected if expected else 1.0

    @property
    def passed(self) -> bool:
        no_errors = all(r.error is None for r in self.results)
        return no_errors and self.precision >= PRECISION_TARGET and self.recall >= RECALL_TARGET

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision_critical_high": round(self.precision, 3),
            "recall_critical_high": round(self.recall, 3),
            "passed": self.passed,
            "cost_usd": round(sum(r.cost_usd for r in self.results), 4),
            "cases": [
                {
                    "id": r.case.id,
                    "error": r.error,
                    "expected": len(r.case.expected),
                    "detected": r.detected,
                    "severe_predicted": len(r.severe),
                    "severe_true_positive": r.true_positives,
                    "findings": [
                        [f.severity, f.category, f"{f.file_path}:{f.line_start}", f.title]
                        for f in r.findings
                    ],
                }
                for r in self.results
            ],
        }


def run_case(
    provider: LLMProvider, case: Case, min_confidence: float = 0.5, knowledge: str = ""
) -> CaseResult:
    """`knowledge` is the rendered review knowledge shown to the reviewer (docs/specs/19): the
    per-project eval passes its invariants, the common eval nothing."""
    prepared = prepare(patch_to_entries(case.patch), ignore_paths=[], sensitive_paths=[])
    target = ReviewTarget(project="codepulse/eval", title=case.description, knowledge=knowledge)
    raw, cost = [], 0.0
    try:
        for piece in chunk(prepared, max_tokens=12000):
            result = review_chunk(provider, build_prompt(target, piece))
            raw += result.review.findings
            cost += sum(c.cost_usd for c in result.calls)
    except Exception as exc:  # an eval run reports every failure instead of stopping
        return CaseResult(case, [], cost, f"{exc.__class__.__name__}: {exc}")
    processed = postprocess(raw, {f.path: f for f in prepared.files}, min_confidence)
    return CaseResult(case, processed.findings, cost)


def run(provider: LLMProvider, cases: list[Case], on_case: Callable[[CaseResult], None]) -> Report:
    report = Report()
    for case in cases:
        result = run_case(provider, case)
        report.results.append(result)
        on_case(result)
    return report


def oracle_provider(cases: list[Case]) -> LLMProvider:
    """Answers exactly the expected findings: proves the harness scores a perfect reviewer 1.0."""
    from app.review.providers import FakeLLMProvider

    def respond(prompt: Any) -> dict[str, Any]:
        case = next(c for c in cases if f"MR: {c.description}" in prompt.user)
        return {
            "summary": "oracle",
            "score": 50,
            "findings": [
                {**e.__dict__, "title": "known", "message": "known", "confidence": 1.0}
                for e in case.expected
            ],
        }

    return FakeLLMProvider(respond)


def load_project_knowledge(project_id: str) -> dict[str, Any]:
    """The project's active review knowledge (docs/specs/19), via the lazily imported loader so
    the common eval still runs where the knowledge module or a database is absent."""
    try:
        load_active_dict = importlib.import_module("app.review.knowledge").load_active_dict
    except (ImportError, AttributeError) as exc:
        raise SystemExit(
            "--project needs app.review.knowledge.load_active_dict (docs/specs/19 §3.2), "
            f"which is not available: {exc}"
        ) from exc
    from app.db import get_sessionmaker

    with get_sessionmaker()() as session:
        knowledge: dict[str, Any] = load_active_dict(session, project_id)
    if not knowledge.get("invariants"):
        raise SystemExit(f"project {project_id} has no active knowledge with invariants")
    return knowledge


def main_project(args: argparse.Namespace) -> int:
    """`--project <id>`: the auto-generated per-project eval (app/review/eval_knowledge.py)."""
    from app.review import eval_knowledge as ek

    knowledge: ek.KnowledgeDict
    if args.knowledge:
        knowledge = json.loads(args.knowledge.read_text(encoding="utf-8"))
    else:
        knowledge = load_project_knowledge(args.project)  # type: ignore[assignment]

    provider: LLMProvider
    generator: ek.Generator | None = None
    if args.fake:
        cases, _ = ek.generate_cases(knowledge, args.project, ek.template_generator)
        provider, generator = ek.oracle_provider(cases), ek.template_generator
    else:
        from app.config import get_settings
        from app.review.providers import build_provider

        provider = build_provider(get_settings())

    def show(r: ek.InvariantResult) -> None:
        status = r.error or (
            f"{'hit' if r.recalled else 'MISS':<4} by {r.matched_by or '-':<7} "
            f"clean-fp {r.clean_false_positives}"
        )
        print(f"{r.invariant_id:<34} {status}  ${r.cost_usd:.3f}", flush=True)

    report = ek.run_knowledge_eval(
        provider, knowledge, args.project, generator=generator, on_result=show
    )
    print(
        f"\nrecall(per invariant) {report.recall:.2f} (target {RECALL_TARGET})  "
        f"precision(critical+high) {report.precision:.2f} (target {PRECISION_TARGET})  "
        f"generated {report.generated}  {'PASS' if report.passed else 'FAIL'}"
    )
    out = ek.write_report(report, args.out.parent if args.out else ek.RESULTS_DIR)
    print(f"report: {out}")
    return 0 if report.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.review.eval")
    parser.add_argument(
        "--provider", choices=["claude_code", "fake", "oracle"], default="claude_code"
    )
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--cases", type=Path, default=CASES_DIR)
    parser.add_argument("--out", type=Path, help="write the JSON report here")
    parser.add_argument("--project", help="per-project eval from the active review knowledge")
    parser.add_argument(
        "--knowledge", type=Path, help="with --project: read the knowledge JSON from this file"
    )
    parser.add_argument(
        "--fake", action="store_true", help="with --project: template cases + oracle, no LLM"
    )
    args = parser.parse_args(argv)
    if args.project:
        return main_project(args)

    cases = load_cases(args.cases, args.only)
    provider: LLMProvider
    if args.provider == "claude_code":
        from app.config import get_settings
        from app.review.providers import build_provider

        provider = build_provider(get_settings())
    elif args.provider == "oracle":
        provider = oracle_provider(cases)
    else:
        from app.review.providers import FakeLLMProvider

        provider = FakeLLMProvider()

    def show(r: CaseResult) -> None:
        status = r.error or f"{r.detected}/{len(r.case.expected)} found, {len(r.severe)} severe"
        print(f"{r.case.id:<34} {status}  ${r.cost_usd:.3f}", flush=True)

    report = run(provider, cases, show)
    summary = report.to_dict()
    print(
        f"\nprecision(critical+high) {summary['precision_critical_high']:.2f} "
        f"(target {PRECISION_TARGET})  recall(critical+high) {summary['recall_critical_high']:.2f} "
        f"(target {RECALL_TARGET})  cost ${summary['cost_usd']:.2f}  "
        f"{'PASS' if report.passed else 'FAIL'}"
    )
    out = args.out or Path("tests/eval/results") / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"report: {out}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
