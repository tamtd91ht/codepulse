"""Which commands a repo offers: its own `.claude/commands` and the dự án's shared .claude repo.

Read through GitLab's archive API, only the commands folder (one request per source, no clone).
A command of the repo and a shared one with the same name: the repo's wins, as it does in the
run (app/commands/workspace.py lays the shared files under the repo's `.claude/` without
replacing any).
"""

import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from app.collector.gitlab.client import ArchiveTooLarge, GitLabClient, GitLabError
from app.review.checkout import CheckoutError, NoSuchPath, checkout
from app.review.claude_kit import _first_paragraph, _frontmatter

log = logging.getLogger(__name__)

# Commands are a few kilobytes of markdown; a bigger folder is not a commands folder.
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_COMMANDS = 300
MAX_PREVIEW_CHARS = 20_000
MAX_DESCRIPTION = 300
# What may follow "/": Claude Code's names (`review`, `report:weekly`, `fix-issue`, `v1.2`).
COMMAND_NAME = re.compile(r"^[A-Za-z0-9_][\w.\-]*(?::[\w.\-]+)*$")
# Where a shared repo keeps its commands: under `.claude/`, or at its root when the repo *is*
# the .claude folder (app/review/claude_repo.as_repo_layout).
SHARED_FOLDERS = (".claude/commands", "commands")


@dataclass
class Command:
    name: str  # what follows "/"
    source: str  # repo | shared
    path: str  # relative to the source repo
    description: str = ""
    argument_hint: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    body: str = ""  # the markdown after the frontmatter, cut to MAX_PREVIEW_CHARS
    # A shared command the repo's own command of the same name replaces.
    overridden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def command_name(rel: PurePosixPath) -> str | None:
    """`a/b.md` (relative to the commands folder) -> `a:b`; None for anything else."""
    if rel.suffix.lower() != ".md" or not rel.stem:
        return None
    name = ":".join((*rel.parts[:-1], rel.stem))
    return name if COMMAND_NAME.match(name) else None


def _tools(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [v.strip() for v in value if v.strip()]
    # `allowed-tools: Bash(git log:*), Read` — commas inside parentheses belong to the rule.
    parts, depth, current = [], 0, ""
    for char in value:
        depth += char == "("
        depth -= char == ")"
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return [p.strip() for p in parts if p.strip()]


def parse(rel: PurePosixPath, text: str, source: str, prefix: str) -> Command | None:
    name = command_name(rel)
    if name is None:
        return None
    meta, body = _frontmatter(text)
    description = meta.get("description")
    if not isinstance(description, str) or not description:
        description = _first_paragraph(body)
    hint = meta.get("argument-hint")
    model = meta.get("model")
    return Command(
        name=name,
        source=source,
        path=f"{prefix}/{rel}",
        description=str(description)[:MAX_DESCRIPTION],
        argument_hint=(hint if isinstance(hint, str) else " ".join(hint or []))[:200],
        allowed_tools=_tools(meta.get("allowed-tools")),
        model=model if isinstance(model, str) else "",
        body=body[:MAX_PREVIEW_CHARS],
    )


def scan(folder: Path, source: str, prefix: str) -> list[Command]:
    """Every command file under `folder` (a commands folder on disk), sorted by name."""
    if not folder.is_dir():
        return []
    out: list[Command] = []
    for path in sorted(folder.rglob("*.md")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        command = parse(PurePosixPath(path.relative_to(folder).as_posix()), text, source, prefix)
        if command is not None:
            out.append(command)
        if len(out) >= MAX_COMMANDS:
            break
    return sorted(out, key=lambda c: c.name)


def read_folder(
    client: GitLabClient,
    external_id: int,
    ref: str,
    folder: str,
    source: str,
    sensitive_paths: list[str],
) -> list[Command] | None:
    """The commands of one folder of a repo at `ref`; None when the folder is not there."""
    try:
        with checkout(
            client,
            external_id,
            ref,
            max_archive_bytes=MAX_ARCHIVE_BYTES,
            sensitive_paths=sensitive_paths,
            path=folder,
        ) as (root, _):
            if not (root / folder).is_dir():
                return None
            return scan(root / folder, source, folder)
    except NoSuchPath:
        return None
    except GitLabError as exc:
        if exc.status_code == 404:
            return None
        raise


def repo_commands(
    client: GitLabClient, external_id: int, ref: str, sensitive_paths: list[str]
) -> list[Command]:
    return read_folder(client, external_id, ref, ".claude/commands", "repo", sensitive_paths) or []


def shared_commands(
    client: GitLabClient, external_id: int, ref: str, sensitive_paths: list[str]
) -> list[Command]:
    for folder in SHARED_FOLDERS:
        found = read_folder(client, external_id, ref, folder, "shared", sensitive_paths)
        if found is not None:
            return found
    return []


def merge(own: list[Command], shared: list[Command]) -> list[Command]:
    """The repo's commands, then the shared ones (marked when the repo's replaces them)."""
    names = {c.name for c in own}
    for command in shared:
        command.overridden = command.name in names
    return sorted([*own, *shared], key=lambda c: (c.name, c.source != "repo"))


def safe_listing(
    client: GitLabClient, external_id: int, ref: str, folder_reader: Any, sensitive: list[str]
) -> tuple[list[Command], str | None]:
    """(commands, why not) — a GitLab or archive problem becomes a message, not a 500."""
    try:
        return folder_reader(client, external_id, ref, sensitive), None
    except (GitLabError, ArchiveTooLarge, CheckoutError, OSError) as exc:
        log.warning("commands not listed", extra={"error": exc.__class__.__name__})
        return [], f"{exc.__class__.__name__}: {str(exc)[:200]}"
