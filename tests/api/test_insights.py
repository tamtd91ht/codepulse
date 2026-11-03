"""docs/specs/20 §8-9: /insights/flow, /insights/flow/mrs, /insights/review-load."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Account,
    BusinessProject,
    Commit,
    MergeRequest,
    MrCommit,
    MrNote,
    Pipeline,
    Project,
    User,
)
from tests.api.conftest import World

pytestmark = pytest.mark.integration

WEEK = "2026-10-05"
MON = datetime(2026, 10, 5, 2, tzinfo=UTC)  # 09:00 in Asia/Ho_Chi_Minh


def at(hours: float) -> datetime:
    return MON + timedelta(hours=hours)


class Seeder:
    def __init__(self, session: Session, project: Project) -> None:
        self.s = session
        self.project = project
        self.accounts: dict[int, Account] = {}
        self.next_iid = 100
        self.next_note = 1

    def account(self, user: User | None, key: str) -> Account:
        acc = Account(
            kind="gitlab_user",
            external_key=key,
            display_name=key,
            user_id=user.id if user else None,
        )
        self.s.add(acc)
        self.s.flush()
        return acc

    def mr(
        self,
        author: Account,
        *,
        opened: float,
        merged: float | None,
        first_commit: float | None = None,
        size: int = 100,
        title: str = "Change",
    ) -> MergeRequest:
        self.next_iid += 1
        mr = MergeRequest(
            project_id=self.project.id,
            iid=self.next_iid,
            title=title,
            author_account_id=author.id,
            state="merged" if merged is not None else "opened",
            target_branch="main",
            created_at_src=at(opened),
            merged_at=at(merged) if merged is not None else None,
            additions=size,
            deletions=0,
            review_rounds=1,
        )
        self.s.add(mr)
        self.s.flush()
        if first_commit is not None:
            sha = f"{self.next_iid:040d}"
            self.s.add(Commit(sha=sha, project_id=self.project.id, authored_at=at(first_commit)))
            self.s.flush()
            self.s.add(MrCommit(mr_id=mr.id, sha=sha))
        return mr

    def note(
        self,
        mr: MergeRequest,
        who: Account,
        hours: float,
        body: str = "LGTM",
        *,
        system: bool = False,
    ) -> None:
        self.next_note += 1
        self.s.add(
            MrNote(
                id=900_000 + self.next_note,
                mr_id=mr.id,
                author_account_id=who.id,
                body=body,
                is_system=system,
                created_at_src=at(hours),
            )
        )


@pytest.fixture
def seeded(db_session: Session, world: World, settings: Settings) -> dict[str, Any]:
    settings.insights_min_sample = 1
    world.project.default_branch = "main"
    world.project.last_synced_at = datetime.now(UTC)
    sd = Seeder(db_session, world.project)
    an, binh, lead = (
        sd.account(world.an, "an"),
        sd.account(world.binh, "binh"),
        sd.account(world.lead, "lead"),
    )
    ghost = sd.account(None, "ghost")  # a reviewer not mapped to anyone
    # This week: an's MR reviewed by binh after 6 h; binh's MR approved (no comment) by lead
    # after 2 h, ready at +1 h; an's MR merged without review; an XL MR by an reviewed by ghost.
    m1 = sd.mr(an, opened=0, merged=30, first_commit=-10)
    sd.note(m1, an, 1, "pls review")
    sd.note(m1, binh, 6)
    m2 = sd.mr(binh, opened=0, merged=5, size=40)
    sd.note(m2, binh, 1, "marked this merge request as **ready**", system=True)
    sd.note(m2, lead, 3, "approved this merge request", system=True)
    m3 = sd.mr(an, opened=10, merged=12, title="Hotfix")
    # CodePulse's own review summary on the hotfix is nobody's review (§4.2).
    bot = sd.account(None, "codepulse-bot")
    sd.note(m3, bot, 11, "<!-- codepulse-review -->\n## CodePulse review")
    m4 = sd.mr(an, opened=20, merged=60, size=1500)
    sd.note(m4, ghost, 40, "nit")
    # Last week: one MR, merged after 10 h.
    sd.mr(binh, opened=-100, merged=-90)
    # Open: binh's MR nobody answered, a draft, and one already answered.
    sd.mr(binh, opened=-48, merged=None, title="Waiting")
    sd.mr(binh, opened=-48, merged=None, title="Draft: later")
    answered = sd.mr(an, opened=-24, merged=None, title="Answered")
    sd.note(answered, binh, -20)
    for i, (status, ref) in enumerate(
        [("success", "main"), ("failed", "main"), ("success", "feature"), ("canceled", "main")]
    ):
        db_session.add(
            Pipeline(
                id=70_000 + i,
                project_id=world.project.id,
                ref=ref,
                status=status,
                duration_sec=600,
                created_at_src=at(i),
            )
        )
    db_session.flush()
    return {"m1": m1, "m2": m2, "m3": m3, "m4": m4}


def get(client: TestClient, path: str, headers: dict[str, str]) -> Any:
    res = client.get(path, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_flow_totals_stages_and_deltas(
    client: TestClient, world: World, seeded: dict[str, Any], as_user: Any
) -> None:
    body = get(client, f"/api/v1/insights/flow?period=week&start={WEEK}", as_user(world.admin))
    assert body["insights_version"] == "1"
    t = body["totals"]
    assert t["merged"] == 4 and t["merged_without_review"] == 1
    assert t["merged_without_review_rate"] == 25.0
    # pickup: m1 6 h (from open), m2 2 h (from ready), m4 20 h -> median 6
    assert t["stages"]["pickup"] == {"n": 3, "median": 6.0, "p75": 13.0}
    assert t["stages"]["coding"]["n"] == 1 and t["stages"]["coding"]["median"] == 10.0
    assert t["size"]["buckets"]["XL"] == 1 and t["size"]["buckets"]["XS"] == 1
    assert t["delta"]["merged"] == 3
    # time to merge: 30, 5, 2, 40 -> median 17.5; last week 10
    assert t["stages"]["time_to_merge"]["median"] == 17.5
    assert t["delta"]["time_to_merge"] == 7.5
    assert body["trend"][-1]["merged"] == 4 and body["trend"][-2]["merged"] == 1
    assert body["confidence"]["level"] == "ok"
    assert body["data_as_of"] is not None
    repo = body["repos"][0]
    assert repo["merged"] == 4 and repo["pipelines"]["success_rate"] == 66.7
    assert body["pipelines"]["default_branch"]["success_rate"] == 50.0


def test_open_mrs_waiting_and_author_visibility(
    client: TestClient, world: World, seeded: dict[str, Any], as_user: Any
) -> None:
    body = get(client, f"/api/v1/insights/flow?period=week&start={WEEK}", as_user(world.lead))
    assert body["open"]["opened"] == 2  # the draft is left out
    assert body["open"]["waiting_first_response"] == 1
    oldest = body["open"]["oldest"][0]
    assert oldest["title"] == "Waiting" and oldest["author"]["name"] == "Binh"
    assert oldest["web_url"].endswith(f"/demo/p/-/merge_requests/{oldest['iid']}")
    assert oldest["waiting_h"] > 48
    # Outsider sees the dự án (view) but does not lead binh: the number, not the name.
    body = get(client, f"/api/v1/insights/flow?period=week&start={WEEK}", as_user(world.outsider))
    assert body["open"]["oldest"][0]["author"] == {
        "user_id": None,
        "name": "Một thành viên",
        "hidden": True,
        "mapped": None,
    }
    assert body["totals"]["merged"] == 4  # aggregates are the same for everyone


def test_drill_down_sorted_by_stage_and_filtered(
    client: TestClient, world: World, seeded: dict[str, Any], as_user: Any
) -> None:
    body = get(
        client,
        f"/api/v1/insights/flow/mrs?period=week&start={WEEK}&stage=pickup",
        as_user(world.admin),
    )
    assert body["total"] == 4
    pickups = [item["pickup"] for item in body["items"]]
    assert pickups == [20.0, 6.0, 2.0, None]
    assert body["items"][0]["size_bucket"] == "XL"
    assert body["items"][-1]["reviewed"] is False
    only = get(
        client,
        f"/api/v1/insights/flow/mrs?period=week&start={WEEK}&only=unreviewed",
        as_user(world.admin),
    )
    assert [i["title"] for i in only["items"]] == ["Hotfix"]
    xl = get(
        client, f"/api/v1/insights/flow/mrs?period=week&start={WEEK}&only=xl", as_user(world.an)
    )
    assert xl["total"] == 1 and xl["items"][0]["author"]["name"] == "An"  # their own MR


def test_review_load_names_follow_work_visibility(
    client: TestClient, world: World, seeded: dict[str, Any], as_user: Any
) -> None:
    path = f"/api/v1/insights/review-load?period=week&start={WEEK}"
    body = get(client, path, as_user(world.admin))
    assert body["merged"] == 4 and body["reviewed"] == 3 and body["reviewers_count"] == 3
    assert body["top_reviewer_share"] == 25.0 and body["concentrated"] is False
    names = sorted(r["name"] for r in body["reviewers"])
    assert names == ["Binh", "Chưa gắn người", "Lead"]  # the bot's comment is not a review
    ghost = next(r for r in body["reviewers"] if r["name"] == "Chưa gắn người")
    assert ghost["mapped"] is False and ghost["reviewed_mrs"] == 1
    # The lead sees their members, not the unmapped account nor themselves hidden.
    lead_view = get(client, path, as_user(world.lead))
    shown = {r["name"] for r in lead_view["reviewers"]}
    assert shown == {"Binh", "Lead", "Một thành viên"}
    # A developer sees only themselves by name.
    an_view = get(client, path, as_user(world.an))
    assert {r["name"] for r in an_view["reviewers"]} == {"Một thành viên"}
    pair = next(p for p in an_view["pairs"] if p["author"]["name"] == "An")
    assert pair["reviewer"]["hidden"] is True


def test_scope_rules(
    client: TestClient, db_session: Session, world: World, seeded: dict[str, Any], as_user: Any
) -> None:
    secret = BusinessProject(name="Secret")
    db_session.add(secret)
    db_session.flush()
    loose = Project(
        connection_id=world.project.connection_id, external_id=4242, path_with_namespace="x/loose"
    )
    db_session.add(loose)
    db_session.flush()
    h = as_user(world.outsider)
    assert (
        client.get(f"/api/v1/insights/flow?business_project_id={secret.id}", headers=h).status_code
        == 404
    )
    assert client.get(f"/api/v1/insights/flow?project_id={loose.id}", headers=h).status_code == 404
    assert (
        client.get(
            f"/api/v1/insights/flow?project_id={world.project.id}&business_project_id={secret.id}",
            headers=as_user(world.admin),
        ).status_code
        == 404
    )
    # An admin sees the unassigned repo; the outsider's "all" scope leaves it out.
    admin_all = get(client, f"/api/v1/insights/flow?start={WEEK}", as_user(world.admin))
    assert {r["path_with_namespace"] for r in admin_all["repos"]} == {"demo/p", "x/loose"}
    mine = get(client, f"/api/v1/insights/flow?start={WEEK}", h)
    assert {r["path_with_namespace"] for r in mine["repos"]} == {"demo/p"}
    assert client.get("/api/v1/insights/flow").status_code == 401


def test_small_sample_and_stale_sync(
    client: TestClient, world: World, seeded: dict[str, Any], settings: Settings, as_user: Any
) -> None:
    settings.insights_min_sample = 5
    world.project.last_synced_at = datetime.now(UTC) - timedelta(days=5)
    body = get(client, f"/api/v1/insights/flow?period=week&start={WEEK}", as_user(world.admin))
    assert body["confidence"]["level"] == "insufficient"
    assert {r["code"] for r in body["confidence"]["reasons"]} == {"small_sample", "stale_sync"}
    assert body["totals"]["stages"]["pickup"]["median"] is None


def test_month_boundary_follows_report_tz(
    client: TestClient, db_session: Session, world: World, settings: Settings, as_user: Any
) -> None:
    settings.insights_min_sample = 1
    sd = Seeder(db_session, world.project)
    an = sd.account(world.an, "an")
    late_october = sd.mr(an, opened=0, merged=0)  # 23:30 VN on 31 Oct
    late_october.created_at_src = datetime(2026, 10, 31, 15, tzinfo=UTC)
    late_october.merged_at = datetime(2026, 10, 31, 16, 30, tzinfo=UTC)
    november = sd.mr(an, opened=0, merged=0)  # 00:00 VN on 1 Nov
    november.created_at_src = datetime(2026, 10, 31, 15, tzinfo=UTC)
    november.merged_at = datetime(2026, 10, 31, 17, tzinfo=UTC)
    db_session.flush()
    h = as_user(world.admin)
    october = get(client, "/api/v1/insights/flow?period=month&start=2026-10-01", h)
    assert october["totals"]["merged"] == 1
    nov = get(client, "/api/v1/insights/flow?period=month&start=2026-11-01", h)
    assert nov["totals"]["merged"] == 1 and nov["totals"]["delta"]["merged"] == 0


def test_range_is_capped_and_drill_down_carries_confidence(
    client: TestClient, world: World, seeded: dict[str, Any], as_user: Any
) -> None:
    h = as_user(world.admin)
    too_long = client.get(
        "/api/v1/insights/flow?period=range&start=2026-04-01&end=2027-04-02", headers=h
    )
    assert too_long.status_code == 422
    ok = get(client, "/api/v1/insights/flow/mrs?period=range&start=2026-10-01&end=2026-10-31", h)
    assert ok["total"] == 5 and ok["confidence"]["level"] == "ok" and ok["data_as_of"]
