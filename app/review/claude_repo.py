"""The dự án's shared .claude repo, and how its instruction files join the repo's own in the
deep review (outside the spec, requested by the user; see PROGRESS.md).

Many dự án keep one GitLab repo with the Claude configuration every service shares (skills,
agents, rules, CLAUDE.md), either as a `.claude/` folder or as the folder's contents at the repo
root. The deep review downloads it (same archive path and filters as the reviewed repo),
discovers its instruction files like the repo's own, and quotes both to the model as data:
the shared ones first, the repo's after them, the repo's winning where they disagree.

Everything that was or was not loaded, and why, goes into the review's basis, so an admin can
check that the skills they expect were in front of the model.
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.collector.gitlab.client import ArchiveTooLarge, GitLabClient, GitLabError
from app.models import BusinessProject, Project
from app.review.checkout import CheckoutError, checkout
from app.review.claude_kit import MAX_FILE_CHARS, SHARED_MOUNT, KitFile, mount_shared, scan
from app.review.discover import CLIPPED, Doc, ProjectDocs, discover, instruction_kind
from app.review.rules import rule_paths

log = logging.getLogger(__name__)

# A configuration repo, not a service: anything bigger is not what we think it is.
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
# What a .claude folder holds at its top level; a repo with these at its root *is* the folder.
CLAUDE_ENTRIES = ("skills", "agents", "commands", "rules", "CLAUDE.md", "settings.json")


def include_globs(spec: str | None) -> list[str]:
    """`claude_repo_include`: comma/newline separated globs relative to the .claude folder."""
    return rule_paths(spec)


def as_repo_layout(root: Path) -> Path:
    """A directory where the configuration sits under `.claude/`, as discover() expects."""
    if (root / ".claude").is_dir():
        return root
    if not any((root / name).exists() for name in CLAUDE_ENTRIES):
        return root  # nothing recognisable; discover() still reads a CLAUDE.md/AGENTS.md
    wrapped = root.parent / "claude-repo"
    wrapped.mkdir()
    shutil.move(str(root), str(wrapped / ".claude"))
    return wrapped


@dataclass
class SharedConfig:
    """What the review got from the shared repo; `docs` is None when it could not be read.
    `files`: its whole catalogue (claude_kit), with paths where the agent can Read them."""

    info: dict[str, Any]
    docs: ProjectDocs | None = None
    files: list[KitFile] = field(default_factory=list)


def load_shared_config(
    client: GitLabClient,
    business: BusinessProject | None,
    connection_id: int | None,
    *,
    sensitive_paths: list[str],
    mount_into: Path | None = None,
    file_chars: int = MAX_FILE_CHARS,
) -> SharedConfig | None:
    """None when the dự án has no shared repo. Never raises for GitLab or archive trouble: the
    review goes on with the repo's own files and the basis records why. The texts are read into
    memory; the downloaded copy is gone when this returns.

    `mount_into`: the deep review's checkout. The .claude folder is copied there under
    SHARED_MOUNT so the agent can Read every file of it, not only the ones quoted."""
    if business is None or not business.claude_repo_external_id or not business.claude_repo_path:
        return None
    info: dict[str, Any] = {
        "repo": business.claude_repo_path,
        "ref": business.claude_repo_ref,
        "include": include_globs(business.claude_repo_include),
    }
    if business.claude_repo_connection_id != connection_id:
        # The review's GitLab client holds another instance's token.
        return SharedConfig({**info, "status": "other_connection"})
    external_id = business.claude_repo_external_id
    try:
        ref = business.claude_repo_ref or str(client.project(external_id)["default_branch"])
        sha = str(client.commit(external_id, ref).get("id") or ref)
        with checkout(
            client,
            external_id,
            sha,
            max_archive_bytes=MAX_ARCHIVE_BYTES,
            sensitive_paths=sensitive_paths,
        ) as (root, stats):
            layout = as_repo_layout(root)
            docs = discover(layout, info["include"])
            mounted, mount_error = _mount(layout / ".claude", mount_into, business)
            # Not mounted (preview, or the path is taken): named after the repo, not readable.
            prefix = SHARED_MOUNT if mounted else f"{business.claude_repo_path}:.claude"
            files = scan(mounted or layout / ".claude", prefix, "shared", file_chars)
    except (GitLabError, ArchiveTooLarge, CheckoutError, OSError, KeyError) as exc:
        log.warning(
            "shared .claude repo not read",
            extra={"repo": business.claude_repo_path, "error": exc.__class__.__name__},
        )
        return SharedConfig({**info, "status": "error", "detail": str(exc)[:200]})
    return SharedConfig(
        {
            **info,
            "ref": ref,
            "sha": sha[:12],
            "files": stats.files,
            "status": "used",
            "mounted": bool(mount_into and mounted),
            **({"mount_error": mount_error} if mount_error else {}),
        },
        docs,
        files,
    )


def _mount(
    claude_dir: Path, root: Path | None, business: BusinessProject
) -> tuple[Path | None, str | None]:
    """The shared folder copied into the checkout, or None and why it was not. A failed copy
    costs only the agent's on-demand reading: the files are still read, quoted and indexed
    from the download."""
    if root is None:
        return None, None
    try:
        return mount_shared(claude_dir, root), None
    except OSError as exc:
        log.warning(
            "shared .claude repo not copied into the checkout",
            extra={"repo": business.claude_repo_path, "error": exc.__class__.__name__},
        )
        return None, exc.__class__.__name__


def load_repo_claude(
    client: GitLabClient,
    project: Project,
    sha: str,
    *,
    changed: set[str],
    sensitive_paths: list[str],
    file_chars: int = MAX_FILE_CHARS,
) -> tuple[list[KitFile], str | None]:
    """The reviewed repo's own `.claude` at the reviewed commit, for the first pass (the deep
    pass reads it from its full checkout). Only that folder is downloaded. A file the change
    itself edits is left out (its author could have written it). ([], why) when the repo has
    none or it cannot be read; the review goes on without it."""
    try:
        with checkout(
            client,
            project.external_id,
            sha,
            max_archive_bytes=MAX_ARCHIVE_BYTES,
            sensitive_paths=sensitive_paths,
            path=".claude",
        ) as (root, _):
            files = scan(root / ".claude", ".claude", "repo", file_chars)
    except GitLabError as exc:
        if exc.status_code == 404:  # no such folder at this commit
            return [], "none"
        log.warning("repo .claude not read", extra={"error": exc.__class__.__name__})
        return [], "error"
    except (ArchiveTooLarge, CheckoutError, OSError) as exc:  # old GitLab: whole repo
        log.warning("repo .claude not read", extra={"error": exc.__class__.__name__})
        return [], "error"
    files = [f for f in files if f.path not in changed]
    return files, None if files else "none"


@dataclass
class Packed:
    text: str
    records: list[dict[str, Any]] = field(default_factory=list)


def pack_instructions(groups: list[tuple[str, str, str, list[Doc]]], limit: int) -> Packed:
    """Quote instruction files of several sources inside `limit` characters.

    `groups`: (source id, heading, path prefix shown to the model, docs) in prompt order; the
    prefix keeps a shared file from passing for one in the checkout. Each non-empty source is
    guaranteed an equal share; what one does not use goes to the others. Every file gets a
    record: used, partial (only its start fits, or discover() clipped it) or cut (not in the
    prompt), and `quoted`: how many characters of the file the prompt holds."""
    live = [group for group in groups if group[3]]
    if not live:
        return Packed("")
    # Exactly what each group adds to the text: its heading and every "### path\ntext" block,
    # each followed by the blank-line separator.
    sizes = [
        len(f"#### {heading}") + 2 + sum(len(f"### {prefix}{d.path}\n{d.text}") + 2 for d in docs)
        for _, heading, prefix, docs in live
    ]
    budgets = [0] * len(live)
    remaining, open_ = limit, list(range(len(live)))
    while open_ and remaining > 0:
        share = remaining // len(open_)
        if share == 0:
            break
        for i in list(open_):
            take = min(share, sizes[i] - budgets[i])
            budgets[i] += take
            remaining -= take
            if budgets[i] >= sizes[i]:
                open_.remove(i)
    parts: list[str] = []
    records: list[dict[str, Any]] = []
    for (source, heading, prefix, docs), budget in zip(live, budgets, strict=True):
        parts.append(f"#### {heading}")
        budget -= len(parts[-1]) + 2
        for doc in docs:
            block = f"### {prefix}{doc.path}\n{doc.text}"
            record: dict[str, Any] = {
                "path": doc.path,
                "kind": instruction_kind(doc.path),
                "source": source,
            }
            if budget <= len(doc.path) + 20:
                record["status"], record["quoted"] = "cut", 0
            elif len(block) > budget:
                parts.append(block[:budget].rstrip() + "\n(… cắt bớt)")
                record["status"] = "partial"
                record["quoted"] = max(budget - (len(block) - len(doc.text)), 0)
                budget = 0
            else:
                parts.append(block)
                record["status"] = "partial" if doc.clipped else "used"
                record["quoted"] = len(doc.text) - (len(CLIPPED) if doc.clipped else 0)
                budget -= len(block) + 2
            records.append(record)
    return Packed("\n\n".join(parts), records)
