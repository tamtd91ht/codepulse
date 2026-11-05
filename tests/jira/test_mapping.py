"""Jira issue -> issues row (docs/specs/27 §5-6, §14)."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.jira.mapping import (
    FieldIds,
    FieldRef,
    complexity,
    find_field,
    latest_sprint,
    map_issue,
    resolve_fields,
    search_fields,
)
from app.jira.settings import JIRA_ID_OFFSET, JiraMapping

IDS = FieldIds(
    story_points=FieldRef("customfield_10002", "Story Points"),
    sprint=FieldRef("customfield_10004", "Sprint"),
    flagged=FieldRef("customfield_10010", "Flagged"),
)
BASE = "https://jira.example.vn/"


def issue(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": "Đăng nhập bằng OTP",
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "issuetype": {"name": "Story", "subtask": False},
        "labels": [],
        "priority": {"name": "Medium"},
        "created": "2026-10-01T09:00:00.000+0700",
        "updated": "2026-10-05T10:00:00.000+0700",
    }
    base.update(fields)
    return {"id": "10123", "key": "ABC-42", "fields": base}


def row(**fields: Any) -> dict[str, Any]:
    return map_issue(issue(**fields), JiraMapping(), IDS, BASE, 7).row


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        (None, (None, None)),
        (0, (None, None)),
        (0.5, ("XS", 1)),
        (1, ("XS", 1)),
        (1.5, ("S", 2)),
        (2, ("S", 2)),
        (3, ("M", 3)),
        (4, ("L", 5)),
        (5, ("L", 5)),
        (8, ("XL", 8)),
        (13, ("XL", 8)),
    ],
)
def test_story_points_round_up_to_a_level(points: Any, expected: tuple[Any, Any]) -> None:
    value = None if points is None else Decimal(str(points))
    assert complexity(value) == expected
    r = row(customfield_10002=points)
    assert (r["complexity"], r["complexity_points"]) == expected


@pytest.mark.parametrize("points", [10**6, -(10**6), 999999.999, 1e12])
def test_story_points_too_large_for_the_column_are_none(points: Any) -> None:
    r = row(customfield_10002=points)
    assert (r["story_points"], r["complexity"], r["complexity_points"]) == (None, None, None)
    assert row(customfield_10002=999999.99)["story_points"] == Decimal("999999.99")


def test_identity_url_and_open_state() -> None:
    r = row(customfield_10002=3.0)
    assert r["id"] == JIRA_ID_OFFSET + 10123
    assert (r["iid"], r["key"], r["source"], r["jira_project_id"]) == (42, "ABC-42", "jira", 7)
    assert r["project_id"] is None
    assert r["web_url"] == "https://jira.example.vn/browse/ABC-42"
    assert r["state"] == "opened" and r["closed_at_src"] is None
    assert r["story_points"] == Decimal("3.0")
    assert r["type"] == "feature"
    assert r["created_at_src"] == datetime(2026, 10, 1, 2, tzinfo=UTC)


def test_done_issue_closes_at_resolution_then_category_change_then_updated() -> None:
    done = {"name": "Done", "statusCategory": {"key": "done"}}
    tz = timezone(timedelta(hours=7))
    r = row(status=done, resolutiondate="2026-10-04T08:00:00.000+0700")
    assert r["state"] == "closed" and r["closed_at_src"] == datetime(2026, 10, 4, 8, tzinfo=tz)
    r = row(status=done, statuscategorychangedate="2026-10-03T08:00:00.000+0700")
    assert r["closed_at_src"] == datetime(2026, 10, 3, 8, tzinfo=tz)
    assert row(status=done)["closed_at_src"] == datetime(2026, 10, 5, 10, tzinfo=tz)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Story", "feature"),
        ("New Feature", "feature"),
        ("bug", "bug"),
        ("Improvement", "refactor"),
        ("Task", "chore"),
        ("Documentation", "docs"),
        ("Spike", "chore"),
    ],
)
def test_issue_types(name: str, expected: str) -> None:
    assert row(issuetype={"name": name})["type"] == expected


def test_sub_task_takes_its_parents_type_and_key() -> None:
    r = row(
        issuetype={"name": "Sub-task", "subtask": True},
        parent={"key": "ABC-40", "fields": {"issuetype": {"name": "Bug"}}},
    )
    assert (r["type"], r["parent_key"]) == ("bug", "ABC-40")
    assert r["complexity"] is None  # no points of its own: counted on the parent


def test_production_bugs_and_priorities() -> None:
    bug = {"name": "Bug"}
    assert row(issuetype=bug)["prod_severity"] is None
    assert row(issuetype=bug, labels=["Production"])["prod_severity"] == "P3"
    assert (
        row(issuetype=bug, labels=["prod-bug"], priority={"name": "Highest"})["prod_severity"]
        == "P1"
    )
    assert row(issuetype=bug, labels=["prod-bug"], priority={"name": "Blocker"})[
        "prod_severity"
    ] == ("P1")
    assert row(issuetype={"name": "Incident"}, priority={"name": "critical"})["prod_severity"] == (
        "P2"
    )
    # A Story with the label is not a production bug.
    assert row(labels=["production"])["prod_severity"] is None


def test_due_date_from_issue_then_sprint_then_fix_version() -> None:
    versions = [
        {"name": "2.1", "releaseDate": "2026-11-20"},
        {"name": "2.0", "releaseDate": "2026-11-10"},
    ]
    sprint = [{"id": 3, "name": "Sprint 3", "state": "active", "endDate": "2026-10-14T18:00:00Z"}]
    r = row(duedate="2026-10-09", customfield_10004=sprint, fixVersions=versions)
    assert (r["due_date"], r["milestone"]) == (date(2026, 10, 9), "Sprint 3")
    r = row(customfield_10004=sprint, fixVersions=versions)
    assert r["due_date"] == date(2026, 10, 14)
    r = row(fixVersions=versions)
    assert (r["due_date"], r["milestone"]) == (date(2026, 11, 10), "2.1")
    assert row()["due_date"] is None and row()["milestone"] is None


SPRINT_TEXT = (
    "com.atlassian.greenhopper.service.sprint.Sprint@1a2b[id={id},rapidViewId=1,state=CLOSED,"
    "name={name},startDate=2026-10-01T09:00:00.000+07:00,endDate={end},"
    "completeDate=<null>,sequence={id}]"
)


def test_sprints_as_strings_of_old_jira_dc() -> None:
    value = [
        SPRINT_TEXT.format(id=4, name="Sprint 4, hot", end="2026-10-28T18:00:00.000+07:00"),
        SPRINT_TEXT.format(id=3, name="Sprint 3", end="2026-10-14T18:00:00.000+07:00"),
    ]
    sprint = latest_sprint(value)
    assert sprint is not None and (sprint.name, sprint.end) == ("Sprint 4, hot", date(2026, 10, 28))
    r = row(customfield_10004=value)
    assert (r["milestone"], r["due_date"]) == ("Sprint 4, hot", date(2026, 10, 28))


def test_sprint_without_end_date_is_the_last_listed() -> None:
    value = [
        SPRINT_TEXT.format(id=5, name="Sprint 5", end="<null>"),
        SPRINT_TEXT.format(id=6, name="Sprint 6", end="<null>"),
    ]
    sprint = latest_sprint(value)
    assert sprint is not None and (sprint.name, sprint.end) == ("Sprint 6", None)
    objects = [{"name": "A", "endDate": None}, {"name": "B"}]
    assert latest_sprint(objects) == latest_sprint([{"name": "B"}])
    assert latest_sprint(None) is None and latest_sprint([]) is None


def test_blocked_by_flag_and_by_status() -> None:
    r = row(labels=["backend"], customfield_10010=[{"value": "Impediment"}])
    assert r["labels"] == ["backend", "blocked::impediment"]
    assert row(customfield_10010=[])["labels"] == []
    assert row(customfield_10010=None)["labels"] == []
    blocked = {"name": "Blocked by vendor", "statusCategory": {"key": "indeterminate"}}
    assert row(status=blocked)["labels"] == ["blocked::Blocked by vendor"]


def test_time_and_assignee() -> None:
    mapped = map_issue(
        issue(
            timeoriginalestimate=7200,
            timespent=3600,
            assignee={
                "key": "JIRAUSER10100",
                "name": "an.nguyen",
                "emailAddress": "an@x.vn",
                "displayName": "Nguyễn An",
            },
        ),
        JiraMapping(),
        IDS,
        BASE,
        7,
    )
    assert (mapped.row["time_estimate_s"], mapped.row["time_spent_s"]) == (7200, 3600)
    assert mapped.assignee is not None
    assert (mapped.assignee.key, mapped.assignee.name, mapped.assignee.email) == (
        "JIRAUSER10100",
        "an.nguyen",
        "an@x.vn",
    )
    assert mapped.assignee.display_name == "Nguyễn An"
    assert map_issue(issue(), JiraMapping(), IDS, BASE, 7).assignee is None


def test_custom_fields_are_found_by_name_case_insensitively() -> None:
    fields = [
        {"id": "summary", "name": "Summary", "custom": False},
        {"id": "customfield_10002", "name": "Story Points", "custom": True},
        {"id": "customfield_10004", "name": "Sprint", "custom": True},
        {"id": "customfield_10010", "name": "Flagged", "custom": True},
        {"id": "customfield_10020", "name": "Story point estimate", "custom": True},
    ]
    ids = resolve_fields(fields, JiraMapping())
    assert ids.as_json() == {
        "story_points": {"id": "customfield_10002", "name": "Story Points"},
        "sprint": {"id": "customfield_10004", "name": "Sprint"},
        "flagged": {"id": "customfield_10010", "name": "Flagged"},
    }
    found = find_field(fields, "STORY POINT ESTIMATE")
    assert found is not None and found.id == "customfield_10020"
    assert find_field(fields, "Nope") is None
    assert "customfield_10002" in search_fields(ids) and "summary" in search_fields(ids)
    assert resolve_fields([], JiraMapping()).as_json()["sprint"] is None


def test_unresolved_custom_fields_are_ignored() -> None:
    r = map_issue(issue(customfield_10002=5), JiraMapping(), FieldIds(), BASE, 7).row
    assert r["complexity"] is None and r["story_points"] is None
