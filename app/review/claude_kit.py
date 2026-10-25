"""Which Claude files (skills, agents, commands, rules) the deep review hands the model, chosen by
what the change touches (outside the spec, requested by the user; see PROGRESS.md and
docs/specs/18 §Chọn file .claude theo commit).

A dự án's .claude folder easily holds 50 files and several times the prompt budget, so loading
them in a fixed order drops whatever comes last (in practice the review commands and security
skills). Instead:

- every file is catalogued (kind, name, the `description:` of its frontmatter, `paths:` globs);
- the project's own review procedure, its review commands (`/review-mr`, `/review-api`…), is
  quoted first, under its own budget: it is how the team reviews a change;
- the rest is ranked against the change (paths, identifiers in the diff, title): `paths:` that
  match a changed file, files a review command names, the admin's pinned globs, then term
  overlap weighted by rarity. Only the few best are quoted (MAX_QUOTED);
- everything else is an index (path, description, why it may matter), the way Claude Code lists
  skills: a review is a deliberate act, so the agent loads what the change needs when it needs
  it instead of starting with everything. The shared repo is copied into the checkout under
  SHARED_MOUNT, so the agent can Read any of it.

Deterministic; the texts are redacted and are data for the model, never instructions to
CodePulse.
"""

import math
import re
import shutil
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from app.review.discover import CLIPPED, Doc, instruction_kind
from app.review.prepare import DiffFile, redact

# Where the shared .claude repo sits inside the checkout during a deep review. Not a file of
# the reviewed repo: never valid as evidence or as the place of a finding.
SHARED_MOUNT = ".codepulse/claude-shared"

MAX_FILE_CHARS = 8_000
MAX_PROCEDURE_FILE_CHARS = 15_000
MAX_PROCEDURE_CHARS = 30_000
MAX_CATALOG_CHARS = 12_000
MAX_DESCRIPTION = 200
MAX_FILES = 300
# Under this score a file is only listed in the catalog, not quoted.
MIN_SCORE = 4.0
# Quoted up front; the rest the agent Reads from the index when the review calls for it.
MAX_QUOTED = 6

# Review commands about a change (MR / commit / diff) go before the topical ones (/review-api).
CHANGE_REVIEW = re.compile(r"mr|merge|pr\b|pull|commit|diff|change|code|dev", re.I)
REVIEWISH = re.compile(r"review|audit", re.I)
_STOP_WORDS = """the and for with from this that are was were not but you your can will should must
    any all into when then than have has had use used using each per its our out get set new
    old none null true false return returns def class self import const let var void public
    private protected static final string int bool list dict map value values data type file
    files line lines code test tests src main app lib java python json yaml md http https www
    com org vn una nhu cho cac cua khi voi duoc khong mot nhung trong nay phai"""
STOP = frozenset(_STOP_WORDS.split())


@dataclass
class KitFile:
    path: str  # as the agent reads it, relative to the checkout
    rel: str  # relative to its .claude folder: what "File nạp thêm" globs match
    source: str  # "shared" | "repo"
    kind: str  # skill | agent | command | rules | instructions
    name: str  # "/review-mr-dev", "file-upload-security", "critical/tenant-isolation"
    description: str
    globs: list[str]
    text: str  # redacted, capped
    chars: int  # size before capping
    procedure: bool = False
    score: float = 0.0
    why: list[str] = field(default_factory=list)  # why it was chosen, for the basis

    def doc(self) -> Doc:
        return Doc(self.path, self.text)


@dataclass
class Selection:
    procedure: list[KitFile] = field(default_factory=list)  # quoted in their own section
    ranked: list[KitFile] = field(default_factory=list)  # quoted while the budget lasts
    catalog: list[KitFile] = field(default_factory=list)  # listed only, readable on demand


# -- reading ---------------------------------------------------------------------------------


