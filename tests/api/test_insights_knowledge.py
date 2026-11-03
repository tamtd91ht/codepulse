"""docs/specs/20 §4.9 through the API: /insights/knowledge."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Account, Commit, CommitFile, User
from tests.api.conftest import World

pytestmark = pytest.mark.integration

END = "2026-10-31"
DAY = datetime(2026, 10, 20, 3, tzinfo=UTC)


@pytest.fixture
def seeded(db_session: Session, world: World, settings: Settings) -> None:
    settings.knowledge_min_commits = 3
    world.project.last_synced_at = datetime.now(UTC)
    s = db_session

    def email(user: User | None, key: str) -> Account:
        acc = Account(kind="git_email", external_key=key, user_id=user.id if user else None)
        s.add(acc)
        s.flush()
        return acc

    an, an2, binh = email(world.an, "an@x.vn"), email(world.an, "an@home"), email(world.binh, "b@x")
    stranger = email(None, "who@else")
    n = 0

    def commit(
        author: Account,
        files: dict[str, int],
        days_ago: int = 1,
        merge: bool = False,
        at: datetime | None = None,
    ) -> None:
        nonlocal n
        n += 1
        sha = f"{n:040x}"
        s.add(
            Commit(
                sha=sha,
                project_id=world.project.id,
                author_account_id=author.id,
                authored_at=at or DAY - timedelta(days=days_ago),
                is_merge=merge,
            )
        )
        s.flush()
        s.add_all(CommitFile(sha=sha, path=p, additions=c, deletions=0) for p, c in files.items())

    core = {"src/core/a.py": 100, "src/core/b.py": 50, "src/core/c.py": 10}
    # core: an (two e-mails, one person) holds ~91 % of the touches over 5 commits.
    for i in range(3):
        commit(an, core, days_ago=i + 1)
    commit(an2, {"src/core/a.py": 75})
    commit(binh, {"src/core/a.py": 50})
    # A bulk commit by binh (an import, a rename): one touch in all, lines not shown.
    commit(binh, {f"src/core/gen{i}.py": 100 for i in range(2000)})
    # Deploy configuration: binh alone, 3 commits, binh now inactive -> orphaned too.
    deploy = {"deploy/a.yaml": 10, "deploy/b.yaml": 10, "deploy/c.yaml": 10}
    for i in range(3):
        commit(binh, deploy, days_ago=i + 1)
    # Docs: shown, never flagged.
    for i in range(3):
        commit(binh, {"README.md": 10}, days_ago=i + 1)
    # web: shared by an and the unmapped stranger.
    for i in range(3):
        commit(an, {"src/web/x.ts": 30, "src/web/w.ts": 1}, days_ago=i + 1)
        commit(stranger, {"src/web/y.ts": 30, "src/web/v.ts": 1}, days_ago=i + 1)
    # pay: only the unmapped stranger: link the account before calling it a single owner.
    for i in range(3):
        commit(stranger, {"src/pay/p.py": 5, "src/pay/q.py": 5, "src/pay/r.py": 5}, days_ago=i + 1)
    # Lone files fold into "(khác)": too few commits to flag.
    commit(an, {"src/a.py": 5})
    commit(an, {"a/b/c/d.py": 5})
    # Generated code is noise.
    for i in range(3):
        commit(binh, {"target/x.class": 1, "package-lock.json": 9}, days_ago=i + 1)
    # Ignored: a merge commit, work outside the 180-day window, and a commit at the instant the
    # window ends (midnight after END in Asia/Ho_Chi_Minh).
    commit(binh, {"src/edge/a.py": 10}, at=datetime(2026, 10, 31, 17, tzinfo=UTC))
    commit(binh, {"src/core/a.py": 10_000}, merge=True)
    commit(binh, {"src/core/a.py": 10_000}, days_ago=400)
    world.binh.active = False
    s.flush()


def get(client: TestClient, path: str, headers: dict[str, str]) -> Any:
    res = client.get(path, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_components_bus_factor_and_flags(
    client: TestClient, world: World, seeded: None, as_user: Any
) -> None:
    body = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.admin))
    by_name = {m["name"]: m for m in body["modules"]}
    assert set(by_name) == {"core", "web", "pay", "(khác)", "(build & cấu hình)", "(tài liệu)"}
    assert "target" not in str(by_name) and "lock" not in str(by_name)  # noise is not shown
    core = by_name["core"]
    assert core["churn"] == 3 * 160 + 75 + 50  # the bulk commit's lines are not counted
    assert core["commits"] == 6
    assert core["bus_factor"] == 1 and core["single_owner"] and not core["orphaned"]
    assert core["owners"][0]["name"] == "An" and core["owners"][0]["share"] > 55
    deploy = by_name["(build & cấu hình)"]
    assert deploy["kind"] == "config" and deploy["single_owner"] and deploy["orphaned"]
    docs = by_name["(tài liệu)"]
    assert docs["kind"] == "docs" and not docs["single_owner"] and not docs["orphaned"]
    web = by_name["web"]
    assert web["bus_factor"] == 1 and web["top_share"] == 50.0
    assert not web["single_owner"]  # an even split is two owners
    assert [o["name"] for o in web["owners"]] == ["An", "Chưa gắn người"]  # mapped first on a tie
    assert web["unlinked_share"] == 50.0
    pay = by_name["pay"]
    assert pay["unattributed"] and not pay["single_owner"]
    assert not by_name["(khác)"]["single_owner"]  # too few commits to flag
    assert body["totals"] == {
        "modules": 6,
        "nodes": 7,  # the repo and its 6 components (one build root: no module level)
        "warnings": 3,  # core, deploy (orphaned too), pay (unattributed)
        "single_owner": 2,
        "unattributed": 1,
        "orphaned": 1,
        "inherited": 0,
        "shown": 6,
    }
    (repo,) = [n for n in body["nodes"] if n["level"] == 0]
    assert repo["tier"] == "repo" and not repo["counted"]
    assert {n["parent"] for n in body["nodes"] if n["level"] == 1} == {repo["module"]}
    assert body["orphan_after_days"] == 60
    assert body["modules"][0]["single_owner"] or body["modules"][0]["unattributed"]
    assert body["maps"][str(world.project.id)]["source"] == "auto"
    assert body["confidence"]["level"] == "ok"


def test_owner_names_follow_work_visibility(
    client: TestClient, world: World, seeded: None, as_user: Any, outsider_leads: Any
) -> None:
    lead = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.lead))
    core = next(m for m in lead["modules"] if m["name"] == "core")
    assert core["owners"][0]["name"] == "An"
    outsider = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.outsider))
    core = next(m for m in outsider["modules"] if m["name"] == "core")
    assert core["owners"][0] == {
        "user_id": None,
        "name": "Một thành viên",
        "hidden": True,
        "mapped": None,
        "share": 62.3,
    }
    assert core["bus_factor"] == 1  # the risk itself is the team's to see


def test_no_file_data_is_insufficient(
    client: TestClient, db_session: Session, world: World, settings: Settings, as_user: Any
) -> None:
    # An idle repo is no reason to run a backfill.
    idle = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.admin))
    assert idle["modules"] == []
    assert "no_file_data" not in {r["code"] for r in idle["confidence"]["reasons"]}
    # Commits in the window, none with file rows: the backfill was never run.
    db_session.add(
        Commit(sha="f" * 40, project_id=world.project.id, authored_at=DAY, is_merge=False)
    )
    db_session.flush()
    body = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.admin))
    assert body["confidence"]["level"] == "insufficient"
    assert body["confidence"]["reasons"][-1]["code"] == "no_file_data"


def test_partial_file_data_and_scope(
    client: TestClient,
    db_session: Session,
    world: World,
    seeded: None,
    as_user: Any,
    outsider_leads: Any,
) -> None:
    # Commits synced before commit_files existed: no file rows for most of the window.
    for i in range(40):
        db_session.add(
            Commit(
                sha=f"e{i:039x}",
                project_id=world.project.id,
                authored_at=DAY - timedelta(days=2),
                is_merge=False,
            )
        )
    db_session.flush()
    body = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.admin))
    assert body["confidence"]["level"] == "low"
    assert body["confidence"]["reasons"][-1]["code"] == "partial_file_data"
    assert body["confidence"]["reasons"][-1]["repos"] == ["demo/p"]
    hidden = client.get(
        "/api/v1/insights/knowledge?business_project_id=999999", headers=as_user(world.outsider)
    )
    assert hidden.status_code == 404


def test_component_map_and_the_ai_grouping(
    client: TestClient,
    world: World,
    seeded: None,
    as_user: Any,
    db_session: Session,
    settings: Settings,
    queue: Any,
) -> None:
    from app.models import AuditLog, RepoComponentMap
    from app.review.providers import FakeLLMProvider
    from app.workers.component_jobs import run_component_map
    from app.workers.knowledge_jobs import month_spend_breakdown

    pid = world.project.id
    url = f"/api/v1/insights/components?project_id={pid}"
    auto = get(client, url, as_user(world.lead))
    assert auto["source"] == "auto" and auto["ai"] is None and auto["can_rebuild"] is False
    by_name = {c["name"]: c for c in auto["components"]}
    assert {"core", "web", "pay"} <= set(by_name)
    assert by_name["core"]["files"] == 2003 and by_name["core"]["samples"]

    rebuild = f"/api/v1/admin/insights/components/{pid}"
    assert client.post(rebuild, headers=as_user(world.lead)).status_code == 403
    assert client.post(rebuild, headers=as_user(world.admin)).status_code == 202
    assert client.post(rebuild, headers=as_user(world.admin)).status_code == 409  # running
    assert queue.jobs[-1][0] == "app.workers.component_jobs.component_map_job"

    core_id, web_id = by_name["core"]["id"], by_name["web"]["id"]
    answer = {
        "groups": [
            {
                "name": "Lõi giao dịch",
                "description": "Xử lý giao dịch và giao diện web",
                "kind": "domain",
                "members": [core_id, web_id, "invented::x"],
            }
        ],
        "confidence": 0.7,
    }
    provider = FakeLLMProvider(lambda p: answer)
    assert run_component_map(db_session, pid, provider, settings) == "done"
    (prompt,) = provider.prompts
    assert "src/core/a.py" in prompt.user and "x.class" not in prompt.user  # paths only, no noise

    grouped = get(client, url, as_user(world.admin))
    assert grouped["source"] == "llm" and grouped["ai"]["status"] == "done"
    grouped_core = next(c for c in grouped["components"] if c["name"] == "Lõi giao dịch")
    assert grouped_core["source"] == "llm" and set(grouped_core["parts"]) == {"core", "web"}
    body = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.admin))
    names = {m["name"] for m in body["modules"]}
    assert "Lõi giao dịch" in names and "core" not in names and "pay" in names
    assert body["maps"][str(pid)]["source"] == "llm"
    assert "insights.components.cost" in set(db_session.scalars(select(AuditLog.action)))
    assert month_spend_breakdown(db_session, datetime.now(UTC))["component_maps_usd"] > 0

    # Back to the automatic map.
    assert client.delete(rebuild, headers=as_user(world.admin)).status_code == 204
    assert db_session.get(RepoComponentMap, pid) is None
    assert get(client, url, as_user(world.admin))["source"] == "auto"


def test_the_ai_grouping_respects_the_external_llm_switch(
    client: TestClient, world: World, seeded: None, as_user: Any, db_session: Session
) -> None:
    world.project.allow_external_llm = False
    db_session.commit()
    res = client.post(
        f"/api/v1/admin/insights/components/{world.project.id}", headers=as_user(world.admin)
    )
    assert res.status_code == 409


@pytest.fixture
def multi_module(db_session: Session, world: World, settings: Settings) -> None:
    """A Maven repo of two modules: an holds the whole account module, binh holds voip."""
    settings.knowledge_min_commits = 3
    world.project.last_synced_at = datetime.now(UTC)
    s = db_session
    accounts = {}
    for user, key in ((world.an, "an@x.vn"), (world.binh, "b@x"), (world.lead, "lead@x")):
        acc = Account(kind="git_email", external_key=key, user_id=user.id)
        s.add(acc)
        s.flush()
        accounts[key] = acc
    n = 0

    def commit(author: Account, files: list[str], days_ago: int) -> None:
        nonlocal n
        n += 1
        sha = f"{n:040x}"
        s.add(
            Commit(
                sha=sha,
                project_id=world.project.id,
                author_account_id=author.id,
                authored_at=DAY - timedelta(days=days_ago),
            )
        )
        s.flush()
        s.add_all(CommitFile(sha=sha, path=p, additions=10, deletions=0) for p in files)

    acct = "svc/account/src/main/java/com/v/account/"
    voip = "svc/voip/src/main/java/com/v/voip/"
    s.add_all([])
    commit(accounts["an@x.vn"], ["svc/pom.xml", "svc/account/pom.xml", "svc/voip/pom.xml"], 9)
    for i in range(6):  # account: an everywhere, in two features
        commit(
            accounts["an@x.vn"],
            [f"{acct}wallet/{x}.java" for x in "ABC"] + [f"{acct}billing/{x}.java" for x in "DEF"],
            i + 1,
        )
    for i in range(6):  # voip: binh, a pocket of their own
        commit(
            accounts["b@x"],
            [f"{voip}call/{x}.java" for x in "ABC"] + [f"{voip}media/{x}.java" for x in "XYZ"],
            i + 1,
        )
    commit(accounts["lead@x"], [f"{voip}call/A.java"], 1)
    s.flush()


def test_the_tree_counts_a_warning_once_at_its_level(
    client: TestClient, world: World, multi_module: None, as_user: Any
) -> None:
    body = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.admin))
    nodes = {(n["tier"], n["name"]): n for n in body["nodes"]}
    account, voip = nodes[("module", "account")], nodes[("module", "voip")]
    wallet, billing = nodes[("component", "wallet")], nodes[("component", "billing")]
    repo = next(n for n in body["nodes"] if n["level"] == 0)
    # an holds two thirds of the whole repo: one warning, on the repo.
    assert repo["contributors"] == 3 and repo["single_owner"] and repo["counted"]
    assert repo["why"] == "cả repo phụ thuộc một người"
    for child in (account, wallet, billing):  # an again: inherited, not counted again
        assert child["single_owner"] and child["inherited"] and not child["counted"]
    assert wallet["parent"] == account["module"] and account["parent"] == repo["module"]
    # voip is binh's: a pocket of knowledge of its own, counted.
    assert voip["single_owner"] and voip["counted"] and voip["why"] == "người nắm khác cấp trên"
    call = nodes[("component", "call")]
    assert call["inherited"] and not call["counted"]  # binh again, under voip
    assert body["totals"]["warnings"] == 2 and body["totals"]["inherited"] == 5
    assert max(n["level"] for n in body["nodes"]) <= 3
