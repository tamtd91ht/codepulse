"""The folder one command run works in: a git clone of the repo, the dự án's shared .claude laid
under it, and what the run hands back.

Layout of a run under COMMAND_RUNS_DIR:

    <run id>/work/   the clone (deleted as soon as the command ends, whatever happened)
    <run id>/out/    the files handed back; CODEPULSE_OUTPUT_DIR of the command
    <run id>/<name>  what the download sends: the only file, or result.zip of several

The clone authenticates with the GitLab token in an `Authorization` header passed through
GIT_CONFIG_* variables: never in the URL, the argv or `.git/config`, so neither `ps` nor the
command (which can run git itself) sees it.

What the repo controls is untrusted: symlinks are never checked out (and any left are removed:
a committed `CLAUDE.md -> /proc/1/environ` would be loaded as memory), the repo's
`.claude/settings*.json` keep only `permissions` and `model` (no `env`, `apiKeyHelper` or other
key that could redirect the Claude token), and `.git` (whose history still holds the files
SENSITIVE_PATHS removed) is kept only for a command whose `allowed-tools` lets it run git.
"""

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from app.review.checkout import is_sensitive

log = logging.getLogger(__name__)

WORK = "work"
OUT = "out"
ZIP_NAME = "result.zip"
# Not part of the result even when the command touched them.
SKIP_PARTS = frozenset({".git", "node_modules", "__pycache__", ".venv"})
GIT_TIMEOUT_S = 600.0


class WorkspaceError(Exception):
    """The repo could not be cloned, or the run's folder could not be prepared."""


def git_env(token: str, base: Mapping[str, str]) -> dict[str, str]:
    """`base` plus the GitLab token as an HTTP header for git, and no prompts."""
    basic = base64.b64encode(f"oauth2:{token}".encode()).decode()
    return {
        **base,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,  # a planted ~/.gitconfig cannot redirect the token
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        "GIT_CONFIG_KEY_1": "core.symlinks",  # a committed symlink becomes a plain text file
        "GIT_CONFIG_VALUE_1": "false",
    }


def clone(
    git: str,
    url: str,
    ref: str,
    dest: Path,
    *,
    token: str,
    depth: int,
    env: Mapping[str, str],
    timeout_s: float = GIT_TIMEOUT_S,
) -> str:
    """Clone `ref` of `url` into `dest` (depth 0 = the whole history); returns HEAD's sha."""
    if ref.startswith("-") or url.startswith("-"):
        raise WorkspaceError("invalid ref or url")
    cmd = [git, "clone", "--single-branch", "--no-tags", "--branch", ref]
    if depth > 0:
        cmd += ["--depth", str(depth)]
    cmd += ["--", url, str(dest)]
    auth = git_env(token, env)
    try:
        done = subprocess.run(  # noqa: S603 - fixed git argv, no shell
            cmd, env=auth, capture_output=True, text=True, timeout=timeout_s, check=False
        )
        if done.returncode != 0:
            raise WorkspaceError(f"git clone failed: {scrub(done.stderr, token)[-400:]}")
        head = subprocess.run(  # noqa: S603
            [git, "-C", str(dest), "rev-parse", "HEAD"],
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise WorkspaceError("git clone timed out") from None
    except OSError as exc:
        raise WorkspaceError(f"git not available: {exc.__class__.__name__}") from None
    return head.stdout.strip()


def remove_symlinks(root: Path) -> int:
    """Delete every symlink under `root` without following any. Returns how many."""
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath, name)
            if path.is_symlink():
                path.unlink()
                removed += 1
        dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
    return removed


# Settings a repo may give its own commands; everything else (env, apiKeyHelper, hooks,
# statusLine, otelHeadersHelper…) could run code or send the Claude token elsewhere.
SETTINGS_KEPT = ("permissions", "model")


def sanitize_settings(claude_dir: Path) -> list[str]:
    """Keep only SETTINGS_KEPT in `.claude/settings.json` / `settings.local.json`; an unreadable
    file is removed. Returns the keys dropped."""
    dropped: list[str] = []
    for name in ("settings.json", "settings.local.json"):
        path = claude_dir / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            path.unlink()
            dropped.append(f"{name}: unreadable")
            continue
        if not isinstance(data, dict):
            path.unlink()
            continue
        dropped += [f"{name}: {key}" for key in data if key not in SETTINGS_KEPT]
        kept = {key: data[key] for key in SETTINGS_KEPT if key in data}
        permissions = kept.get("permissions")
        if isinstance(permissions, dict):  # the CLI flags decide the mode and the folders
            for key in ("defaultMode", "additionalDirectories"):
                if permissions.pop(key, None) is not None:
                    dropped.append(f"{name}: permissions.{key}")
        path.write_text(json.dumps(kept, indent=2), encoding="utf-8")
    return dropped


def needs_git(allowed_tools: list[str]) -> bool:
    """Whether a command may run git: `Bash`, `Bash(*)` or a `Bash(git …)` rule."""
    for tool in allowed_tools:
        rule = tool.replace(" ", "")
        if rule in ("Bash", "Bash(*)", "Bash(:*)") or rule.startswith("Bash(git"):
            return True
    return False


