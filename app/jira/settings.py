"""What the Jira integration is set to (docs/specs/27 §2, §5, §9): the connection, the DPI task
source and the field mapping, kept in app_settings (the token in stored_secrets, encrypted) and
edited in Admin > Jira or DPI > Jira. A connection value saved on the web wins over its JIRA_*
environment variable; the variables are an optional fallback.

Shared by the sync (app/jira/sync.py), the DPI (app/dpi) and the GitLab collector (which stops
fetching issues once the source is Jira).
"""

import ipaddress
import logging
import re
import ssl
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import SecretBox, secret_box
from app.models import AppSetting, StoredSecret

log = logging.getLogger(__name__)

# A Jira issue's row id in `issues`: this plus its Jira id (clear of GitLab's global issue ids).
JIRA_ID_OFFSET = 10**15
TASK_SOURCE_KEY = "dpi_task_source"
MAPPING_KEY = "jira_mapping"
CONNECTION_KEY = "jira_connection"
TOKEN_SECRET = "jira_token"  # noqa: S105 - a stored_secrets row name, not a secret
MAX_BASE_URL_CHARS = 300
MAX_CA_PEM_BYTES = 100_000
MAX_TOKEN_CHARS = 1000
# In CONNECTION_KEY: the server (origin) the saved token belongs to.
TOKEN_ORIGIN = "token_origin"  # noqa: S105 - a key name, not a secret
TOKEN_RE = re.compile(r"^[\x21-\x7e]+$")  # printable ASCII, no space
# Bounds of the numbers (the same as the JIRA_* variables in app/config.py).
INTERVAL_MIN = (1, 1440)
BACKFILL_DAYS = (1, 3650)
TIMEOUT_S = (1, 300)
NUMBERS = {"interval_min": INTERVAL_MIN, "backfill_days": BACKFILL_DAYS, "timeout_s": TIMEOUT_S}

TaskSource = Literal["gitlab", "jira"]
TASK_SOURCES: tuple[TaskSource, ...] = ("gitlab", "jira")
DPI_TYPES = ("feature", "bug", "refactor", "chore", "docs", "incident", "automation")
SEVERITIES = ("P1", "P2", "P3")

Source = Literal["web", "env"]


@dataclass(frozen=True)
class JiraConnection:
    """The connection in effect (§2): each value saved on the web, else its JIRA_* variable."""

    base_url: str  # without a trailing slash; "" when none
    token: str = field(repr=False)
    ca_pem: str = field(repr=False)  # an internal CA saved on the web (PEM), else ""
    ca_path: str = ""  # JIRA_CA_BUNDLE, used when no PEM is saved
    interval_min: int = 15
    backfill_days: int = 180
    timeout_s: float = 20.0
    enabled: bool = True
    base_url_source: Source | None = None
    token_source: Source | None = None
    token_updated_at: datetime | None = None
    ca_source: Source | None = None
    # Where interval_min / backfill_days / timeout_s / enabled come from.
    sources: dict[str, Source] = field(default_factory=dict)
    env_base_url: bool = False
    env_token: bool = False
    env_ca_bundle: bool = False
    # A token was saved on the web but cannot be decrypted (APP_ENCRYPTION_KEY changed).
    token_unreadable: bool = False
    # A token exists (web or env) but was set for another server than base_url: not used.
    token_other_server: bool = False

    @property
    def reachable(self) -> bool:
        """Enough to call Jira (a test call does not need the integration switched on)."""
        return bool(self.base_url and self.token)

    @property
    def configured(self) -> bool:
        return self.reachable and self.enabled

    def verify(self) -> ssl.SSLContext | str | bool:
        """What httpx checks Jira's certificate with: the CA saved on the web, JIRA_CA_BUNDLE,
        else the system's CAs."""
        if self.ca_pem:
            return ssl.create_default_context(cadata=self.ca_pem)
        return self.ca_path or True

    def as_json(self) -> dict[str, Any]:
        """For the Admin screen: never the token nor the PEM."""
        return {
            "base_url": self.base_url or None,
            "base_url_source": self.base_url_source,
            "token_set": bool(self.token),
            "token_source": self.token_source,
            "token_updated_at": self.token_updated_at,
            "token_unreadable": self.token_unreadable,
            "token_other_server": self.token_other_server,
            "ca_set": bool(self.ca_pem or self.ca_path),
            "ca_source": self.ca_source,
            "interval_min": self.interval_min,
            "backfill_days": self.backfill_days,
            "timeout_s": self.timeout_s,
            "enabled": self.enabled,
            "configured": self.configured,
            "sources": dict(self.sources),
            "env": {
                "base_url": self.env_base_url,
                "token": self.env_token,
                "ca_bundle": self.env_ca_bundle,
            },
        }


