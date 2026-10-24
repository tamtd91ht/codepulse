"""What a repository says about itself (docs/specs/18 §Hồ sơ dự án).

Deterministic: README, the instruction files AI tools already keep in the repo (`CLAUDE.md`,
`AGENTS.md`, `.claude/rules`, reviewer agents, Cursor / Copilot / Codex / Windsurf / Cline
files), security and architecture docs, dependency manifests, and the directory shape. The
text is capped and handed to the model as data; nothing here is executed or treated as an
instruction to CodePulse.
"""

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from app.review.prepare import redact

MAX_FILE_CHARS = 8_000
MAX_TOTAL_CHARS = 40_000
MAX_TREE_ENTRIES = 80
SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", "target", "__pycache__", ".next"}

README_NAMES = ("README.md", "README.rst", "README.txt", "README", "readme.md")
# Fixed instruction files, in the order AI tools look for them.
INSTRUCTION_FILES = (
    "CLAUDE.md",
    ".claude/CLAUDE.md",
    "AGENTS.md",
    ".codex/AGENTS.md",
    "codex.md",
    ".cursorrules",
    ".windsurfrules",
    ".clinerules",
    ".github/copilot-instructions.md",
    "GEMINI.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
)
# Globs (posix, relative) for rule sets and reviewer definitions.
INSTRUCTION_GLOBS = (
    ".claude/rules/*.md",
    ".claude/rules/*/*.md",
    ".claude/agents/*review*.md",
    ".claude/agents/*audit*.md",
    ".claude/agents/*security*.md",
    ".claude/commands/*review*.md",
    ".cursor/rules/*",
    ".github/instructions/*.md",
    "docs/*security*.md",
    "docs/*/*security*.md",
    "docs/*architecture*.md",
    "docs/*/*architecture*.md",
    "docs/specs/*.md",
)
MANIFESTS = {
    "pyproject.toml": "Python",
    "requirements.txt": "Python",
    "Pipfile": "Python",
    "package.json": "JavaScript/TypeScript",
    "pom.xml": "Java (Maven)",
    "build.gradle": "Java/Kotlin (Gradle)",
    "build.gradle.kts": "Kotlin (Gradle)",
    "go.mod": "Go",
    "Cargo.toml": "Rust",
    "Gemfile": "Ruby",
    "composer.json": "PHP",
    "pubspec.yaml": "Dart/Flutter",
    "mix.exs": "Elixir",
    "Dockerfile": "Docker",
    "docker-compose.yml": "Docker Compose",
    "docker-compose.yaml": "Docker Compose",
}
FRAMEWORK_HINTS = (
    ("fastapi", "FastAPI"),
    ("django", "Django"),
    ("flask", "Flask"),
    ("sqlalchemy", "SQLAlchemy"),
    ("spring-boot", "Spring Boot"),
    ("vertx", "Vert.x"),
    ("quarkus", "Quarkus"),
    ("micronaut", "Micronaut"),
    ('"express"', "Express"),
    ("@nestjs", "NestJS"),
    ('"next"', "Next.js"),
    ('"react"', "React"),
    ('"vue"', "Vue"),
    ("@angular", "Angular"),
    ("gin-gonic", "Gin"),
    ("labstack/echo", "Echo"),
    ("rails", "Rails"),
    ("laravel", "Laravel"),
    ("kafka", "Kafka"),
    ("rabbitmq", "RabbitMQ"),
    ("amqp", "AMQP"),
    ("redis", "Redis"),
    ("mongodb", "MongoDB"),
    ("mongo", "MongoDB"),
    ("postgres", "PostgreSQL"),
    ("psycopg", "PostgreSQL"),
    ("mysql", "MySQL"),
    ("elasticsearch", "Elasticsearch"),
    ("opensearch", "OpenSearch"),
    ("grpc", "gRPC"),
    ("graphql", "GraphQL"),
    ("keycloak", "Keycloak"),
    ("jsonwebtoken", "JWT"),
    ("pyjwt", "JWT"),
    ("jjwt", "JWT"),
    ("stripe", "Stripe"),
    ("minio", "MinIO / S3"),
    ("aws-sdk", "AWS SDK"),
    ("boto3", "AWS SDK"),
    ("prisma", "Prisma"),
    ("typeorm", "TypeORM"),
    ("hibernate", "Hibernate"),
    ("flyway", "Flyway"),
    ("liquibase", "Liquibase"),
    ("alembic", "Alembic"),
)
EXTENSION_LANGUAGES = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".kt": "Kotlin",
    ".go": "Go",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".rs": "Rust",
    ".swift": "Swift",
    ".dart": "Dart",
    ".sql": "SQL",
    ".vue": "Vue",
    ".scala": "Scala",
    ".ex": "Elixir",
    ".tf": "Terraform",
    ".yaml": "YAML",
    ".yml": "YAML",
}


@dataclass
class Doc:
    path: str
    text: str


