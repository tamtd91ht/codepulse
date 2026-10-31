"""What a close request's check may show of the repository's current files (outside the spec,
requested by the user 2026-10-31; app/workers/closure_jobs.gather_sources).

An argument like "prod reads the key from the k8s environment" is checked on the files it names,
and those are exactly the files that hold secrets. Nothing of a value is ever shown:
- a configuration file (`.env*`, yaml, properties, ini, toml, json, tfvars…, or a SENSITIVE_PATHS
  match): `mask_config` keeps the keys, replaces every value by a fixed label (a literal and its
  length, or where the value comes from), and hides comments, key blocks and multi-line values;
- a source file: `mask_code` hides the literal values of secret-looking names, key blocks, and
  what `redact` catches.
`looks_secret` refuses an argument that pastes a secret.
"""

import re

from app.collector.gitlab.diffstat import is_ignored
from app.review.prepare import redact

MAX_LINES = 400
MAX_LINE = 500

CONFIG_EXTENSIONS = frozenset(
    {
        "yml",
        "yaml",
        "properties",
        "ini",
        "toml",
        "conf",
        "cfg",
        "cnf",
        "tfvars",
        "json",
        "xml",
        "env",
        "plist",
        "hcl",
    }
)


def is_config(path: str, sensitive_paths: list[str]) -> bool:
    """A file shown through `mask_config`: SENSITIVE_PATHS on the whole path or the file name
    alone (".env*" must catch config/.env-prod), `.env…` names, configuration extensions."""
    name = path.rsplit("/", 1)[-1].lower()
    if is_ignored(path, sensitive_paths) or is_ignored(name, sensitive_paths):
        return True
    if name.startswith(".env") or name.endswith(".env") or name in {"env", "secrets"}:
        return True
    return "." in name and name.rsplit(".", 1)[-1] in CONFIG_EXTENSIONS


# -- values ------------------------------------------------------------------------------------

ENV_REF = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$|^\$[A-Z_][A-Z0-9_]*$")
ENV_DEFAULT = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_.]*(?::-|:|-)(.*)\}$")
ENV_CALL = re.compile(
    r"^(os\.getenv\(|os\.environ(\[|\.get\()|process\.env\.|System\.getenv\(|getenv\(|env\()",
    re.IGNORECASE,
)
SECRET_STORE = re.compile(r"^(vault:|\{\{.+\}\}$|secretKeyRef\b|valueFrom\b|ref\+)", re.IGNORECASE)


def describe(value: str) -> str:
    """A value as a fixed label: never any of its characters."""
    text = value.strip().strip("\"'").strip()
    if not text:
        return "<ẩn: trống>"
    if ENV_REF.match(text):
        return "<ẩn: đọc từ biến môi trường>"
    if match := ENV_DEFAULT.match(text):
        fallback = match.group(1).strip().strip("\"'")
        return (
            f"<ẩn: đọc từ biến môi trường, có giá trị mặc định cứng {len(fallback)} ký tự>"
            if fallback
            else "<ẩn: đọc từ biến môi trường>"
        )
    if ENV_CALL.match(text):
        return "<ẩn: đọc từ biến môi trường bằng hàm>"
    if SECRET_STORE.match(text):
        return "<ẩn: lấy từ kho bí mật/template>"
    if text in {"|", "|-", "|+", ">", ">-", ">+"}:
        return "<ẩn: giá trị nhiều dòng>"
    return f"<ẩn: chuỗi cứng {len(text)} ký tự>"


# -- lines -------------------------------------------------------------------------------------

# A key: an identifier (letters first, ≤ 64 chars), optionally quoted, after an optional
# "- " (yaml list) or "export ". A random-looking run before "=" is base64, not a key.
LINE = re.compile(
    r"^(?P<lead>\s*(?:-\s+)?(?:export\s+)?)"
    r"(?P<key>[\"']?[A-Za-z_][A-Za-z0-9_.\-\[\]]{0,63}[\"']?)"
    r"(?P<sep>\s*(?::|=|=>)\s*)(?P<value>.*)$"
)
COMMENT = re.compile(r"^\s*(#|//|;|<!--|--)")
PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*-----")
PEM_END = re.compile(r"-----END [A-Z0-9 ]*-----")
BLOCK_SCALAR = re.compile(r"^[|>][-+]?\s*$")