def reset_history(git: str, work: Path, env: Mapping[str, str]) -> None:
    """Replace `.git` with one commit of the cleaned tree: a command may run git (status, diff,
    log of its own changes) but no history or SENSITIVE_PATHS file is in reach."""
    remove(work / ".git")
    ident = {
        **env,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "CodePulse",
        "GIT_AUTHOR_EMAIL": "codepulse@localhost",
        "GIT_COMMITTER_NAME": "CodePulse",
        "GIT_COMMITTER_EMAIL": "codepulse@localhost",
    }
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--no-verify", "-m", "snapshot"]):
        try:
            done = subprocess.run(  # noqa: S603 - fixed git argv, no shell
                [git, "-C", str(work), *args],
                env=ident,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError(f"git {args[0]} failed: {exc.__class__.__name__}") from None
        if done.returncode != 0 and args[0] != "commit":  # an empty tree has nothing to commit
            raise WorkspaceError(f"git {args[0]} failed: {done.stderr[-300:]}")


def scrub(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text


def drop_sensitive(root: Path, patterns: list[str]) -> int:
    """Delete files matching SENSITIVE_PATHS (a committed `.env`, keys) before the model runs,
    as the review's checkout does. Returns how many."""
    removed = 0
    for path in list(root.rglob("*")):
        rel = PurePosixPath(path.relative_to(root).as_posix())
        if rel.parts and rel.parts[0] == ".git":
            continue
        if (path.is_file() or path.is_symlink()) and is_sensitive(rel, patterns):
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def shared_claude_dir(root: Path) -> Path:
    """The `.claude` folder of a shared repo clone: `.claude/` when it has one, else its root."""
    return root / ".claude" if (root / ".claude").is_dir() else root


def overlay(shared: Path, target: Path) -> list[str]:
    """Copy the shared .claude folder into `target` (a `.claude` folder) without replacing any
    file the repo has. Returns the shared files the repo's own replaced, relative to `.claude`."""
    replaced: list[str] = []
    for path in sorted(shared.rglob("*")):
        rel = path.relative_to(shared)
        if any(part in SKIP_PARTS for part in rel.parts) or path.is_symlink():
            continue
        dest = target / rel
        if path.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
        elif dest.exists():
            replaced.append(rel.as_posix())
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
    return replaced


Snapshot = dict[str, tuple[int, int]]


def snapshot(root: Path) -> Snapshot:
    """(size, mtime) of every file under `root` but .git and dependency folders."""
    out: Snapshot = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
        for name in filenames:
            path = Path(dirpath, name)
            try:
                stat = path.lstat()
            except OSError:
                continue
            if path.is_symlink():
                continue
            out[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return out


def changed(before: Snapshot, after: Snapshot) -> list[str]:
    """Files created or modified between the two snapshots."""
    return sorted(rel for rel, stat in after.items() if before.get(rel) != stat)


@dataclass
class Collected:
    files: list[dict[str, int | str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # over the size cap
    total: int = 0


def collect(work: Path, out: Path, edited: list[str], max_bytes: int) -> Collected:
    """What the run hands back: everything in `out/` (CODEPULSE_OUTPUT_DIR) plus the files the
    command created or changed in the clone, copied under `out/workspace/`. Stops adding at
    `max_bytes` (the rest is listed as skipped)."""
    result = Collected()
    room = max_bytes - sum(
        p.stat().st_size for p in out.rglob("*") if p.is_file() and not p.is_symlink()
    )
    for rel in edited:
        source = work / rel
        if source.is_symlink() or not source.is_file():
            continue
        size = source.stat().st_size
        if size > room:  # never copied: a 3 GB build output must not land on the volume first
            result.skipped.append(f"workspace/{rel}")
            continue
        room -= size
        dest = out / "workspace" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
    for path in sorted(p for p in out.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(out).as_posix()
        size = path.stat().st_size
        if result.total + size > max_bytes:
            path.unlink()
            result.skipped.append(rel)
            continue
        result.total += size
        result.files.append({"name": rel, "size": size})
    return result


def package(run_dir: Path, files: list[dict[str, int | str]]) -> str | None:
    """The one file to download, relative to `run_dir`: the only output itself, or a zip of
    them all. None when there is nothing."""
    out = run_dir / OUT
    if not files:
        return None
    if len(files) == 1:
        return f"{OUT}/{files[0]['name']}"
    with zipfile.ZipFile(run_dir / ZIP_NAME, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            archive.write(out / str(item["name"]), str(item["name"]))
    return ZIP_NAME


def finish(root: Path, before: Snapshot, max_bytes: int) -> tuple[Collected, Path | None]:
    """After the CLI: what the run hands back (symlinks dropped first) and the one file to
    download (the only output, or a zip of them all), or None."""
    work, out = root / WORK, root / OUT
    remove_symlinks(work)
    remove_symlinks(out)
    collected = collect(work, out, changed(before, snapshot(work)), max_bytes)
    rel = package(root, collected.files)
    return collected, (root / rel if rel else None)


SAFE_FILE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_file_name(name: str) -> str:
    """A plain file name (no folder, no leading dot) for what is stored and downloaded."""
    base = SAFE_FILE.sub("_", PurePosixPath(name.replace("\\", "/")).name).strip(" .")
    return base[:120] or "result"


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
