"""What a close request's check may show of the repository's current files (outside the spec,
requested by the user 2026-10-31; app/workers/closure_jobs.gather_sources).

An argument like "prod reads the key from the k8s environment" is checked on the files it names,
and those are exactly the files that hold secrets. No character of a value is ever shown:
- a configuration file (`.env*`, yaml, properties, ini, toml, json, xml, `.config`, credential
  dotfiles, shell/Docker files, their .example/.tpl/.bak variants, or a SENSITIVE_PATHS match):
  `mask_config` keeps the keys, replaces every value by a fixed label (a literal and its length,
  or where the value comes from) and hides comments, key blocks, multi-line values and anything
  it cannot parse;
- a source file: `mask_code` replaces EVERY string literal by its length, and on a line naming
  a secret (password, token, key, auth…) hides every value after the name except calls and
  dotted references (os.getenv(…), cfg.db.password); long random tokens are hidden anywhere.
Over-hiding costs a little precision; showing a secret is not an option.
`looks_secret` refuses an argument that pastes a secret.
"""

import re

from app.collector.gitlab.diffstat import is_ignored
from app.review.prepare import SECRET_PATTERNS, redact

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
        "config",
        "cfg",
        "cnf",
        "tfvars",
        "json",
        "xml",
        "env",
        "plist",
        "hcl",
        "sh",
        "bash",
        "zsh",
        "bat",
        "cmd",
        "ps1",
        "dockerfile",
    }
)
# A template, sample or backup of a config file is a config file too.
TEMPLATE_SUFFIXES = frozenset(
    {
        "example",
        "sample",
        "dist",
        "tpl",
        "tmpl",
        "template",
        "j2",
        "jinja",
        "erb",
        "bak",
        "orig",
        "old",
        "local",
    }
)
CREDENTIAL_FILES = frozenset(
    {
        "env",
        "secrets",
        "credentials",
        "dockerfile",
        "makefile",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".pgpass",
        ".htpasswd",
        ".git-credentials",
        ".dockercfg",
        ".s3cfg",
        "id_rsa",
        "id_ed25519",
    }
)


def is_config(path: str, sensitive_paths: list[str]) -> bool:
    """A file shown through `mask_config` (the stricter one): SENSITIVE_PATHS on the whole path
    or the file name alone (".env*" must catch config/.env-prod), `.env…` names, credential
    files, configuration and script extensions, and their template/backup variants."""
    name = path.rsplit("/", 1)[-1].lower()
    if is_ignored(path, sensitive_paths) or is_ignored(name, sensitive_paths):
        return True
    if name.startswith(".env") or name.endswith(".env") or name in CREDENTIAL_FILES:
        return True
    parts = name.split(".")
    while len(parts) > 1 and parts[-1] in TEMPLATE_SUFFIXES:
        parts.pop()
    return (len(parts) > 1 and parts[-1] in CONFIG_EXTENSIONS) or parts[0] == "dockerfile"


# -- values ------------------------------------------------------------------------------------

ENV_REF = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$|^\$[A-Z_][A-Z0-9_]*$")
ENV_DEFAULT = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_.]*(?::-|:|-)(.*)\}$")
ENV_CALL = re.compile(
    r"^(os\.getenv\(|os\.environ(\[|\.get\()|process\.env\.|System\.getenv\(|getenv\(|env\()",
    re.IGNORECASE,
)
SECRET_STORE = re.compile(r"^(vault:|\{\{.+\}\}$|secretKeyRef\b|valueFrom\b|ref\+)", re.IGNORECASE)
BLOCK_SCALAR = re.compile(r"^[|>][-+0-9]*\s*(#.*)?$")


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
        return "<ẩn: đọc từ biến môi trường bằng hàm, có thể kèm giá trị mặc định>"
    if SECRET_STORE.match(text):
        return "<ẩn: lấy từ kho bí mật/template>"
    if BLOCK_SCALAR.match(text):
        return "<ẩn: giá trị nhiều dòng>"
    return f"<ẩn: chuỗi cứng {len(text)} ký tự>"


# -- configuration files --------------------------------------------------------------------------

