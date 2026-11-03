"""docs/specs/20 §4.10 (D2) through the API: /insights/dora."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import BusinessProject, Deployment
from tests.api.conftest import World
from tests.api.seeding import Seeder, at

pytestmark = pytest.mark.integration
WEEK = "2026-10-05"


@pytest.fixture
def seeded(db_session: Session, world: World, settings: Settings) -> None:
    settings.insights_min_sample = 1
    sd = Seeder(db_session, world.project)
    an = sd.account(world.an, "an")
    first = sd.mr(an, opened=0, merged=5, first_commit=-10)
    second = sd.mr(an, opened=6, merged=20, first_commit=0)

    def deploy(dep_id: int, hours: float, sha: str, status: str = "success", **kw: Any) -> None:
        shipped = {2: [first.iid], 3: [second.iid, 4242]}.get(dep_id)  # 4242: never collected
        db_session.add(
            Deployment(
                id=dep_id,
                project_id=world.project.id,
                environment=kw.get("env", "production"),
                environment_tier=kw.get("tier"),
                sha=sha,
                status=status,
                finished_at_src=at(hours),
                mr_iids=shipped,
            )
        )

    deploy(1, -200, "a")  # last month: makes 4 a rollback
    deploy(2, 10, "b")  # ships the first MR (lead 20 h)
    deploy(3, 30, "c")  # ships the second MR (lead 30 h)...
    deploy(4, 32, "a")  # ...rolled back: c was a failed change
    deploy(5, 40, "d", "failed")
    deploy(6, 44, "d")
    deploy(7, 12, "z", env="staging")  # not production
    deploy(8, 13, "y", env="live", tier="production")  # production by its tier
    db_session.flush()


def test_dora_numbers(client: TestClient, world: World, seeded: None, as_user: Any) -> None:
    res = client.get(f"/api/v1/insights/dora?period=week&start={WEEK}", headers=as_user(world.lead))
    assert res.status_code == 200, res.text
    body = res.json()
    t = body["totals"]
    assert (t["deployments"], t["failed"], t["rollbacks"]) == (5, 1, 1)  # 2, 3, 4, 6, 8
    assert t["change_failure_rate"] == 33.3  # (failed 5 + rolled back 3) / 6 finished
    assert t["lead_time_h"]["median"] == 25.0 and t["shipped_mrs"] == 2
    assert t["recovery_h"]["n"] == 2  # 3 -> 4 (2 h), 5 -> 6 (4 h)
    assert t["delta"]["deployments"] == 5
    kinds = {(f["id"], f["kind"]) for f in body["failures"]}
    assert kinds == {(3, "rolled_back"), (5, "failed")}
    repo = body["repos"][0]
    assert repo["environments"] == ["live", "production", "staging"]  # every one, to choose from
    assert repo["production_detected"] == ["live", "production"]
    assert body["confidence"]["level"] in ("ok", "low")


def test_an_override_changes_what_production_is(
    client: TestClient, db_session: Session, world: World, seeded: None, as_user: Any
) -> None:
    world.project.production_environment = "staging"
    db_session.flush()
    t = client.get(f"/api/v1/insights/dora?start={WEEK}", headers=as_user(world.admin)).json()
    assert t["totals"]["deployments"] == 1 and t["totals"]["shipped_mrs"] == 0


def test_no_deployments_is_insufficient_and_scope_holds(
    client: TestClient, db_session: Session, world: World, as_user: Any
) -> None:
    body = client.get(f"/api/v1/insights/dora?start={WEEK}", headers=as_user(world.admin)).json()
    assert body["confidence"]["level"] == "insufficient"
    assert body["confidence"]["reasons"][-1]["code"] == "no_deployments"
    secret = BusinessProject(name="Secret")
    db_session.add(secret)
    db_session.flush()
    res = client.get(
        f"/api/v1/insights/dora?business_project_id={secret.id}", headers=as_user(world.an)
    )
    assert res.status_code == 404


def test_changing_production_queues_a_full_resync(
    client: TestClient, world: World, queue: Any, as_user: Any
) -> None:
    res = client.patch(
        f"/api/v1/projects/{world.project.id}",
        headers=as_user(world.admin),
        json={"production_environment": "live"},
    )
    assert res.status_code == 200 and res.json()["production_environment"] == "live"
    assert queue.jobs[-1][0] == "app.collector.sync.sync_project_job"
    assert queue.jobs[-1][2]["full"] is True
    count = len(queue.jobs)
    client.patch(  # unchanged: nothing to collect again
        f"/api/v1/projects/{world.project.id}",
        headers=as_user(world.admin),
        json={"production_environment": "live"},
    )
    assert len(queue.jobs) == count


def test_incidents_in_dora(
    client: TestClient, db_session: Session, world: World, seeded: None, as_user: Any
) -> None:
    from app.models import Incident

    db_session.add_all(
        [
            Incident(
                id=1,
                project_id=world.project.id,
                iid=5,
                title="API down",
                state="closed",
                created_at_src=at(33),
                closed_at_src=at(35),
                fix_mr_iids=[7],
                web_url="https://gitlab.example.com/demo/p/-/issues/5",
            ),
            Incident(
                id=2,
                project_id=world.project.id,
                iid=6,
                title="Slow",
                state="opened",
                created_at_src=at(41),
                web_url="javascript:alert(1)",
            ),
        ]
    )
    db_session.flush()
    body = client.get(f"/api/v1/insights/dora?start={WEEK}", headers=as_user(world.lead)).json()
    inc = body["incidents"]
    assert (inc["opened"], inc["closed"], inc["open_now"]) == (2, 1, 1)
    assert inc["per_deployment"] == 0.4  # 2 incidents / 5 deployments
    assert inc["restore_h"]["median"] == 2.0
    assert [r["title"] for r in inc["recent"]] == ["Slow", "API down"]
    assert inc["recent"][0]["web_url"] is None  # only http(s) links go out
    assert inc["recent"][1]["fix_mrs"] == 1
