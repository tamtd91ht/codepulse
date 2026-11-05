"""One Jira issue (REST v2 `/search` result) -> one `issues` row (docs/specs/27 §5-6). Pure.

Custom fields (Story Points, Sprint, Flagged) are found by name in `/field` (`resolve_fields`);
the issue's JSON then carries them under their ids (`customfield_10002`).
"""

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.jira.settings import JIRA_ID_OFFSET, JiraMapping

# Story Points -> (complexity, points): the smallest level the value fits under (§6).
SIZES: tuple[tuple[float, str, int], ...] = ((1, "XS", 1), (2, "S", 2), (3, "M", 3), (5, "L", 5))
TOP_SIZE = ("XL", 8)
MAX_POINTS = Decimal(10**6)  # issues.story_points is Numeric(8, 2)
BASE_FIELDS = (
    "summary",
    "status",
    "issuetype",
    "parent",
    "labels",
    "priority",
    "assignee",
    "duedate",
    "fixVersions",
    "resolutiondate",
    "statuscategorychangedate",
    "created",
    "updated",
    "timeoriginalestimate",
    "timespent",
)
_SPRINT_TEXT = re.compile(r"\[(.*)\]\s*$", re.S)
_SPRINT_PAIR = re.compile(r"(\w+)=(.*?)(?=,\w+=|$)", re.S)


@dataclass(frozen=True)
class FieldRef:
    id: str
    name: str

    def as_json(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True)
class FieldIds:
    """The custom fields of the mapping, as found on this Jira (None: not found)."""

    story_points: FieldRef | None = None
    sprint: FieldRef | None = None
    flagged: FieldRef | None = None

    def as_json(self) -> dict[str, dict[str, str] | None]:
        return {
            "story_points": self.story_points.as_json() if self.story_points else None,
            "sprint": self.sprint.as_json() if self.sprint else None,
            "flagged": self.flagged.as_json() if self.flagged else None,
        }


@dataclass(frozen=True)
class JiraUser:
    key: str  # stable across username changes (§7)
    name: str | None
    email: str | None
    display_name: str | None


@dataclass(frozen=True)
class MappedIssue:
    row: dict[str, Any]  # the `issues` columns, assignee_account_ids aside
    assignee: JiraUser | None


@dataclass(frozen=True)
class Sprint:
    name: str | None
    end: date | None


def find_field(fields: list[dict[str, Any]], name: str) -> FieldRef | None:
    """The field named `name` (case-insensitive) in `/field`; a custom field wins a tie."""
    wanted = name.strip().lower()
    matches = [
        f for f in fields if str(f.get("name") or "").strip().lower() == wanted and f.get("id")
    ]
    matches.sort(key=lambda f: not f.get("custom", False))
    if not matches:
        return None
    return FieldRef(id=str(matches[0]["id"]), name=str(matches[0].get("name")))


def resolve_fields(fields: list[dict[str, Any]], mapping: JiraMapping) -> FieldIds:
    return FieldIds(
        story_points=find_field(fields, mapping.story_points_field),
        sprint=find_field(fields, mapping.sprint_field),
        flagged=find_field(fields, mapping.flagged_field),
    )


def search_fields(ids: FieldIds) -> list[str]:
    """The fields a `/search` asks for."""
    extra = [ref.id for ref in (ids.story_points, ids.sprint, ids.flagged) if ref is not None]
    return [*BASE_FIELDS, *extra]