def normalize_base_url(raw: str) -> str:
    """`jira.congty.vn` or `https://jira.congty.vn/jira/` -> an http(s) base without a trailing
    slash; ValueError (Vietnamese) otherwise. No login, query or fragment."""
    text = raw.strip()
    if not text or any(c.isspace() for c in text) or len(text) > MAX_BASE_URL_CHARS:
        raise ValueError(
            "base_url: cần một địa chỉ như https://jira.congty.vn"
            f" (tối đa {MAX_BASE_URL_CHARS} ký tự)"
        )
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    try:
        parts.port  # noqa: B018 - raises on a port that is not a number
    except ValueError:
        raise ValueError("base_url: cổng không hợp lệ") from None
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or "@" in parts.netloc
        or "?" in text
        or "#" in text
    ):
        raise ValueError("base_url: chỉ nhận địa chỉ http(s), không kèm tài khoản, ? hay #")
    if _local_host(parts.hostname):
        raise ValueError("base_url: không nhận localhost, loopback hay link-local")
    return text.rstrip("/")


def _local_host(host: str) -> bool:
    """localhost, 127.0.0.0/8, ::1, 169.254.0.0/16, fe80::/10 (a token must not go there)."""
    name = host.lower().rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local


def origin(base_url: str) -> str:
    """scheme://host:port of a base URL ("" for none): where a token is sent."""
    if not base_url:
        return ""
    parts = urlsplit(base_url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.scheme}://{(parts.hostname or '').lower()}:{port}"


def check_ca_pem(raw: str) -> str:
    """One or more CA certificates (PEM); ValueError (Vietnamese) otherwise."""
    text = raw.strip()
    if len(text.encode()) > MAX_CA_PEM_BYTES:
        raise ValueError(f"ca_pem: tối đa {MAX_CA_PEM_BYTES // 1000} KB")
    if "-----BEGIN CERTIFICATE-----" not in text:
        raise ValueError("ca_pem: cần chứng chỉ dạng PEM (-----BEGIN CERTIFICATE-----)")
    try:
        ssl.create_default_context(cadata=text)
    except (ssl.SSLError, ValueError):
        raise ValueError("ca_pem: không đọc được chứng chỉ PEM") from None
    return text + "\n"


def check_token(raw: str) -> str:
    token = raw.strip()
    if len(token) > MAX_TOKEN_CHARS or not TOKEN_RE.match(token):
        raise ValueError(
            f"token: cần một Personal Access Token (tối đa {MAX_TOKEN_CHARS} ký tự ASCII, "
            "không khoảng trắng)"
        )
    return token


def _bounded(name: str, value: Any, bounds: tuple[int, int]) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise ValueError(f"{name}: cần một số nguyên")
    low, high = bounds
    if not low <= int(value) <= high:
        raise ValueError(f"{name}: từ {low} đến {high}")
    return int(value)