# A key: an identifier (letters first, ≤ 64 chars), optionally quoted, after an optional
# "- " (yaml list) or "export " / "ENV " (Dockerfile).
LINE = re.compile(
    r"^(?P<lead>\s*(?:-\s+)?(?:(?:export|ENV|ARG|set)\s+)?)"
    r"(?P<key>[\"']?[A-Za-z_][A-Za-z0-9_.\-\[\]]{0,63}[\"']?)"
    r"(?P<sep>\s*(?::|=|=>)\s*|\s+)(?P<value>.*)$"
)
COMMENT = re.compile(r"^\s*(#|//|;|<!--|--|REM\b|::)", re.IGNORECASE)
PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*-----")
PEM_END = re.compile(r"-----END [A-Z0-9 ]*-----")
TOKEN_SHAPE = re.compile(r"\d.*[A-Za-z]|[A-Za-z].*\d")


def _shown_key(key: str) -> bool:
    """A key that can be shown: not a token itself (sk_live_… as a map key, AKIA…, base64)."""
    bare = key.strip("\"'")
    if redact(bare) != bare:
        return False
    if len(bare) >= 24 and TOKEN_SHAPE.search(bare):
        return False
    return not (len(bare) >= 16 and bare.isupper() and any(c.isdigit() for c in bare))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def mask_config(text: str) -> str:
    """A configuration file as the checker may see it: keys, and for each value only a label."""
    out: list[str] = []
    in_pem = False
    block_indent: int | None = None
    open_quote: str | None = None
    for line in text.replace("\r\n", "\n").split("\n")[:MAX_LINES]:
        if in_pem:
            if PEM_END.search(line):
                in_pem = False
            continue
        if open_quote is not None:  # inside a quoted value that spans lines
            if line.count(open_quote) % 2 == 1:
                open_quote = None
            continue
        if PEM_BEGIN.search(line):
            out.append("<ẩn: khối khoá>")
            in_pem = not PEM_END.search(line)
            continue
        if block_indent is not None:
            if not line.strip() or _indent(line) > block_indent:
                continue  # inside a multi-line value
            block_indent = None
        if not line.strip():
            out.append("")
            continue
        if COMMENT.match(line):
            out.append("<ẩn: chú thích>")
            continue
        match = LINE.match(line)
        if len(line) > MAX_LINE or match is None or not _shown_key(match["key"]):
            out.append("<ẩn: dòng>")
            continue
        value = match["value"].strip()
        if BLOCK_SCALAR.match(value):
            block_indent = _indent(line)
        for quote in ('"', "'"):
            if value.startswith(quote) and value.count(quote) % 2 == 1:
                open_quote = quote
        label = describe(value) if value else "<ẩn: trống>"
        separator = match["sep"].strip() or " "
        out.append(f"{match['lead']}{match['key']}{separator} {label}".rstrip())
    return "\n".join(out)


# -- source files -------------------------------------------------------------------------------