# -- small readers ---------------------------------------------------------------------------


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value.strip() == "<null>":
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _date(value: Any) -> date | None:
    """The calendar date as written (an endDate's local day, not UTC's)."""
    if not isinstance(value, str) or len(value.strip()) < 10:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _seconds(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def story_points(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        points = Decimal(str(value))
    except InvalidOperation:
        return None
    # issues.story_points is Numeric(8, 2): a value it cannot hold is taken as no estimate.
    if not points.is_finite() or abs(points.quantize(Decimal("0.01"))) >= MAX_POINTS:
        return None
    return points


def complexity(points: Decimal | None) -> tuple[str | None, int | None]:
    """§6: rounded up to the next level; empty or 0 has none; above 5 is XL (8, the cap)."""
    if points is None or points <= 0:
        return None, None
    value = float(points)
    for top, size, score in SIZES:
        if value <= top or math.isclose(value, top):
            return size, score
    return TOP_SIZE


def _sprint_text(text: str) -> dict[str, str]:
    """`com.atlassian.greenhopper.service.sprint.Sprint@1a2b[id=3,...,name=Sprint 3,...]`."""
    match = _SPRINT_TEXT.search(text)
    if not match:
        return {}
    return {k: v for k, v in _SPRINT_PAIR.findall(match.group(1))}


def sprints(value: Any) -> list[Sprint]:
    """The sprints of the field, as strings (older Jira DC) or objects."""
    items = value if isinstance(value, list) else [value] if value else []
    out: list[Sprint] = []
    for item in items:
        raw = _sprint_text(item) if isinstance(item, str) else _dict(item)
        if not raw:
            continue
        name = raw.get("name")
        name = str(name) if name not in (None, "", "<null>") else None
        out.append(Sprint(name=name, end=_date(raw.get("endDate"))))
    return out


def latest_sprint(value: Any) -> Sprint | None:
    """The sprint ending last; none with an end date: the last listed."""
    found = sprints(value)
    if not found:
        return None
    dated = [s for s in found if s.end is not None]
    if dated:
        return max(dated, key=lambda s: s.end or date.min)
    return found[-1]


def _flagged(value: Any) -> bool:
    if isinstance(value, list):
        return any(v for v in value)
    return bool(value)


def _user(value: Any) -> JiraUser | None:
    raw = _dict(value)
    key = raw.get("key") or raw.get("name")
    if not key:
        return None
    return JiraUser(
        key=str(key),
        name=str(raw["name"]) if raw.get("name") else None,
        email=str(raw["emailAddress"]) if raw.get("emailAddress") else None,
        display_name=str(raw["displayName"]) if raw.get("displayName") else None,
    )


def _lookup(table: dict[str, str], name: str) -> str | None:
    low = name.strip().lower()
    return next((v for k, v in table.items() if k.strip().lower() == low), None)


def _in(names: list[str], name: str) -> bool:
    low = name.strip().lower()
    return any(n.strip().lower() == low for n in names)


# -- the row ---------------------------------------------------------------------------------


def issue_type(fields: dict[str, Any], mapping: JiraMapping) -> str:
    """§5: issuetype through the table; a sub-task takes its parent's type."""
    kind = _dict(fields.get("issuetype"))
    if kind.get("subtask"):
        parent_kind = _dict(_dict(_dict(fields.get("parent")).get("fields")).get("issuetype"))
        if parent_kind.get("name"):
            kind = parent_kind
    return _lookup(mapping.types, str(kind.get("name") or "")) or mapping.default_type


def prod_severity(fields: dict[str, Any], dpi_type: str, mapping: JiraMapping) -> str | None:
    """A Bug labelled with a prod label, or an issue of a prod type: P1/P2 by priority, else P3."""
    own_type = str(_dict(fields.get("issuetype")).get("name") or "")
    labels = [str(label) for label in fields.get("labels") or []]
    is_prod = _in(mapping.prod_issue_types, own_type) or (
        dpi_type == "bug" and any(_in(mapping.prod_labels, label) for label in labels)
    )
    if not is_prod:
        return None
    priority = str(_dict(fields.get("priority")).get("name") or "")
    return _lookup(mapping.priorities, priority) or "P3"


def map_issue(
    issue: dict[str, Any],
    mapping: JiraMapping,
    ids: FieldIds,
    base_url: str,
    jira_project_id: int,
) -> MappedIssue:
    fields = _dict(issue.get("fields"))
    key = str(issue["key"])
    status = _dict(fields.get("status"))
    done = _dict(status.get("statusCategory")).get("key") == "done"
    closed_at = None
    if done:
        closed_at = (
            _datetime(fields.get("resolutiondate"))
            or _datetime(fields.get("statuscategorychangedate"))
            or _datetime(fields.get("updated"))
        )
    dpi_type = issue_type(fields, mapping)
    points = story_points(fields.get(ids.story_points.id)) if ids.story_points else None
    size, size_points = complexity(points)
    sprint = latest_sprint(fields.get(ids.sprint.id)) if ids.sprint else None
    versions = [_dict(v) for v in fields.get("fixVersions") or []]
    releases = [d for d in (_date(v.get("releaseDate")) for v in versions) if d is not None]
    due = _date(fields.get("duedate")) or (sprint.end if sprint else None)
    if due is None and releases:
        due = min(releases)
    milestone = sprint.name if sprint and sprint.name else None
    if milestone is None and versions and versions[0].get("name"):
        milestone = str(versions[0]["name"])
    labels = [str(label) for label in fields.get("labels") or []]
    if ids.flagged and _flagged(fields.get(ids.flagged.id)):
        labels.append("blocked::impediment")
    status_name = str(status.get("name") or "").strip()
    if status_name and any(
        b.strip() and b.strip().lower() in status_name.lower() for b in mapping.blocked_statuses
    ):
        labels.append(f"blocked::{status_name}")
    parent_key = _dict(fields.get("parent")).get("key")
    row: dict[str, Any] = {
        "id": JIRA_ID_OFFSET + int(issue["id"]),
        "source": "jira",
        "project_id": None,
        "jira_project_id": jira_project_id,
        "iid": int(key.rsplit("-", 1)[1]),
        "key": key,
        "parent_key": str(parent_key) if parent_key else None,
        "story_points": points,
        "title": fields.get("summary"),
        "state": "closed" if done else "opened",
        "labels": labels,
        "type": dpi_type,
        "complexity": size,
        "complexity_points": size_points,
        "prod_severity": prod_severity(fields, dpi_type, mapping),
        "milestone": milestone,
        "due_date": due,
        "web_url": f"{base_url.rstrip('/')}/browse/{key}",
        "created_at_src": _datetime(fields.get("created")),
        "closed_at_src": closed_at,
        "time_estimate_s": _seconds(fields.get("timeoriginalestimate")),
        "time_spent_s": _seconds(fields.get("timespent")),
    }
    return MappedIssue(row=row, assignee=_user(fields.get("assignee")))