def parse_connection(raw: dict[str, Any]) -> dict[str, Any]:
    """The non-secret connection values to store, only the keys given; ValueError (Vietnamese)
    when one is invalid. An empty base_url / ca_pem or a None: the saved value goes (the
    environment applies again)."""
    out: dict[str, Any] = {}
    if "base_url" in raw:
        value = raw["base_url"]
        out["base_url"] = normalize_base_url(str(value)) if value and str(value).strip() else None
    if "ca_pem" in raw:
        value = raw["ca_pem"]
        out["ca_pem"] = check_ca_pem(str(value)) if value and str(value).strip() else None
    for name, bounds in NUMBERS.items():
        if name in raw:
            out[name] = None if raw[name] is None else _bounded(name, raw[name], bounds)
    if "enabled" in raw:
        if raw["enabled"] is not None and not isinstance(raw["enabled"], bool):
            raise ValueError("enabled: cần true hoặc false")
        out["enabled"] = raw["enabled"]
    return out


def stored_connection(session: Session) -> dict[str, Any]:
    """What is saved on the web (a value that no longer passes the checks is ignored)."""
    row = session.get(AppSetting, CONNECTION_KEY)
    if row is None or not isinstance(row.value, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in row.value.items():
        try:
            out.update(parse_connection({key: value}))
        except ValueError:
            log.warning("stored jira connection value ignored", extra={"key": key})
    return {k: v for k, v in out.items() if v is not None}


def save_connection(session: Session, values: dict[str, Any], user_id: int | None) -> None:
    """Merge `values` (from parse_connection; None removes a key) into the saved connection."""
    row = session.get(AppSetting, CONNECTION_KEY)
    current = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    for key, value in values.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    if row is None:
        session.add(AppSetting(key=CONNECTION_KEY, value=current, set_by=user_id))
    else:
        row.value, row.set_by = current, user_id
    session.flush()


def effective_base_url(session: Session, settings: Settings) -> str:
    """The base URL in effect: saved on the web, else JIRA_BASE_URL ("" for none)."""
    return str(stored_connection(session).get("base_url") or _env_base(settings))


def _env_base(settings: Settings) -> str:
    return settings.jira_base_url.strip().rstrip("/")


def save_token(
    session: Session, box: SecretBox, token: str, user_id: int | None, base_url: str
) -> None:
    """The PAT pasted on the Admin screen, encrypted at rest like the Claude token, and bound to
    the server (`origin(base_url)`) it was given for: it is never sent anywhere else."""
    server = origin(base_url)
    if not server:
        raise ValueError("token: nhập URL Jira trước (token gắn với máy chủ của URL đó)")
    row = session.get(StoredSecret, TOKEN_SECRET)
    if row is None:
        row = StoredSecret(name=TOKEN_SECRET)
        session.add(row)
    row.value_encrypted = box.encrypt(check_token(token))
    row.source = "paste"
    row.set_by = user_id
    save_connection(session, {TOKEN_ORIGIN: server}, user_id)


def clear_token(session: Session) -> bool:
    row = session.get(StoredSecret, TOKEN_SECRET)
    conn_row = session.get(AppSetting, CONNECTION_KEY)
    if conn_row is not None and isinstance(conn_row.value, dict) and TOKEN_ORIGIN in conn_row.value:
        conn_row.value = {k: v for k, v in conn_row.value.items() if k != TOKEN_ORIGIN}
    if row is None:
        session.flush()
        return False
    session.delete(row)
    session.flush()
    return True


def clear_connection(session: Session) -> bool:
    """ "Dùng lại biến môi trường": every value saved on the web goes; True if one was."""
    had_token = clear_token(session)
    row = session.get(AppSetting, CONNECTION_KEY)
    if row is not None:
        session.delete(row)
        session.flush()
    return had_token or row is not None


_warned_unreadable = False


def _stored_token(
    session: Session, box: SecretBox | None
) -> tuple[str, str, datetime | None, bool]:
    """(token, the server it belongs to, updated_at, unreadable)."""
    global _warned_unreadable
    row = session.get(StoredSecret, TOKEN_SECRET)
    if row is None:
        return "", "", None, False
    conn_row = session.get(AppSetting, CONNECTION_KEY)
    raw = conn_row.value if conn_row is not None and isinstance(conn_row.value, dict) else {}
    server = str(raw.get(TOKEN_ORIGIN) or "")
    try:
        return (box or secret_box()).decrypt(row.value_encrypted), server, row.updated_at, False
    except ValueError:
        # Wrong APP_ENCRYPTION_KEY or corrupt data: no token from the web (the Admin screen says
        # so). Logged once per process, not on every scheduler tick.
        if not _warned_unreadable:
            _warned_unreadable = True
            log.warning("stored jira token cannot be decrypted")
        return "", server, row.updated_at, True


def connection(
    session: Session,
    settings: Settings,
    box: SecretBox | None = None,
    *,
    base_url: str | None = None,
) -> JiraConnection:
    """The connection in effect: a value saved on the web wins, else its JIRA_* variable. `box`
    decrypts the saved token (default: APP_ENCRYPTION_KEY's). `base_url`: another URL than the
    one in effect (a test of a URL not saved yet).

    A token only goes to its own server (§2): the web token to the origin it was saved for,
    JIRA_TOKEN to the origin of JIRA_BASE_URL (none: never). Otherwise there is no token."""
    stored = stored_connection(session)
    env_base = _env_base(settings)
    env_token = settings.jira_token.get_secret_value().strip()
    env_ca = settings.jira_ca_bundle.strip()
    web_token, web_server, web_at, unreadable = _stored_token(session, box)
    url_source: Source | None = None
    if base_url is None:
        base_url = str(stored.get("base_url") or env_base)
        url_source = ("web" if stored.get("base_url") else "env") if base_url else None
    else:
        url_source = "web" if base_url else None
    server = origin(base_url)
    web_ok = bool(web_token and server and web_server == server)
    env_ok = bool(env_token and env_base and server == origin(env_base))
    token = web_token if web_ok else env_token if env_ok else ""
    other_server = not token and bool((web_token and not web_ok) or (env_token and not env_ok))
    ca_pem = str(stored.get("ca_pem") or "")
    sources: dict[str, Source] = {
        k: ("web" if k in stored else "env") for k in (*NUMBERS, "enabled")
    }
    return JiraConnection(
        base_url=base_url,
        token=token,
        ca_pem=ca_pem,
        ca_path=env_ca,
        interval_min=int(stored.get("interval_min", settings.jira_sync_interval_min)),
        backfill_days=int(stored.get("backfill_days", settings.jira_backfill_days)),
        timeout_s=float(stored.get("timeout_s", settings.jira_timeout_s)),
        enabled=bool(stored.get("enabled", True)),
        base_url_source=url_source,
        token_source="web" if web_ok else "env" if env_ok else None,
        token_updated_at=web_at if web_ok else None,
        ca_source="web" if ca_pem else ("env" if env_ca else None),
        sources=sources,
        env_base_url=bool(env_base),
        env_token=bool(env_token),
        env_ca_bundle=bool(env_ca),
        token_unreadable=unreadable,
        token_other_server=other_server,
    )


def jira_configured(session: Session, settings: Settings, box: SecretBox | None = None) -> bool:
    """A base URL and a token in effect, and the integration not switched off."""
    return connection(session, settings, box).configured


def task_source(session: Session, settings: Settings) -> TaskSource:
    """The stored choice; none stored: Jira when Jira is configured, else GitLab."""
    row = session.get(AppSetting, TASK_SOURCE_KEY)
    if row is not None and row.value in TASK_SOURCES:
        return "jira" if row.value == "jira" else "gitlab"
    return "jira" if jira_configured(session, settings) else "gitlab"


def set_task_source(session: Session, source: TaskSource, user_id: int | None) -> None:
    if source not in TASK_SOURCES:
        raise ValueError(f"unknown task source {source!r}")
    row = session.get(AppSetting, TASK_SOURCE_KEY)
    if row is None:
        session.add(AppSetting(key=TASK_SOURCE_KEY, value=source, set_by=user_id))
    else:
        row.value, row.set_by = source, user_id


def _default_types() -> dict[str, str]:
    return {
        "Story": "feature",
        "New Feature": "feature",
        "Bug": "bug",
        "Improvement": "refactor",
        "Task": "chore",
        "Documentation": "docs",
        "Incident": "incident",
    }


def _default_priorities() -> dict[str, str]:
    return {"Blocker": "P1", "Highest": "P1", "Critical": "P2", "High": "P2"}


@dataclass
class JiraMapping:
    """§5-6. Names are matched case-insensitively."""

    story_points_field: str = "Story Points"
    sprint_field: str = "Sprint"
    flagged_field: str = "Flagged"
    # issuetype.name -> DPI type; sub-tasks take their parent's; anything else: default_type.
    types: dict[str, str] = field(default_factory=_default_types)
    default_type: str = "chore"
    # A Bug with one of these labels, or an issue of one of these types, is a production bug.
    prod_labels: list[str] = field(default_factory=lambda: ["production", "prod-bug"])
    prod_issue_types: list[str] = field(default_factory=lambda: ["Incident"])
    # priority.name -> P1/P2; anything else P3.
    priorities: dict[str, str] = field(default_factory=_default_priorities)
    # A status whose name contains one of these (case-insensitive) is blocked.
    blocked_statuses: list[str] = field(default_factory=lambda: ["Blocked"])

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


def _str_list(value: Any, limit: int = 50) -> list[str]:
    if not isinstance(value, list):
        raise ValueError("cần một danh sách")
    out = [str(v).strip() for v in value if str(v).strip()]
    if len(out) > limit or any(len(v) > 100 for v in out):
        raise ValueError(f"tối đa {limit} mục, mỗi mục 100 ký tự")
    return out


def _str_map(value: Any, allowed: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("cần một bảng tên -> giá trị")
    out: dict[str, str] = {}
    for k, v in value.items():
        name = str(k).strip()
        if not name or len(name) > 100:
            raise ValueError("tên cần 1-100 ký tự")
        if v not in allowed:
            raise ValueError(f"{name}: chỉ nhận {', '.join(allowed)}")
        out[name] = str(v)
    if len(out) > 100:
        raise ValueError("tối đa 100 dòng")
    return out


def parse_mapping(raw: dict[str, Any]) -> JiraMapping:
    """A mapping from the Admin screen or app_settings; ValueError (Vietnamese) when invalid.
    Missing keys keep their defaults."""
    m = JiraMapping()
    for name in ("story_points_field", "sprint_field", "flagged_field"):
        if name in raw:
            value = str(raw[name] or "").strip()
            if not value or len(value) > 100:
                raise ValueError(f"{name}: cần 1-100 ký tự")
            setattr(m, name, value)
    if "types" in raw:
        m.types = _str_map(raw["types"], DPI_TYPES)
    if "default_type" in raw:
        if raw["default_type"] not in DPI_TYPES:
            raise ValueError(f"default_type: chỉ nhận {', '.join(DPI_TYPES)}")
        m.default_type = str(raw["default_type"])
    if "prod_labels" in raw:
        m.prod_labels = _str_list(raw["prod_labels"])
    if "prod_issue_types" in raw:
        m.prod_issue_types = _str_list(raw["prod_issue_types"])
    if "priorities" in raw:
        m.priorities = _str_map(raw["priorities"], SEVERITIES)
    if "blocked_statuses" in raw:
        m.blocked_statuses = _str_list(raw["blocked_statuses"])
    return m


def mapping(session: Session) -> JiraMapping:
    row = session.get(AppSetting, MAPPING_KEY)
    if row is None or not isinstance(row.value, dict):
        return JiraMapping()
    try:
        return parse_mapping(row.value)
    except ValueError:
        return JiraMapping()


def set_mapping(session: Session, value: JiraMapping, user_id: int | None) -> None:
    row = session.get(AppSetting, MAPPING_KEY)
    if row is None:
        session.add(AppSetting(key=MAPPING_KEY, value=value.as_json(), set_by=user_id))
    else:
        row.value, row.set_by = value.as_json(), user_id
