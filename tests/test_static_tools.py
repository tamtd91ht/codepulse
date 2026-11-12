"""The real Gitleaks and Semgrep (docs/specs/07), where installed (the worker image): line
numbers match the files, tests/ is scanned, and a change cannot switch the tools off with its
own .gitleaks.toml / .semgrepignore. Skipped where the tools are missing."""

import os
import shutil
import time

import pytest

from app.static_analysis.runner import run_static

pytestmark = pytest.mark.skipif(
    not (shutil.which("gitleaks") and shutil.which("semgrep")),
    reason="Gitleaks / Semgrep not installed here (they are in the worker image)",
)

KEY = "AKIA" + "Z7XQ2M4N8P6R3T5V"  # an AWS access key id shape, built at run time
CODE = (
    "import os\n"
    "\n"
    "def handler(event):\n"
    f'    aws_access_key_id = "{KEY}"\n'
    "    return eval(event['expr'])\n"
)


def test_real_tools_on_added_lines() -> None:
    files = {
        "tests/handler_test.py": CODE,  # Semgrep skips tests/ by default: not here
        ".gitleaks.toml": '[allowlist]\npaths = [".*"]\n',  # never written for the tools
        ".semgrepignore": "*\n",
    }
    added = {"tests/handler_test.py": {4, 5}}
    started = time.monotonic()
    report = run_static(
        files,
        added,
        semgrep_config=os.environ.get("SEMGREP_CONFIG", "p/default"),
        timeout_s=300,
    )
    tools = {f.tool: f for f in report.findings}
    assert report.tools["gitleaks"].ran and report.tools["semgrep"].ran, report.basis()
    assert report.files == 1  # the two tool configs were not written
    secret = tools["gitleaks"]
    assert (secret.file_path, secret.line_start) == ("tests/handler_test.py", 4)
    assert KEY not in secret.excerpt and KEY not in secret.message
    ev = tools["semgrep"]
    assert ev.line_start == 5 and ev.category == "security", report.findings
    assert time.monotonic() - started < 300
