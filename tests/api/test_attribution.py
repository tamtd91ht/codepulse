"""Reviews follow their author's account when it gets a person (app/identity/attribution.py)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.identity.attribution import attribute_reviews
from app.identity.autolink import autolink
from app.models import Account, Commit, MergeRequest, Review
from app.review.rubric import RUBRIC_VERSION
from tests.api.conftest import FakeQueue, World

pytestmark = pytest.mark.integration

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
FINISHED = datetime(2026, 10, 6, 9, tzinfo=UTC)  # Tuesday 06/10: week of 05/10, October


def unmapped_work(session: Session, world: World) -> tuple[Account, Account, Review, Review]:
    """An outside email with a reviewed commit, an unlinked GitLab user with a reviewed MR."""
    email = Account(kind="git_email", external_key="an.home@gmail.com")
    gitlab = Account(kind="gitlab_user", external_key="4242", display_name="an.home")
    session.add_all([email, gitlab])
    session.flush()
    commit = Commit(sha="c" * 40, project_id=world.project.id, author_account_id=email.id)
    mr = MergeRequest(project_id=world.project.id, iid=31, head_sha="d" * 40)
    mr.author_account_id = gitlab.id
    session.add_all([commit, mr])
    session.flush()
    reviews = [
        Review(
            target_type="commit",
            commit_sha=commit.sha,
            reviewed_sha=commit.sha,
            status="done",
            score=Decimal(80),
            finished_at=FINISHED,
            rubric_version=RUBRIC_VERSION,
        ),
        Review(
            target_type="mr",
            mr_id=mr.id,
            reviewed_sha=mr.head_sha,
            status="done",
            score=Decimal(60),
            finished_at=FINISHED,
            rubric_version=RUBRIC_VERSION,
        ),
    ]
    session.add_all(reviews)
    session.flush()
    return email, gitlab, reviews[0], reviews[1]


def test_linking_an_account_gives_its_earlier_reviews_the_person_and_queues_a_rebuild(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    queue: FakeQueue,
) -> None:
    email, gitlab, commit_review, mr_review = unmapped_work(db_session, world)
    admin = as_user(world.admin)
    # The option off: the link alone, earlier reviews keep no person.
    res = client.put(
        f"/api/v1/accounts/{gitlab.id}/user",
        json={"user_id": world.an.id, "backfill": False},
        headers=admin,
    )
    assert res.status_code == 200, res.text
    assert res.json()["reviews_attributed"] == 0 and queue.jobs == []
    db_session.refresh(mr_review)
    assert mr_review.author_user_id is None
    # The option on (the default): the MR's review is An's, and its week and month are rebuilt.
    res = client.put(
        f"/api/v1/accounts/{gitlab.id}/user", json={"user_id": world.an.id}, headers=admin
    )
    body = res.json()
    assert (body["user_id"], body["reviews_attributed"], body["periods_queued"]) == (
        world.an.id,
        1,
        2,
    )
    db_session.refresh(mr_review)
    db_session.refresh(commit_review)
    assert mr_review.author_user_id == world.an.id and commit_review.author_user_id is None
    ((func, args, _kwargs),) = queue.jobs
    assert func == "app.scoring.aggregate.aggregate_periods_job"
    assert args == ([["month", "2026-10-01"], ["week", "2026-10-05"]],)
    # A corrected link moves the reviews along; the commit email from the commit list too.
    client.put(f"/api/v1/accounts/{gitlab.id}/user", json={"user_id": world.binh.id}, headers=admin)
    db_session.refresh(mr_review)
    assert mr_review.author_user_id == world.binh.id
    res = client.post(
        "/api/v1/identity/authors",
        json={"email": "an.home@gmail.com", "user_id": world.an.id},
        headers=admin,
    )
    assert res.status_code == 200, res.text
    assert res.json()["reviews_attributed"] == 1
    db_session.refresh(commit_review)
    assert commit_review.author_user_id == world.an.id
    assert email.id == res.json()["id"]


def test_the_sweep_takes_only_reviews_without_a_person_of_the_accounts_given(
    db_session: Session, world: World
) -> None:
    email, gitlab, commit_review, mr_review = unmapped_work(db_session, world)
    # Linked behind the API's back (an autolink during a sync).
    email.user_id = world.an.id
    gitlab.user_id = world.binh.id
    mr_review.author_user_id = world.lead.id  # already someone's: the sweep leaves it alone
    db_session.flush()
    # Only the accounts given are looked at (an admin's "no" to the option holds).
    assert attribute_reviews(db_session, TZ, [gitlab.id]).reviews == 0
    swept = attribute_reviews(db_session, TZ, [email.id, gitlab.id])
    assert swept.reviews == 1
    db_session.refresh(commit_review)
    db_session.refresh(mr_review)
    assert commit_review.author_user_id == world.an.id
    assert mr_review.author_user_id == world.lead.id
    assert attribute_reviews(db_session, TZ, [email.id]).reviews == 0  # nothing left: a no-op
    assert attribute_reviews(db_session, TZ, []).reviews == 0


def test_merged_mrs_and_only_finished_reviews_decide_the_periods(
    db_session: Session, world: World
) -> None:
    email, gitlab, commit_review, mr_review = unmapped_work(db_session, world)
    commit_review.status = "failed"  # in no stored metrics: no period of its own
    db_session.flush()
    email.user_id = world.an.id
    assert attribute_reviews(db_session, TZ, [email.id]).periods == set()
    # The MR merged in September although its review counts in October: both are rebuilt,
    # since the stored metrics count merged MRs through the account's current person.
    mr = db_session.get(MergeRequest, mr_review.mr_id)
    assert mr is not None
    mr.merged_at = datetime(2026, 9, 15, 3, tzinfo=UTC)
    gitlab.user_id = world.an.id
    db_session.flush()
    periods = attribute_reviews(db_session, TZ, [gitlab.id]).periods
    assert periods == {
        ("week", date(2026, 10, 5)),
        ("month", date(2026, 10, 1)),
        ("week", date(2026, 9, 14)),
        ("month", date(2026, 9, 1)),
    }


def test_unlinking_keeps_the_reviews_and_a_sync_follows_only_its_own_links(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    _, gitlab, _, mr_review = unmapped_work(db_session, world)
    admin = as_user(world.admin)
    client.put(f"/api/v1/accounts/{gitlab.id}/user", json={"user_id": world.an.id}, headers=admin)
    res = client.put(f"/api/v1/accounts/{gitlab.id}/user", json={"user_id": None}, headers=admin)
    assert res.status_code == 200 and res.json()["reviews_attributed"] == 0
    db_session.refresh(mr_review)
    assert mr_review.author_user_id == world.an.id
    # A sync's autolink reports the accounts it tied: those, and only those, are swept.
    stray = Account(kind="git_email", external_key="an@x.vn")
    db_session.add(stray)
    db_session.flush()
    assert stray.id in autolink(db_session, ["x.vn"]).account_ids