def _random_key(key: str) -> bool:
    bare = key.strip("\"'")
    return (
        len(bare) > 16
        and bool(re.search(r"[a-z]", bare))
        and bool(re.search(r"[A-Z]", bare))
        and bool(re.search(r"\d", bare))
        and not re.search(r"[_.\-]", bare)
    )


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def mask_config(text: str) -> str:
    """A configuration file as the checker may see it: keys, and for each value only a label."""
    out: list[str] = []
    in_pem = False
    block_indent: int | None = None
    for line in text.splitlines()[:MAX_LINES]:
        if in_pem:
            if PEM_END.search(line):
                in_pem = False
            continue
        if PEM_BEGIN.search(line):
            out.append("<ẩn: khối khoá>")
            in_pem = not PEM_END.search(line)
            continue
        if block_indent is not None:
            if not line.strip() or _indent(line) > block_indent:
                continue  # inside a multi-line value
            block_indent = None
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if COMMENT.match(line):
            out.append("<ẩn: chú thích>")
            continue
        match = LINE.match(line)
        if len(line) > MAX_LINE or match is None or _random_key(match["key"]):
            out.append("<ẩn: dòng>")
            continue
        value = match["value"]
        if BLOCK_SCALAR.match(value.strip()):
            block_indent = _indent(line)
        label = describe(value) if value.strip() else "<ẩn: trống>"
        out.append(f"{match['lead']}{match['key']}{match['sep'].rstrip()} {label}".rstrip())
    return "\n".join(out)


# A secret-looking name and the literal given to it, in code: name = "x", name: 'x', "name": "x",
# name := "x", setPassword("x") is out of reach (not an assignment) but redact() still runs.
GUARDED_NAME = (
    r"[\w.\-\"']*(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|private[_-]?key|credential"
    r"|access[_-]?key|aes[_-]?key|signing[_-]?key|salt)[\w\-\"']*"
)
CODE_ASSIGNMENT = re.compile(
    rf"(?P<name>{GUARDED_NAME})(?P<sep>\s*(?::=|=>|:|=)\s*)(?P<q>[\"'`])(?P<value>[^\"'`\n]+)(?P=q)",
    re.IGNORECASE,
)
BARE_ASSIGNMENT = re.compile(
    rf"(?P<name>{GUARDED_NAME})(?P<sep>\s*[:=]\s*)(?P<value>[^\s\"'`,;)(\]}}{{]{{6,}})(?=\s|$|[,;])",
    re.IGNORECASE,
)
# Kept as code: an attribute or module path (cfg.password), or a plain name without digits
# (password = pwd). Anything else unquoted may be a literal: hidden (over-hiding is harmless).
REFERENCE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$|^[A-Za-z_]+$")


def _bare(match: re.Match[str]) -> str:
    value = match["value"]
    if REFERENCE.match(value):
        return match.group(0)
    return f"{match['name']}{match['sep']}<ẩn: giá trị không nháy {len(value)} ký tự>"


def mask_code(text: str) -> str:
    """A source file as the checker may see it: literals of secret-looking names and key blocks
    hidden, the rest through `redact`."""
    out: list[str] = []
    in_pem = False
    for line in text.splitlines()[:MAX_LINES]:
        if in_pem:
            if PEM_END.search(line):
                in_pem = False
            continue
        if PEM_BEGIN.search(line):
            out.append("<ẩn: khối khoá>")
            in_pem = not PEM_END.search(line)
            continue
        if len(line) > MAX_LINE:
            out.append("<ẩn: dòng quá dài>")
            continue
        line = CODE_ASSIGNMENT.sub(
            lambda m: f"{m['name']}{m['sep']}{m['q']}{describe(m['value'])}{m['q']}", line
        )
        line = BARE_ASSIGNMENT.sub(_bare, line)
        out.append(redact(line))
    return "\n".join(out)


def looks_secret(text: str) -> bool:
    """An argument that pastes a secret: a key block, something `redact` catches, or a literal
    given to a secret-looking name."""
    if PEM_BEGIN.search(text) or redact(text) != text:
        return True
    return bool(CODE_ASSIGNMENT.search(text)) or mask_code(text) != text
