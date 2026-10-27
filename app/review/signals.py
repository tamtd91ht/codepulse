"""What kind of change a diff is (docs/specs/19 §4.1): deterministic signals from the changed
paths and the added lines, used to pick the invariants and checks of the review knowledge whose
`signals` match. The names are the fixed set of app.review.knowledge.SIGNALS."""

import re

from app.review.prepare import DiffFile

# Signals read off a changed path.
_PATH_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "migration",
        re.compile(r"(^|/)(migrations?|migrate|db/migrate|flyway|liquibase|alembic)(/|$)", re.I),
    ),
    ("migration", re.compile(r"(^|/)V\d+__[^/]*\.sql$|(^|/)[^/]*\.(up|down)\.sql$", re.I)),
    ("auth", re.compile(r"(^|/)[^/]*(auth|permission|rbac|acl|login|password|jwt)", re.I)),
    (
        "money",
        re.compile(r"(^|/)[^/]*(payment|billing|invoice|wallet|refund|pricing|balance)", re.I),
    ),
    ("file_upload", re.compile(r"(^|/)[^/]*upload", re.I)),
    (
        "config",
        re.compile(
            r"(^|/)(\.env[^/]*|config[^/]*|settings[^/]*|application[^/]*\.(ya?ml|properties))$",
            re.I,
        ),
    ),
    ("config", re.compile(r"(^|/)(config|configs|settings)/", re.I)),
    ("job", re.compile(r"(^|/)[^/]*(cron|jobs?|workers?|scheduler|tasks?)([^a-z]|$)", re.I)),
    (
        "event",
        re.compile(
            r"(^|/)[^/]*(events?|kafka|consumer|producer|listener|subscriber)([^a-z]|$)", re.I
        ),
    ),
]

# Signals read off an added line. Word edges are letters only, so `has_permission` and
# `tenant_amount` count (an underscore is a word character for \b).
_LINE_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "endpoint",
        re.compile(
            r"@(router|app|api|bp|blueprint)\.(route|get|post|put|patch|delete|api_route)\("
            r"|@(Get|Post|Put|Patch|Delete|Request)Mapping\b"
            r"|@(Get|Post|Put|Patch|Delete)\("
            r"|http\.HandleFunc\("
            r"|\b(router|app|r|e|g|mux)\.(GET|POST|PUT|PATCH|DELETE|Get|Post|Put|Patch|Delete"
            r"|get|post|put|patch|delete|Handle|HandleFunc)\(\s*[\"'`]/"
        ),
    ),
    (
        "db_query",
        re.compile(
            r"\bSELECT\b.*\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b"
            r"|session\.(query|execute|scalars?)\(|\bselect\("
            r"|\.(find|findOne|findMany|findAll|findFirst|findById|aggregate)\("
            r"|\bprisma\.|createQuery\(|\bknex\(|\bcollection\.(find|insert|update|delete)",
            re.I,
        ),
    ),
    (
        "outbound_call",
        re.compile(
            r"\b(requests|httpx|aiohttp|urllib)\.|\bfetch\(|\baxios\b"
            r"|\bhttp\.(Get|Post|Do|NewRequest)\(|\bHttpClient\b|\bRestTemplate\b|\bWebClient\b"
        ),
    ),
    (
        "auth",
        re.compile(
            r"(?<![a-z])(auth|authenticate|authentication|authoriz(e|ation)|permissions?|roles?"
            r"|tokens?|jwt|password|login|logout|oauth|api[_-]?key|is_admin|current_user"
            r"|principal)(?![a-z])",
            re.I,
        ),
    ),
    (
        "money",
        re.compile(
            r"(?<![a-z])(amount|prices?|balance|invoices?|payments?|refunds?|currency|wallet"
            r"|billing|discount|fees?|vat|tax)(?![a-z])",
            re.I,
        ),
    ),
    (
        "file_upload",
        re.compile(
            r"(?<![a-z])(upload|multipart|UploadFile|FormData|MultipartFile)(?![a-z])", re.I
        ),
    ),
    (
        "config",
        re.compile(
            r"\b(os\.environ|getenv|process\.env|dotenv|ConfigMap|System\.getenv|os\.Getenv)\b"
        ),
    ),
    (
        "job",
        re.compile(
            r"\b(cron|crontab|@Scheduled|celery|apscheduler|\.delay\(|enqueue\(|BackgroundTasks"
            r"|setInterval|schedule\.)",
            re.I,
        ),
    ),
    (
        "event",
        re.compile(
            r"\b(publish|emit|kafka|rabbitmq|amqp|sns|sqs|pubsub|EventEmitter|KafkaTemplate"
            r"|producer\.send)\b",
            re.I,
        ),
    ),
    (
        "migration",
        re.compile(
            r"\b(CREATE|ALTER|DROP)\s+(TABLE|INDEX|COLUMN)\b"
            r"|\bop\.(add_column|create_table|drop_column|alter_column|create_index)\("
            r"|\bSchema::(create|table)\(",
            re.I,
        ),
    ),
]


def detect_signals(files: list[DiffFile]) -> set[str]:
    """The set of signals a change carries, from its paths and added lines only (removed and
    context lines are not what is being introduced). Deterministic; no model involved."""
    out: set[str] = set()
    for f in files:
        for signal, pattern in _PATH_RULES:
            if pattern.search(f.path):
                out.add(signal)
        for hunk in f.hunks:
            for op, _, text in hunk.lines:
                if op != "+":
                    continue
                for signal, pattern in _LINE_RULES:
                    if signal not in out and pattern.search(text):
                        out.add(signal)
    return out