# Names that hold secrets. "key"/"auth" alone included: over-hiding is harmless.
GUARDED = re.compile(
    r"(?i)(pass(?:word|wd|phrase)?|pwd|secret|token|\bkey\b|_key\b|key_|apikey|auth|credential"
    r"|dsn|webhook|salt|cookie|session_?id|private|mật\s*khẩu|\bmk\b|khóa|khoá)"
)
# A string literal, with its prefix (b"", r"", f"", @"") and escapes; triple quotes too.
STRING = re.compile(r"(?:\b[rRbBuUfF]{1,2}|@)?(\"\"\"|'''|\"|'|`)((?:\\.|(?!\1)[\s\S])*?)\1")
# What stays after a secret's name: dotted references and calls.
CALL_OR_PATH = re.compile(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+|[A-Za-z_$][\w$]*(?=\s*\()")
VALUE_TOKEN = re.compile(
    r"<chuỗi \d+ ký tự>|[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+|[\w$@%!#^&*+~\\-]+"
)
# A long random-looking token, anywhere: hidden.
RANDOM_TOKEN = re.compile(r"(?<![\w/.-])[A-Za-z0-9+/_=-]{20,}(?![\w/.-])")


def _literal(match: re.Match[str]) -> str:
    return f"<chuỗi {len(match.group(2))} ký tự>"


def _after_name(line: str) -> str:
    """On a line naming a secret: keep the name, punctuation, calls and dotted references; hide
    every other value token (an unquoted word may be the password itself)."""
    found = GUARDED.search(line)
    if found is None:
        return line
    head, tail = line[: found.end()], line[found.end() :]
    kept: list[str] = []
    position = 0
    for token in VALUE_TOKEN.finditer(tail):
        kept.append(tail[position : token.start()])
        text = token.group(0)
        call = CALL_OR_PATH.match(tail, token.start())
        if text.startswith("<chuỗi") or (call is not None and call.end() == token.end()):
            kept.append(text)
        else:
            kept.append("<ẩn>")
        position = token.end()
    kept.append(tail[position:])
    return head + "".join(kept)


def _random(token: re.Match[str]) -> str:
    text = token.group(0)
    return "<ẩn: chuỗi ngẫu nhiên>" if TOKEN_SHAPE.search(text) else text


def mask_code(text: str) -> str:
    """A source file as the checker may see it: every string literal as its length, values after
    secret names hidden, key blocks and random tokens hidden, the rest through `redact`."""
    text = STRING.sub(_literal, redact(text.replace("\r\n", "\n")))
    out: list[str] = []
    in_pem = False
    for line in text.split("\n")[:MAX_LINES]:
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
        quote = next((q for q in ('"', "'", "`") if q in line), None)
        if quote is not None:  # a literal left open (it spans lines): hidden from its quote on
            line = line[: line.index(quote)] + "<ẩn: chuỗi nhiều dòng>"
        line = RANDOM_TOKEN.sub(_random, line)
        out.append(_after_name(line))
    return "\n".join(out)


# -- arguments ----------------------------------------------------------------------------------

# A secret's name, then a separator (":", "=", "là", "is"; a few words may come between:
# "mật khẩu prod là …") or just a space, then a value.
MASKED_WORD = (
    r"(?:pass(?:word|wd)?|pwd|\bpw\b|secret|token|api[_-]?key|private[_-]?key|access[_-]?key"
    r"|aes[_-]?key|mật\s*khẩu|\bmk\b|khóa|khoá|\bkey\b)"
)
PASTED = re.compile(
    rf"(?i){MASKED_WORD}(?:[^\n:=.]{{0,30}}?\s*(?:[:=]|\blà\b|\bis\b)|\s)\s*[\"'`]?"
    r"(?P<value>[^\s\"'`,;)]{5,})"
)
HIGH_ENTROPY = re.compile(r"(?<![\w/.-])[A-Za-z0-9+/_=@!#$%^&*-]{20,}(?![\w/.-])")
FILE_NAME = re.compile(r"^[\w.-]*\.[A-Za-z0-9]{1,10}$")


def _is_path(token: str) -> bool:
    """A file name or a repository path, not a token: config/.env-prod, deploy/k8s/app.yaml;
    "wJalrXUtnFEMI/K7MDENG/bPxRfi…" (mixed case, no extension) is a key."""
    if FILE_NAME.match(token):
        return True
    if "/" not in token:
        return False
    return bool(FILE_NAME.match(token.rsplit("/", 1)[-1])) or token == token.lower()


def _token_secret(text: str) -> bool:
    """What redact's token patterns catch (keys, URLs with credentials…), not its quoted
    assignments: quoting "${DB_PASSWORD}" is a fine argument."""
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def looks_secret(text: str) -> bool:
    """An argument that pastes a secret: a key block, something `redact` catches, a value with
    digits or symbols given to a secret's name that is not a reference (an env variable, a file
    name), or a long random token."""
    text = text.replace("\r\n", "\n")
    if PEM_BEGIN.search(text) or _token_secret(text):
        return True
    for match in PASTED.finditer(text):
        value = match["value"].rstrip(".")
        if value.startswith("$") or _is_path(value):
            continue  # ${DB_PASSWORD}, $AES_KEY, application-prod.yml, config/.env-prod
        if any(c.isdigit() for c in value) or not value.replace("_", "").isalnum():
            return True
    return any(
        TOKEN_SHAPE.search(m.group(0)) and not _is_path(m.group(0))
        for m in HIGH_ENTROPY.finditer(text)
    )
