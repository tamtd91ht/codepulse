"""docs/specs/20 §4.10 (D2): production rule, rollbacks, failure rate, recovery, lead time."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.insights.dora import Dep, Shipped, events, first_shipped, is_production, summarise

T0 = datetime(2026, 10, 5, 2, tzinfo=UTC)
VN = ZoneInfo("Asia/Ho_Chi_Minh")


def h(hours: float) -> datetime:
    return T0 + timedelta(hours=hours)


def d(dep_id: int, at: float | None, sha: str, status: str = "success", env: str = "prod") -> Dep:
    return Dep(dep_id, 1, env, status, sha, h(at) if at is not None else None)


@pytest.mark.parametrize(
    ("env", "tier", "override", "prod"),
    [
        ("production", None, None, True),
        ("Prod", None, None, True),
        ("live", "production", None, True),
        ("production", "staging", None, False),  # the tier wins over the name
        ("staging", None, None, False),
        ("prd-eu", "staging", "prd-eu", True),  # an admin override wins over both
        ("production", "production", "prd-eu", False),
        (None, None, None, False),
    ],
)
def test_is_production(env: str | None, tier: str | None, override: str | None, prod: bool) -> None:
    assert is_production(env, tier, override) is prod


def test_a_rollback_and_a_failed_deploy_with_their_recoveries() -> None:
    deps = [
        d(1, 0, "a"),
        d(2, 10, "b"),  # bad change...
        d(3, 12, "a"),  # ...rolled back to a: recovered 2 h after b went out
        d(4, 20, "c", "failed"),
        d(5, 25, "c"),  # recovered 5 h after the failed deploy
        d(6, 30, "c"),  # a re-deploy of the same sha is not a rollback
        d(7, 31, "x", "running"),  # unfinished: ignored
    ]
    ev = events(deps)[(1, "prod")]
    assert [x.id for x in ev.rollbacks] == [3]
    assert [x.id for x in ev.rolled_back] == [2]
    assert [x.id for x in ev.failed] == [4]
    assert [hours for _at, hours in ev.recoveries] == [2.0, 5.0]


def test_environments_are_separate() -> None:
    deps = [d(1, 0, "a"), d(2, 1, "b", env="eu"), d(3, 2, "a", env="eu"), d(4, 3, "b")]
    # eu: b then a (a never deployed to eu before): no rollback; prod: a then b, no rollback.
    assert all(not e.rollbacks for e in events(deps).values())


def test_summarise_window_counts_and_rates() -> None:
    deps = [
        d(1, -100, "a"),  # history before the window: makes 3 a rollback
        d(2, 10, "b"),
        d(3, 12, "a"),
        d(4, 20, "c", "failed"),
        d(5, 25, "c"),
    ]
    shipped = [
        Shipped(1, 1, h(-30), h(-5), h(10)),  # lead 40 h, deploy 15 h
        Shipped(2, 1, h(0), h(20), h(25)),  # lead 25 h, deploy 5 h
        Shipped(3, 1, h(-300), h(-200), h(-100)),  # outside
    ]
    s = summarise(deps, shipped, h(0), h(7 * 24), VN, min_sample=1)
    assert (s["deployments"], s["failed"], s["rollbacks"]) == (3, 1, 1)
    assert s["per_week"] == 3.0 and s["deploy_days"] == 2  # 05/10 (b, a) and 06/10 (c)
    assert s["change_failure_rate"] == 50.0  # (1 failed + 1 rolled back) / 4 finished
    assert s["recovery_h"] == {"n": 2, "median": 3.5, "p75": 4.2}
    assert s["lead_time_h"]["median"] == 32.5 and s["deploy_h"]["median"] == 10.0
    assert s["shipped_mrs"] == 2


def test_summarise_nothing() -> None:
    s = summarise([], [], h(0), h(24), VN, min_sample=1)
    assert s["deployments"] == 0 and s["change_failure_rate"] is None
    assert s["lead_time_h"]["median"] is None


def test_first_shipped_keeps_the_earliest_deployment() -> None:
    got = first_shipped([(7, 1, h(5)), (7, 2, h(1)), (8, 3, None), (9, 4, h(2))])
    assert got == {7: (2, h(1)), 9: (4, h(2))}
