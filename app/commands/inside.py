"""What runs inside a command's throwaway container (`python -m app.commands.inside`).

The worker uploads RUN_ROOT/request.json and the prepared clone (RUN_ROOT/work), starts the
container and follows its stdout. This program deletes the request, runs the CLI in work/ and
echoes its stream-json lines, then gathers what the command wrote (app/commands/workspace.finish)
into RUN_ROOT/result/<one file> and prints a final RESULT_TYPE line. The container has no
secrets but the Claude token, mounts nothing and is removed afterwards with every process the
command started. It must not load the application settings (there are none here).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.commands import workspace
from app.review.providers.claude_code import stream_lines

RUN_ROOT = Path("/home/codepulse/run")
REQUEST = "request.json"
RESULT_DIR = "result"
RESULT_TYPE = "codepulse_result"
MAX_LISTED = 200


def run(root: Path = RUN_ROOT, emit: Any = None) -> dict[str, Any]:
    def out(line: str) -> None:
        sys.stdout.write(line if line.endswith("\n") else line + "\n")
        sys.stdout.flush()

    emit = emit or out
    request = json.loads((root / REQUEST).read_text(encoding="utf-8"))
    (root / REQUEST).unlink()
    work, outdir = root / workspace.WORK, root / workspace.OUT
    outdir.mkdir(exist_ok=True)
    before = workspace.snapshot(work)
    summary: dict[str, Any] = {"type": RESULT_TYPE, "code": -1, "timed_out": False}
    try:
        code, stderr = stream_lines(
            [str(a) for a in request["argv"]],
            stdin=str(request.get("stdin") or ""),
            cwd=str(work),
            env=dict(os.environ),
            timeout_s=float(request["timeout_s"]),
            on_line=emit,
            new_session=True,
        )
        summary.update(code=code, stderr=stderr[-2000:])
    except subprocess.TimeoutExpired:
        summary["timed_out"] = True
    collected, download = workspace.finish(root, before, int(request["max_output_bytes"]))
    summary["files"] = collected.files[:MAX_LISTED]
    summary["skipped"] = collected.skipped[:MAX_LISTED]
    if download is not None:
        name = workspace.safe_file_name(download.name)
        (root / RESULT_DIR).mkdir(exist_ok=True)
        os.replace(download, root / RESULT_DIR / name)
        summary["name"] = name
    emit(json.dumps(summary))
    return summary


def main() -> None:
    run()


if __name__ == "__main__":
    main()
