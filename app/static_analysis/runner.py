"""Run Gitleaks and Semgrep on a change's files and keep what lies on its added lines."""

import contextlib
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from app.review.masking import mask_code
from app.review.prepare import redact

log = logging.getLogger(__name__)

EXCERPT_LINES = 8
MESSAGE_CHARS = 600
MAX_FINDINGS_PER_TOOL = 200
SEMGREP_SEVERITY = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
SEMGREP_CATEGORY = {
    "security": "security",
    "correctness": "bug",
    "performance": "performance",
    "best-practice": "maintainability",
    "maintainability": "maintainability",
    "portability": "maintainability",
}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

Run = Callable[..., subprocess.CompletedProcess[str]]

# Files a change could add to switch the tools off for itself (an allowlist of every path, an
# ignore file): never written to the folder the tools scan.
TOOL_CONFIG = (".gitleaks.toml", ".gitleaksignore", ".semgrepignore")


def run_group(
    cmd: list[str], *, timeout: float, cwd: str | None = None, **_: object
) -> subprocess.CompletedProcess[str]:
    """subprocess.run, but a timeout ends the tool's whole process group (Semgrep starts
    worker processes that outlive a plain kill of the parent)."""
    posix = os.name == "posix"
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        start_new_session=posix,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
        proc.communicate()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _config_file(path: PurePosixPath) -> bool:
    return path.name in TOOL_CONFIG or any(part.startswith(".semgrep") for part in path.parts)


@dataclass
class StaticFinding:
    tool: str  # "gitleaks" | "semgrep"
    rule_id: str  # "gitleaks:<rule>" | "semgrep:<check id>"
    severity: str  # critical | high | medium | low | info
    category: str  # security | bug | performance | maintainability
    file_path: str
    line_start: int
    line_end: int
    title: str
    message: str
    excerpt: str  # masked: a secret never reaches the database
    fingerprint: str


@dataclass
class ToolRun:
    ran: bool = False
    found: int = 0
    seconds: float = 0.0
    error: str | None = None
    version: str | None = None


@dataclass
class StaticReport:
    findings: list[StaticFinding] = field(default_factory=list)
    tools: dict[str, ToolRun] = field(default_factory=dict)
    files: int = 0

    def basis(self) -> dict[str, Any]:
        """What the review's basis card shows."""
        return {
            "files": self.files,
            "found": len(self.findings),
            "tools": {
                name: {
                    "ran": t.ran,
                    "found": t.found,
                    "seconds": round(t.seconds, 1),
                    "error": t.error,
                    "version": t.version,
                }
                for name, t in self.tools.items()
            },
        }


def available() -> dict[str, str | None]:
    """Where each tool is installed (None: not installed on this worker)."""
    return {"gitleaks": shutil.which("gitleaks"), "semgrep": shutil.which("semgrep")}


def _safe_path(path: str) -> PurePosixPath | None:
    """A repository-relative path, or None when it could leave the folder it is written to."""
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or not p.parts or any(part in ("..", "") for part in p.parts):
        return None
    return p


def _excerpt(text: str, start: int, end: int, blank: tuple[int, int, int] | None = None) -> str:
    """Lines start..end (at most EXCERPT_LINES), masked; `blank` (line, from column, to column,
    1-based) is a secret's place on its line, replaced whatever the masking found."""
    lines = text.splitlines()
    first = max(1, start)
    last = min(len(lines), max(end, start), first + EXCERPT_LINES - 1)
    out: list[str] = []
    for n in range(first, last + 1):
        line = lines[n - 1]
        if blank and blank[0] == n and blank[1] >= 1:
            a, b = blank[1] - 1, max(blank[1], blank[2])
            line = line[:a] + "***REDACTED***" + line[b:]
        out.append(line)
    return mask_code("\n".join(out))[:2000]


def _fingerprint(tool: str, rule: str, path: str, excerpt: str) -> str:
    body = " ".join(excerpt.split())
    return hashlib.sha1(f"{tool}|{rule}|{path}|{body}".encode(), usedforsecurity=False).hexdigest()


def _on_added(added: set[int], start: int, end: int) -> bool:
    return any(n in added for n in range(start, max(start, end) + 1))


