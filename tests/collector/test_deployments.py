"""docs/specs/20 §5.2 (D2): environments, deployments and the MRs production deployments shipped."""

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.gitlab import collect as ep
from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.collect import GitLabCollector
from app.collector.gitlab.convert import Converter, raw_rows_query
from app.collector.gitlab.extract import extract
from app.models import Deployment, MergeRequest, Project, RawGitlab
from tests.collector.fake_gitlab import API, BASE, mount
from tests.collector.test_collect import make_project
from tests.collector.test_extract_convert import IGNORE

pytestmark = pytest.mark.integration
P = f"{API}/projects/1001"


def dep(
    dep_id: int, env: str, status: str, sha: str, finished: str | None = "2026-10-05T03:00:00Z"
) -> dict[str, object]:
    return {
        "id": dep_id,
        "status": status,
        "sha": sha,
        "ref": "main",
        "created_at": "2026-10-05T02:00:00Z",
        "updated_at": "2026-10-05T03:00:00Z",
        "finished_at": finished,
        "environment": {"id": 1, "name": env},
    }


def run(session: Session, router: respx.MockRouter, project: Project) -> None:
    with GitLabClient(BASE, "t") as client:
        GitLabCollector(client).collect(session, project, None)
    rows = session.scalars(raw_rows_query(project)).all()
    Converter(session, project, IGNORE).run(extract(rows))
    session.flush()


def test_production_deployments_and_their_mrs(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        router.get(f"{P}/environments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"name": "live", "tier": "production"},  # tier wins over the name (D2)
                    {"name": "production", "tier": "staging"},
                ],
            )
        )
        router.get(f"{P}/deployments").mock(
            return_value=httpx.Response(
                200,
                json=[
                    dep(11, "live", "success", "a" * 40),
                    dep(12, "live", "failed", "b" * 40),
                    dep(13, "production", "success", "c" * 40),
                    dep(14, "live", "running", "d" * 40, finished=None),
                ],
            )
        )
        shipped = router.get(f"{P}/deployments/11/merge_requests").mock(
            return_value=httpx.Response(200, json=[{"iid": 1}, {"iid": 999}])
        )
        run(db_session, router, project)
    assert shipped.called
    # Only the successful production one asks GitLab for its MRs.
    asked = {r.endpoint for r in db_session.scalars(select(RawGitlab))}
    assert ep.EP_DEPLOYMENT_MRS in asked
    deps = {d.id: d for d in db_session.scalars(select(Deployment))}
    assert set(deps) == {11, 12, 13, 14}
    assert (deps[11].environment, deps[11].environment_tier) == ("live", "production")
    assert deps[13].environment_tier == "staging"
    assert deps[12].finished_at_src is not None and deps[14].finished_at_src is None
    # Kept as iids: 999 is not collected (yet) and still links once it is.
    assert deps[11].mr_iids == [1, 999]
    assert deps[12].mr_iids is None and deps[13].mr_iids is None


def test_an_admin_override_names_production(db_session: Session) -> None:
    project = make_project(db_session)
    project.production_environment = "production"
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        router.get(f"{P}/environments").mock(
            return_value=httpx.Response(200, json=[{"name": "production", "tier": "staging"}])
        )
        router.get(f"{P}/deployments").mock(
            return_value=httpx.Response(200, json=[dep(21, "production", "success", "e" * 40)])
        )
        shipped = router.get(f"{P}/deployments/21/merge_requests").mock(
            return_value=httpx.Response(200, json=[{"iid": 1}])
        )
        run(db_session, router, project)
    assert shipped.called


def test_deployments_the_token_cannot_read_are_skipped(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        router.get(f"{P}/environments").mock(
            return_value=httpx.Response(403, json={"message": "403 Forbidden"})
        )
        run(db_session, router, project)
    assert db_session.scalars(select(Deployment)).all() == []
    assert db_session.scalars(select(MergeRequest)).all()  # the rest of the sync went on


def test_an_empty_mr_list_right_after_a_deployment_is_asked_again(db_session: Session) -> None:
    """GitLab links a deployment's MRs a little after it succeeds."""
    from datetime import UTC, datetime

    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        router.get(f"{P}/environments").mock(
            return_value=httpx.Response(200, json=[{"name": "production", "tier": "production"}])
        )
        router.get(f"{P}/deployments").mock(
            return_value=httpx.Response(
                200, json=[dep(31, "production", "success", "f" * 40, "2026-10-05T03:00:00Z")]
            )
        )
        router.get(f"{P}/deployments/31/merge_requests").mock(
            return_value=httpx.Response(200, json=[])
        )
        just_after = datetime(2026, 10, 5, 3, 2, tzinfo=UTC)
        with GitLabClient(BASE, "t") as client:
            GitLabCollector(client, clock=lambda: just_after).collect(db_session, project, None)
        Converter(db_session, project, IGNORE).run(
            extract(db_session.scalars(raw_rows_query(project)).all())
        )
        db_session.flush()
    stored = db_session.get(Deployment, 31)
    assert stored is not None and stored.mr_iids is None  # not "shipped nothing"


def test_a_tier_change_applies_to_the_whole_history(db_session: Session) -> None:
    project = make_project(db_session)
    for tier in ("staging", "production"):
        with respx.mock(assert_all_called=False) as router:
            mount(router)
            router.get(f"{P}/environments").mock(
                return_value=httpx.Response(200, json=[{"name": "live", "tier": tier}])
            )
            batch = [dep(41, "live", "failed", "g" * 40)] if tier == "staging" else []
            router.get(f"{P}/deployments").mock(return_value=httpx.Response(200, json=batch))
            run(db_session, router, project)
    stored = db_session.get(Deployment, 41)
    assert stored is not None and stored.environment_tier == "production"


def test_first_deployment_sync_backfills_and_removal_counts_them(db_session: Session) -> None:
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    from app.collector.removal import remove_projects
    from app.collector.sync import sync_project
    from app.models import Connection

    project = make_project(db_session)
    project.last_synced_at = datetime(2026, 10, 20, tzinfo=UTC)  # synced before 9.8
    db_session.flush()

    def client(connection: Connection) -> GitLabClient:
        return GitLabClient(BASE, "t")

    with respx.mock(assert_all_called=False) as router:
        mount(router)
        listed = router.get(f"{P}/deployments").mock(
            return_value=httpx.Response(200, json=[dep(51, "production", "failed", "h" * 40)])
        )
        router.get(f"{P}/environments").mock(
            return_value=httpx.Response(200, json=[{"name": "production", "tier": "production"}])
        )
        sync_project(
            db_session,
            project,
            client,
            backfill_days=90,
            ignore_paths=IGNORE,
            now=datetime(2026, 11, 3, tzinfo=UTC),
        )
        first = listed.calls[-1].request.url.params["updated_after"]
        sync_project(
            db_session,
            project,
            client,
            backfill_days=90,
            ignore_paths=IGNORE,
            now=datetime(2026, 11, 3, 1, tzinfo=UTC),
        )
        second = listed.calls[-1].request.url.params["updated_after"]
    assert first.startswith("2026-08-05")  # the whole backfill window, once
    assert second.startswith("2026-11-02T23")  # then incremental (1 h overlap), like the rest
    removed = remove_projects(db_session, [project.id], ZoneInfo("Asia/Ho_Chi_Minh"))
    assert removed.rows["deployments"] == 1
