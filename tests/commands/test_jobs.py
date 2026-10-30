from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any

import pytest

from app.commands import jobs, runner
from app.config import Settings
from app.models import CommandRun


def test_timeouts_leave_room_for_two_clones() -> None:
    assert jobs.job_timeout_s(1800) >= 1800 + 2 * 600
    assert jobs.job_id(7) == "command-7"


def test_a_dotted_command_keeps_its_name() -> None:
    assert jobs._rel("v1.2") == PurePosixPath("v1.2.md")
    assert jobs._rel("report:weekly") == PurePosixPath("report/weekly.md")


def test_cost_is_the_cli_figure_or_the_cap_as_an_estimate() -> None:
    limits: dict[str, Any] = {"command_max_budget_usd": 5}
    meta: dict[str, Any] = {}
    done = runner.Outcome(ok=True, cost_usd=0.1234567, turns=3)
    assert jobs._spent(done, limits, meta, ran=True) == Decimal("0.1235")
    assert meta == {}
    timed_out = runner.Outcome(ok=False, error="quá thời gian")
    assert jobs._spent(timed_out, limits, meta, ran=True) == Decimal(5)
    assert meta == {"cost_estimated": True}
    assert jobs._spent(None, limits, {}, ran=False) is None  # cancelled while cloning


def test_a_queued_run_fails_once_the_feature_is_off() -> None:
    off = Settings(command_runs_enabled=False)
    with pytest.raises(RuntimeError, match="COMMAND_RUNS_ENABLED"):
        jobs._plan(None, CommandRun(id=1), off)  # type: ignore[arg-type]
