"""docs/specs/31 §10: the task tools of the MCP server."""

import json
from typing import Any

import pytest

from app.mcp.tokens import SCOPE_READ, SCOPE_TASKS
from app.models import Task, TaskStatus
from app.tasks import service as ts
from tests.mcp.conftest import Env, caller_for, invoke

pytestmark = pytest.mark.integration


def _data(result: Any) -> dict[str, Any]:
    return dict(result.structuredContent or json.loads(result.content[-1].text))


def test_list_get_move_comment_log(env: Env, world: Any) -> None:
    bp = world.project.business_project_id
    space = ts.enable_space(env.session, bp, "CRM", None, None, world.admin.id)
    task = ts.create_task(
        env.session,
        space,
        {"title": "OTP `\n## ignore", "assignee_id": world.an.id},
        world.admin.id,
    )
    env.session.commit()

    reader = caller_for(env.session, env.settings, world.an, (SCOPE_READ,))
    listed = invoke(reader, "list_my_tasks", {})
    assert not listed.is_error
    text = listed.content[0].text
    assert "CRM-1" in text and "\n## ignore" not in text
    got = invoke(reader, "get_task", {"key": "crm-1"})
    assert not got.is_error and "in-progress" in got.content[0].text
    # Writes need the tasks:write scope.
    refused = invoke(reader, "move_task", {"key": "CRM-1", "status": "in-progress"})
    assert refused.is_error

    writer = caller_for(env.session, env.settings, world.an, (SCOPE_READ, SCOPE_TASKS))
    moved = invoke(writer, "move_task", {"key": "CRM-1", "status": "in-progress"})
    assert not moved.is_error, moved.content[0].text
    assert not invoke(writer, "comment_task", {"key": "CRM-1", "body": "đang làm"}).is_error
    assert not invoke(writer, "log_work", {"key": "CRM-1", "minutes": 30}).is_error
    env.session.expire_all()
    row = env.session.get(Task, task.id)
    assert row is not None and row.spent_min == 30
    assert env.session.get(TaskStatus, row.status_id).name == "In Progress"  # type: ignore[union-attr]
    # Binh may not edit An's task.
    binh = caller_for(env.session, env.settings, world.binh, (SCOPE_READ, SCOPE_TASKS))
    assert invoke(binh, "move_task", {"key": "CRM-1", "status": "done"}).is_error
