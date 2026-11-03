"""docs/specs/20 §4.9 through the API: /insights/knowledge."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.insights.knowledge import module_of
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

    # src/core: an (two e-mails, one person) holds 91 % over 4 commits -> single owner.
    for i in range(3):
        commit(an, {"src/core/a.py": 100, "src/core/b.py": 50}, days_ago=i + 1)
    commit(an2, {"src/core/a.py": 75})
    commit(binh, {"src/core/a.py": 50})
    # docs at the root: binh alone, 3 commits, binh now inactive -> orphaned too.
    for i in range(3):
        commit(binh, {"README.md": 10}, days_ago=i + 1)
    # src/web: shared by an and the unmapped stranger.
    for i in range(3):
        commit(an, {"src/web/x.ts": 30}, days_ago=i + 1)
        commit(stranger, {"src/web/y.ts": 30}, days_ago=i + 1)
    # Shallower and deeper than the module depth (2): "src" and "a/b".
    commit(an, {"src/a.py": 5})
    commit(an, {"a/b/c/d.py": 5})
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


def test_modules_bus_factor_and_flags(
    client: TestClient, world: World, seeded: None, as_user: Any
) -> None:
    body = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.admin))
    by_module = {m["module"]: m for m in body["modules"]}
    seeded_paths = ("src/core/a.py", "README.md", "src/web/x.ts", "src/a.py", "a/b/c/d.py")
    assert set(by_module) == {module_of(p, 2) for p in seeded_paths}  # SQL twin of module_of
    assert set(by_module) == {"src/core", "(root)", "src/web", "src", "a/b"}
    core = by_module["src/core"]
    assert core["churn"] == 3 * 150 + 75 + 50 and core["commits"] == 5
    assert core["bus_factor"] == 1 and core["single_owner"] and not core["orphaned"]
    assert core["owners"][0]["name"] == "An" and core["owners"][0]["share"] == 91.3
    root = by_module["(root)"]
    assert root["single_owner"] and root["orphaned"]  # binh is switched off
    web = by_module["src/web"]
    assert web["bus_factor"] == 1 and web["top_share"] == 50.0
    assert not web["single_owner"]  # an even split is two owners
    assert [o["name"] for o in web["owners"]] == ["An", "Chưa gắn người"]  # mapped first on a tie
    assert not by_module["src"]["single_owner"]  # too few commits to flag
    assert body["totals"] == {"modules": 5, "single_owner": 2, "orphaned": 1, "shown": 5}
    assert body["orphan_after_days"] == 60
    assert body["modules"][0]["single_owner"]  # flagged modules first
    assert body["confidence"]["level"] == "ok"


def test_owner_names_follow_work_visibility(
    client: TestClient, world: World, seeded: None, as_user: Any
) -> None:
    lead = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.lead))
    core = next(m for m in lead["modules"] if m["module"] == "src/core")
    assert core["owners"][0]["name"] == "An"
    outsider = get(client, f"/api/v1/insights/knowledge?end={END}", as_user(world.outsider))
    core = next(m for m in outsider["modules"] if m["module"] == "src/core")
    assert core["owners"][0] == {
        "user_id": None,
        "name": "Một thành viên",
        "hidden": True,
        "mapped": None,
        "share": 91.3,
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
    client: TestClient, db_session: Session, world: World, seeded: None, as_user: Any
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