def _frontmatter(text: str) -> tuple[dict[str, str | list[str]], str]:
    """`---` YAML frontmatter, the subset Claude files use: `key: value`, inline lists and
    `- item` lists. Anything else is ignored (no YAML library, no surprises)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta: dict[str, str | list[str]] = {}
    key = ""
    for raw in text[3:end].splitlines():
        line = raw.rstrip()
        item = re.match(r"^\s*-\s+(.+)$", line)
        if item and key:
            value = meta.get(key)
            meta[key] = [*(value if isinstance(value, list) else []), _unquote(item[1])]
            continue
        pair = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not pair:
            continue
        key, value = pair[1].lower(), pair[2].strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [_unquote(v) for v in value[1:-1].split(",") if v.strip()]
        elif value in ("", "|", ">", "|-", ">-"):
            meta[key] = ""
        else:
            meta[key] = _unquote(value)
    return meta, text[end + 4 :].lstrip("\n")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _first_paragraph(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "```", "---", "|", "<")):
            return line
    return ""


def _name(rel: PurePosixPath, kind: str, meta: dict[str, str | list[str]]) -> str:
    parts = rel.parts
    if kind == "command":
        # commands/review/mr.md is /review:mr in Claude Code.
        return "/" + ":".join((*parts[1:-1], rel.stem))
    if kind == "skill":
        return parts[1] if len(parts) > 2 else rel.stem
    if kind == "agent":
        named = meta.get("name")
        return named if isinstance(named, str) and named else rel.stem
    if kind == "rules":
        return str(PurePosixPath(*parts[1:]).with_suffix(""))
    return str(rel)


def _wanted(rel: PurePosixPath) -> bool:
    parts = rel.parts
    if rel.suffix.lower() != ".md":
        return False
    if len(parts) == 1:
        return parts[0] == "CLAUDE.md"
    top = parts[0]
    if top == "skills":
        return len(parts) == 3 and parts[2] == "SKILL.md"
    return top in ("agents", "commands", "rules")


def scan(claude_dir: Path, prefix: str, source: str) -> list[KitFile]:
    """Every skill, agent, command, rule and CLAUDE.md under `claude_dir` (a `.claude` folder);
    `prefix` is that folder's path relative to the checkout, so the agent can Read it."""
    if not claude_dir.is_dir():
        return []
    out: list[KitFile] = []
    for path in sorted(claude_dir.rglob("*.md")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = PurePosixPath(path.relative_to(claude_dir).as_posix())
        if not _wanted(rel):
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, body = _frontmatter(raw)
        kind = instruction_kind(f".claude/{rel}")
        name = _name(rel, kind, meta)
        description = meta.get("description")
        if not isinstance(description, str) or not description:
            description = _first_paragraph(body)
        globs = meta.get("paths") or meta.get("globs") or []
        if isinstance(globs, str):
            globs = [g.strip() for g in globs.split(",") if g.strip()]
        procedure = kind == "command" and bool(REVIEWISH.search(name))
        limit = MAX_PROCEDURE_FILE_CHARS if procedure else MAX_FILE_CHARS
        text = redact(raw)
        out.append(
            KitFile(
                path=f"{prefix.rstrip('/')}/{rel}",
                rel=str(rel),
                source=source,
                kind=kind,
                name=name,
                description=redact(" ".join(description.split()))[:MAX_DESCRIPTION],
                globs=[g for g in globs if g][:20],
                text=text if len(text) <= limit else text[:limit].rstrip() + CLIPPED,
                chars=len(text),
                procedure=procedure,
            )
        )
        if len(out) == MAX_FILES:
            break
    return out


def mount_shared(claude_dir: Path, root: Path) -> Path | None:
    """Copy the shared .claude folder into the checkout under SHARED_MOUNT (Read, Grep and Glob
    only work inside the working directory). None when the repo already has that path.

    On a failed copy the partial folder is removed before the error goes up, so the agent never
    reads half a configuration that the prompt does not mention."""
    target = root / SHARED_MOUNT
    if target.exists() or not claude_dir.is_dir():
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(claude_dir, target, symlinks=False, ignore_dangling_symlinks=True)
    except OSError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return target


# -- ranking ---------------------------------------------------------------------------------


def _terms(text: str) -> list[str]:
    """Lower-case words of identifiers and prose: `tenantId` and `tenant_id` both give
    "tenant"; short and common words are dropped."""
    out: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z0-9]+", text):
        for part in re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", word):
            low = part.lower()
            if len(low) >= 3 and not low.isdigit() and low not in STOP:
                out.append(low)
    return out


def change_terms(files: list[DiffFile], title: str, description: str | None) -> Counter[str]:
    """What the change is about: its paths (weighted), changed lines and title."""
    terms: Counter[str] = Counter()
    for f in files:
        for t in _terms(f.path):
            terms[t] += 3
        for hunk in f.hunks:
            for op, _, text in hunk.lines:
                if op in ("+", "-"):
                    terms.update(_terms(text))
    for t in _terms(f"{title}\n{description or ''}"):
        terms[t] += 2
    return terms


def _key_terms(f: KitFile) -> set[str]:
    headings = " ".join(line for line in f.text.splitlines() if line.lstrip().startswith("#"))
    return set(_terms(f"{f.name} {f.path} {f.description} {headings}"))


def _matches(globs: list[str], paths: list[str]) -> bool:
    for pattern in globs:
        pattern = pattern.lstrip("./")
        for path in paths:
            # `src/**/*.ts` must also match `src/a.ts`; fnmatch's * already crosses "/".
            if fnmatch(path, pattern) or fnmatch(path, pattern.replace("**/", "")):
                return True
    return False


