"""Static analysis hints (docs/specs/07): Gitleaks / Semgrep output read, kept to the added
lines, secrets masked, a missing or failing tool reported without failing."""

import json
import subprocess
from pathlib import Path
from typing import Any

from app.static_analysis.runner import render_hints, run_static

CODE = (
    "import os\n\ndef pay(amount):\n    key = 'sk_live_" + "x" * 24 + "'\n    return eval(amount)\n"
)
FILES = {"app/pay.py": CODE, "../escape.py": "x = 1\n"}
ADDED = {"app/pay.py": {4, 5}}


class FakeTools:
    """Answers like the real tools: Gitleaks writes its report file, Semgrep prints JSON."""

    def __init__(self, semgrep_code: int = 1, slow: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.semgrep_code = semgrep_code
        self.slow = slow
        self.root: Path | None = None

    def __call__(self, cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        tool = Path(cmd[0]).name
        if cmd[1] in ("version", "--version"):
            return subprocess.CompletedProcess(cmd, 0, f"{tool} 1.0\n", "")
        if self.slow == tool:
            raise subprocess.TimeoutExpired(cmd, 1)
        if tool == "gitleaks":
            root = Path(cmd[2])
            self.root = root
            assert not (root.parent / "escape.py").exists()  # "../" never written outside
            report = Path(cmd[cmd.index("--report-path") + 1])
            report.write_text(
                json.dumps(
                    [
                        {
                            "RuleID": "stripe-access-token",
                            "Description": "Stripe Access Token",
                            "File": str(root / "app" / "pay.py"),
                            "StartLine": 4,
                            "EndLine": 4,
                            "StartColumn": 12,
                            "EndColumn": 43,
                        },
                        {  # on a line the change did not add: not this person's
                            "RuleID": "generic-api-key",
                            "File": str(root / "app" / "pay.py"),
                            "StartLine": 1,
                            "EndLine": 1,
                        },
                    ]
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(cmd, 0, "", "")
        results = {
            "results": [
                {
                    "check_id": "python.lang.security.audit.eval-detected",
                    "path": "app/pay.py",
                    "start": {"line": 5},
                    "end": {"line": 5},
                    "extra": {
                        "severity": "WARNING",
                        "message": "Detected the use of eval(). Avoid it.",
                        "metadata": {"category": "security", "confidence": "LOW"},
                    },
                },
                {
                    "check_id": "python.lang.correctness.unused-import",
                    "path": "app/pay.py",
                    "start": {"line": 1},
                    "end": {"line": 1},
                    "extra": {"severity": "INFO", "message": "unused", "metadata": {}},
                },
            ]
        }
        return subprocess.CompletedProcess(cmd, self.semgrep_code, json.dumps(results), "")


TOOLS = {"gitleaks": "/usr/bin/gitleaks", "semgrep": "/usr/bin/semgrep"}


def test_findings_on_added_lines_mapped_and_masked() -> None:
    fake = FakeTools()
    report = run_static(FILES, ADDED, tools=TOOLS, run=fake, semgrep_config="p/default")
    assert report.files == 1  # the "../" path is not written
    assert [(f.tool, f.line_start, f.severity, f.category) for f in report.findings] == [
        ("gitleaks", 4, "critical", "security"),
        ("semgrep", 5, "medium", "security"),
    ]
    secret, ev = report.findings
    assert secret.rule_id == "gitleaks:stripe-access-token"
    assert "sk_live_" not in secret.excerpt and "key =" in secret.excerpt  # the secret masked
    assert "rotate" in secret.message
    assert ev.rule_id == "semgrep:python.lang.security.audit.eval-detected"
    assert ev.title == "Eval detected" and "eval(amount)" in ev.excerpt
    assert report.tools["gitleaks"].ran and report.tools["semgrep"].found == 1
    semgrep_cmd = next(c for c in fake.calls if c[1] == "scan")
    assert "--metrics=off" in semgrep_cmd and "p/default" in semgrep_cmd
    basis = report.basis()
    assert basis["found"] == 2 and basis["tools"]["semgrep"]["version"] == "semgrep 1.0"

    hints = render_hints(report.findings)
    assert "app/pay.py:4 [gitleaks:stripe-access-token] critical/security" in hints
    assert "sk_live_" not in hints
    assert render_hints([]) == ""


def test_missing_failing_or_slow_tools_never_fail() -> None:
    none = run_static(FILES, ADDED, tools={"gitleaks": None, "semgrep": None}, run=FakeTools())
    assert none.findings == [] and none.tools["semgrep"].error == "chưa cài trên worker"

    broken = run_static(FILES, ADDED, tools=TOOLS, run=FakeTools(semgrep_code=2))
    assert broken.tools["semgrep"].ran is False and "thoát mã 2" in (
        broken.tools["semgrep"].error or ""
    )
    assert [f.tool for f in broken.findings] == ["gitleaks"]  # the other tool still counts

    slow = run_static(FILES, ADDED, tools=TOOLS, run=FakeTools(slow="gitleaks"), timeout_s=5)
    assert slow.tools["gitleaks"].error == "quá 5 giây" and [f.tool for f in slow.findings] == [
        "semgrep"
    ]

    assert run_static({}, {}, tools=TOOLS, run=FakeTools()).tools == {}
