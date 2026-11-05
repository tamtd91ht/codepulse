"""What the Jira integration is set to (docs/specs/27 §5, §9): the DPI task source and the field
mapping, both kept in app_settings and edited in Admin > DPI > Jira.

Shared by the sync (app/jira/sync.py), the DPI (app/dpi) and the GitLab collector (which stops
fetching issues once the source is Jira).
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AppSetting

# A Jira issue's row id in `issues`: this plus its Jira id (clear of GitLab's global issue ids).
JIRA_ID_OFFSET = 10**15
TASK_SOURCE_KEY = "dpi_task_source"
MAPPING_KEY = "jira_mapping"

TaskSource = Literal["gitlab", "jira"]
TASK_SOURCES: tuple[TaskSource, ...] = ("gitlab", "jira")
DPI_TYPES = ("feature", "bug", "refactor", "chore", "docs", "incident", "automation")
SEVERITIES = ("P1", "P2", "P3")


def jira_configured(settings: Settings) -> bool:
    return bool(settings.jira_base_url.strip() and settings.jira_token.get_secret_value())


def task_source(session: Session, settings: Settings) -> TaskSource:
    """The stored choice; none stored: Jira when Jira is configured, else GitLab."""
    row = session.get(AppSetting, TASK_SOURCE_KEY)
    if row is not None and row.value in TASK_SOURCES:
        return "jira" if row.value == "jira" else "gitlab"
    return "jira" if jira_configured(settings) else "gitlab"


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