def _referenced(f: KitFile, procedures: list[KitFile]) -> bool:
    """A review command that names this skill / rule / agent (`file-upload-security`,
    `rules/critical/tenant-isolation.md`, `@risk-audit-agent`)."""
    stem = PurePosixPath(f.path).stem if f.kind != "skill" else f.name
    needles = {f.name.lstrip("/").lower(), stem.lower()} - {"skill", "claude", "readme"}
    # Whole names only: `deploy` is not named by `deploy-checklist`.
    patterns = [
        re.compile(rf"(?<![\w-]){re.escape(n)}(?![\w-])", re.I) for n in needles if len(n) >= 5
    ]
    return any(rx.search(p.text) for p in procedures if p is not f for rx in patterns)


def select(
    files: list[KitFile],
    diff_files: list[DiffFile],
    title: str,
    description: str | None,
    pinned: Sequence[str] = (),
) -> Selection:
    """Split the catalogue into the review procedure, the files worth quoting for this change
    (best first) and the ones only listed. `pinned`: globs relative to a .claude folder that
    the admin always wants loaded ("File nạp thêm")."""
    changed = [f.path for f in diff_files]
    terms = change_terms(diff_files, title, description)
    procedures = [f for f in files if f.procedure]
    keys = {id(f): _key_terms(f) for f in files}
    bodies = {id(f): set(_terms(f.text)) for f in files}
    df: Counter[str] = Counter()
    for f in files:
        df.update(keys[id(f)] | bodies[id(f)])
    n = max(len(files), 1)

    def weight(t: str) -> float:
        # A word every file has says nothing about which one fits.
        return math.log((n + 1) / df[t]) if df[t] else 0.0

    for f in files:
        why: list[str] = []
        score = 0.0
        if any(fnmatch(f.rel, g) for g in pinned):
            score += 100
            why.append("nạp thêm")
        if f.procedure:
            score += 30 if CHANGE_REVIEW.search(f.name) else 0
            why.append("lệnh review")
        elif f.kind == "instructions":
            score += 40
            why.append("hướng dẫn chung")
        if f.globs:
            if _matches(f.globs, changed):
                score += 50
                why.append("paths khớp file thay đổi")
            elif "nạp thêm" not in why:
                # A path-scoped rule that does not apply here (Claude Code semantics).
                f.score, f.why = 0.0, ["paths không khớp"]
                continue
        if not f.procedure and _referenced(f, procedures):
            score += 20
            why.append("lệnh review nhắc tới")
        if f.kind in ("agent", "skill") and REVIEWISH.search(f.name):
            score += 5
        hits = sorted(((weight(t), t) for t in keys[id(f)] if t in terms), reverse=True)
        score += 3 * sum(w for w, _ in hits)
        body = sum(weight(t) for t in bodies[id(f)] - keys[id(f)] if t in terms)
        score += min(body * 0.3, 15)
        if hits:
            why.append("khớp: " + ", ".join(t for _, t in hits[:5]))
        f.score, f.why = round(score, 1), why

    chosen: list[KitFile] = []
    used = 0
    for f in sorted(procedures, key=lambda f: -f.score):
        size = len(f.text) + len(f.path) + 8
        if used + size <= MAX_PROCEDURE_CHARS or not chosen:
            chosen.append(f)
            used += size
    rest = [f for f in files if f not in chosen]
    by_score = sorted(rest, key=lambda f: (-f.score, f.path))
    ranked = [f for f in by_score if f.score >= MIN_SCORE][:MAX_QUOTED]
    # The index: likely ones first (they are what the agent should open), then by path.
    catalog = [f for f in by_score if f.score >= MIN_SCORE and f not in ranked]
    catalog += sorted((f for f in rest if f.score < MIN_SCORE), key=lambda f: f.path)
    return Selection(chosen, ranked, catalog)


# -- rendering -------------------------------------------------------------------------------


def render_procedure(files: list[KitFile]) -> str:
    return "\n\n".join(f"### {f.path} ({f.name})\n{f.text}" for f in files)


def render_catalog(files: list[KitFile], origin: dict[str, str]) -> str:
    """One line per file the prompt does not quote: the agent Reads the ones it needs.
    `origin`: source -> how to name it ("repo shared/cfg")."""
    lines: list[str] = []
    used = 0
    for i, f in enumerate(files):
        hint = f" [liên quan: {'; '.join(f.why)}]" if f.why and f.score > 0 else ""
        where = origin.get(f.source, f.source)
        line = f"- {f.path} ({f.kind} {f.name}, {where}): {f.description}{hint}"
        if used + len(line) > MAX_CATALOG_CHARS:
            lines.append(
                f"- … và {len(files) - i} file khác (Glob `**/*.md` trong các thư mục trên)"
            )
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