def _gitleaks(
    run: Run,
    binary: str,
    root: Path,
    files: Mapping[str, str],
    added: Mapping[str, set[int]],
    timeout: float,
    sensitive: Collection[str] = (),
) -> tuple[list[StaticFinding], str | None]:
    report = root.parent / "gitleaks.json"
    proc = run(
        [
            binary,
            "dir",
            str(root),
            "--report-format",
            "json",
            "--report-path",
            str(report),
            "--redact",
            "--no-banner",
            "--exit-code",
            "0",
            "--max-target-megabytes",
            "2",
            "--gitleaks-ignore-path",
            str(root.parent / "gitleaks-ignore"),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        # stderr may quote source text: logged, not stored (the basis is shown to readers).
        log.debug(
            "gitleaks failed",
            extra={"code": proc.returncode, "stderr": (proc.stderr or "")[-500:]},
        )
        return [], f"gitleaks thoát mã {proc.returncode}"
    raw = json.loads(report.read_text(encoding="utf-8") or "[]") if report.exists() else []
    out: list[StaticFinding] = []
    for item in raw[:MAX_FINDINGS_PER_TOOL]:
        path = _relative(str(item.get("File") or ""), root)
        start = int(item.get("StartLine") or 0)
        end = int(item.get("EndLine") or start)
        if path not in files or not _on_added(added.get(path, set()), start, end):
            continue
        rule = str(item.get("RuleID") or "secret")
        excerpt = (
            ""
            if path in sensitive
            else _excerpt(
                files[path],
                start,
                end,
                (start, int(item.get("StartColumn") or 0), int(item.get("EndColumn") or 0)),
            )
        )
        out.append(
            StaticFinding(
                tool="gitleaks",
                rule_id=f"gitleaks:{rule}",
                severity="critical",
                category="security",
                file_path=path,
                line_start=start,
                line_end=end,
                title=f"Lộ secret trong code ({rule})",
                message=redact(
                    f"{item.get('Description') or 'Một giá trị bí mật'} được ghi thẳng vào code. "
                    "Gỡ khỏi code, đưa vào biến môi trường / kho secret và thu hồi (rotate) giá "
                    "trị đã lộ: nó vẫn nằm trong lịch sử git."
                )[:MESSAGE_CHARS],
                excerpt=excerpt,
                fingerprint=_fingerprint("gitleaks", rule, path, f"{start}"),
            )
        )
    return out, None


def _semgrep(
    run: Run,
    binary: str,
    root: Path,
    files: Mapping[str, str],
    added: Mapping[str, set[int]],
    timeout: float,
    config: str,
    sensitive: Collection[str] = (),
) -> tuple[list[StaticFinding], str | None]:
    proc = run(
        [
            binary,
            "scan",
            "--config",
            config,
            "--json",
            "--metrics=off",
            "--disable-version-check",
            "--quiet",
            "--timeout",
            "30",
            "--max-target-bytes",
            "1000000",
            "--jobs",
            "2",
            "--max-memory",
            "2000",
            str(root),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )
    # Semgrep exits 1 when it found something, 0 when not; above: a real error.
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        log.debug(
            "semgrep failed",
            extra={"code": proc.returncode, "stderr": (proc.stderr or "")[-500:]},
        )
        return [], f"semgrep thoát mã {proc.returncode}"
    data = json.loads(proc.stdout)
    out: list[StaticFinding] = []
    for item in (data.get("results") or [])[:MAX_FINDINGS_PER_TOOL]:
        path = _relative(str(item.get("path") or ""), root)
        start = int((item.get("start") or {}).get("line") or 0)
        end = int((item.get("end") or {}).get("line") or start)
        if path not in files or not _on_added(added.get(path, set()), start, end):
            continue
        extra = item.get("extra") or {}
        meta = extra.get("metadata") or {}
        category = SEMGREP_CATEGORY.get(str(meta.get("category") or "").lower(), "maintainability")
        severity = SEMGREP_SEVERITY.get(str(extra.get("severity") or "").upper(), "low")
        if (
            category == "security"
            and severity == "high"
            and str(meta.get("confidence") or "").upper() == "HIGH"
        ):
            severity = "critical"  # docs/specs/07: a sure security error is critical
        check = str(item.get("check_id") or "rule")
        short = check.rsplit(".", 1)[-1].replace("-", " ").replace("_", " ")
        excerpt = "" if path in sensitive else _excerpt(files[path], start, end)
        out.append(
            StaticFinding(
                tool="semgrep",
                rule_id=f"semgrep:{check}"[:200],
                severity=severity,
                category=category,
                file_path=path,
                line_start=start,
                line_end=end,
                title=short[:1].upper() + short[1:200],
                # A message may quote the matched code: masked like the excerpt.
                message=mask_code(redact(" ".join(str(extra.get("message") or "").split())))[
                    :MESSAGE_CHARS
                ],
                excerpt=excerpt,
                fingerprint=_fingerprint("semgrep", check, path, f"{start}:{excerpt}"),
            )
        )
    return out, None


def _relative(path: str, root: Path) -> str:
    p = Path(path)
    try:
        return (
            p.resolve().relative_to(root.resolve()).as_posix() if p.is_absolute() else p.as_posix()
        )
    except ValueError:
        return p.as_posix()


_VERSIONS: dict[str, str | None] = {}


def _version(run: Run, binary: str, args: list[str]) -> str | None:
    if binary in _VERSIONS:
        return _VERSIONS[binary]
    _VERSIONS[binary] = None
    try:
        proc = run([binary, *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (proc.stdout or proc.stderr or "").strip().splitlines()
    _VERSIONS[binary] = text[0][:40] if proc.returncode == 0 and text else None
    return _VERSIONS[binary]


def run_static(
    files: Mapping[str, str],
    added: Mapping[str, set[int]],
    *,
    semgrep_config: str = "p/default",
    timeout_s: float = 120.0,
    tools: Mapping[str, str | None] | None = None,
    run: Run = run_group,
    deadline: float | None = None,
    sensitive: Collection[str] = (),
) -> StaticReport:
    """Write `files` (path -> text at the reviewed commit) to a throw-away folder, run each
    installed tool on it, keep the findings on `added` lines (path -> added line numbers).
    `deadline` (time.monotonic()) bounds both tools; `sensitive` files are scanned but no line of
    them is kept."""
    where = dict(tools) if tools is not None else available()
    report = StaticReport()
    with tempfile.TemporaryDirectory(prefix="cp-static-") as tmp:
        root = Path(tmp) / "src"
        root.mkdir()
        # Ours, empty: no ignore list of the tools' own (Semgrep skips tests/, vendor/… by
        # default) and nothing the change brings.
        (root / ".semgrepignore").write_text("", encoding="utf-8")
        (Path(tmp) / "gitleaks-ignore").write_text("", encoding="utf-8")
        written: dict[str, str] = {}
        for path, text in files.items():
            safe = _safe_path(path)
            if safe is None or _config_file(safe):
                continue
            target = root.joinpath(*safe.parts)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            except OSError:  # "a" and "a/b" in one change, a name the filesystem refuses
                continue
            written[path] = text
            report.files += 1
        if not report.files:
            return report
        files = written

        def budget() -> float:
            left = timeout_s if deadline is None else min(timeout_s, deadline - time.monotonic())
            if left <= 1:
                raise subprocess.TimeoutExpired("static", timeout_s)
            return left

        steps: list[
            tuple[str, Callable[[str], tuple[list[StaticFinding], str | None]], list[str]]
        ] = [
            (
                "gitleaks",
                lambda b: _gitleaks(run, b, root, files, added, budget(), sensitive),
                ["version"],
            ),
            (
                "semgrep",
                lambda b: _semgrep(run, b, root, files, added, budget(), semgrep_config, sensitive),
                ["--version"],
            ),
        ]
        for name, step, version_args in steps:
            binary = where.get(name)
            outcome = report.tools[name] = ToolRun()
            if not binary:
                outcome.error = "chưa cài trên worker"
                continue
            started = time.monotonic()
            try:
                found, error = step(binary)
                outcome.ran = error is None
                outcome.error = error
                outcome.found = len(found)
                report.findings.extend(found)
            except subprocess.TimeoutExpired:
                outcome.error = f"quá {int(timeout_s)} giây"
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                outcome.error = f"lỗi khi chạy ({exc.__class__.__name__})"
                log.warning("static tool failed", extra={"tool": name, "error": str(exc)[:200]})
            outcome.seconds = time.monotonic() - started
            outcome.version = _version(run, binary, version_args) if outcome.ran else None
    report.findings.sort(
        key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), f.file_path, f.line_start)
    )
    return report


HINTS_MAX = 40
HINTS_CHARS = 6_000


def render_hints(findings: list[StaticFinding]) -> str:
    """The STATIC ANALYSIS block of the review prompt ("" when there is nothing)."""
    if not findings:
        return ""
    lines = [
        "STATIC ANALYSIS (Gitleaks / Semgrep on the lines this change adds; tools can be wrong):",
        "For each, report a finding of your own only when it is a real problem in this change "
        "(rule_id: the tool's rule as written below); say nothing of a false positive. A secret "
        "is shown as ***REDACTED***: it really is in the code.",
    ]
    size = sum(len(x) for x in lines)
    shown = 0
    for f in findings[:HINTS_MAX]:
        line = (
            f"- {f.file_path}:{f.line_start} [{f.rule_id}] {f.severity}/{f.category}: "
            f"{f.title}. {f.message[:240]}"
        )
        if size + len(line) > HINTS_CHARS:
            break
        lines.append(line)
        size += len(line)
        shown += 1
    if shown < len(findings):
        lines.append(f"- … {len(findings) - shown} more not shown")
    return "\n".join(lines)