@dataclass
class ProjectDocs:
    readme: Doc | None = None
    instructions: list[Doc] = field(default_factory=list)
    manifests: list[Doc] = field(default_factory=list)
    languages: list[tuple[str, int]] = field(default_factory=list)  # (language, files)
    frameworks: list[str] = field(default_factory=list)
    tree: list[str] = field(default_factory=list)
    files: int = 0

    @property
    def instruction_paths(self) -> list[str]:
        return [d.path for d in self.instructions]


def _read(path: Path, limit: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = redact(text)
    return text if len(text) <= limit else text[:limit].rstrip() + "\n(… rút gọn)"


def _walk(root: Path) -> tuple[list[str], Counter[str], int]:
    """Relative file paths (posix), language counts and total count; skips vendored dirs."""
    paths: list[str] = []
    languages: Counter[str] = Counter()
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        rel_dir = Path(dirpath).relative_to(root)
        for name in filenames:
            rel = PurePosixPath(rel_dir.as_posix()) / name if rel_dir.parts else PurePosixPath(name)
            paths.append(str(rel))
            total += 1
            lang = EXTENSION_LANGUAGES.get(Path(name).suffix.lower())
            if lang:
                languages[lang] += 1
    return paths, languages, total


def _tree(paths: list[str]) -> list[str]:
    """Top-level entries and second-level directories: enough to see the shape."""
    top: Counter[str] = Counter()
    second: set[str] = set()
    for p in paths:
        parts = PurePosixPath(p).parts
        top[parts[0]] += 1
        if len(parts) > 2:
            second.add(f"{parts[0]}/{parts[1]}/")
    out = [
        f"{name}/ ({n} files)" if n > 1 or "." not in name else name
        for name, n in top.most_common()
    ]
    out += sorted(second)
    return out[:MAX_TREE_ENTRIES]


def discover(root: Path) -> ProjectDocs:
    docs = ProjectDocs()
    paths, languages, total = _walk(root)
    docs.files = total
    docs.languages = languages.most_common(6)
    docs.tree = _tree(paths)
    budget = MAX_TOTAL_CHARS

    for name in README_NAMES:
        if (root / name).is_file():
            docs.readme = Doc(name, _read(root / name, min(MAX_FILE_CHARS, budget)))
            budget -= len(docs.readme.text)
            break

    seen: set[str] = set()
    candidates: list[str] = [p for p in INSTRUCTION_FILES if (root / p).is_file()]
    hidden = sorted(_hidden_files(root))  # os.walk above skipped dot-directories on purpose
    for pattern in INSTRUCTION_GLOBS:
        candidates += sorted(p for p in paths + hidden if fnmatch(p, pattern))
    for rel in candidates:
        if rel in seen or budget <= 0:
            continue
        seen.add(rel)
        text = _read(root / rel, min(MAX_FILE_CHARS, budget))
        if text.strip():
            docs.instructions.append(Doc(rel, text))
            budget -= len(text)

    hints: list[str] = []
    for name, label in MANIFESTS.items():
        path = root / name
        if path.is_file():
            text = _read(path, 3_000)
            docs.manifests.append(Doc(name, text))
            lowered = text.lower()
            hints += [fw for needle, fw in FRAMEWORK_HINTS if needle in lowered]
            if label not in [lang for lang, _ in docs.languages]:
                hints.append(label)
    docs.frameworks = list(dict.fromkeys(hints))
    return docs


def _hidden_files(root: Path) -> list[str]:
    """Files under dot-directories we care about (.claude, .cursor, .github, .codex)."""
    out: list[str] = []
    for top in (".claude", ".cursor", ".github", ".codex"):
        base = root / top
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                out.append(Path(dirpath, name).relative_to(root).as_posix())
    return out


def render(docs: ProjectDocs) -> str:
    """The onboarding packet for the profile call and the deep review prompt."""
    parts: list[str] = []
    if docs.languages:
        parts.append(
            "Languages (by file count): "
            + ", ".join(f"{lang} ({n})" for lang, n in docs.languages)
            + f"; {docs.files} files"
        )
    if docs.frameworks:
        parts.append("Dependencies / frameworks seen in manifests: " + ", ".join(docs.frameworks))
    if docs.tree:
        parts.append("Repository shape:\n" + "\n".join(f"  {e}" for e in docs.tree))
    if docs.readme:
        parts.append(f"### {docs.readme.path}\n{docs.readme.text}")
    for doc in docs.instructions:
        parts.append(f"### {doc.path}\n{doc.text}")
    for doc in docs.manifests:
        parts.append(f"### {doc.path}\n{doc.text}")
    text = "\n\n".join(parts)
    return text if len(text) <= MAX_TOTAL_CHARS else text[:MAX_TOTAL_CHARS]


def mentions_review_rules(docs: ProjectDocs) -> bool:
    return any(re.search(r"review|rule|security|tenant", d.path, re.I) for d in docs.instructions)
