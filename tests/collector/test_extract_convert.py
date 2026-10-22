from datetime import UTC, datetime
from typing import Any

import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.collector.gitlab import collect as ep
from app.collector.gitlab.client import GitLabClient
from app.collector.gitlab.collect import GitLabCollector
from app.collector.gitlab.convert import Converter, raw_rows_query
from app.collector.gitlab.diffstat import file_stat
from app.collector.gitlab.extract import extract, first_review_at, review_rounds
from app.models import Account, Commit, MergeRequest, MrCommit, MrNote, Pipeline, RawGitlab
from tests.collector.fake_gitlab import BASE, fixture, mount
from tests.collector.test_collect import make_project

IGNORE = ["*.lock", "dist/**", "vendor/**", "*.min.js", "**/generated/**"]
A, B, C, D = "a" * 40, "b" * 40, "c" * 40, "d" * 40


def raw(endpoint: str, payload: Any, **params: Any) -> RawGitlab:
    return RawGitlab(endpoint=endpoint, payload=payload, params=params or None)


def fixture_rows() -> list[RawGitlab]:
    rows = [raw(ep.EP_PROJECT, fixture("project.json"))]
    rows += [raw(ep.EP_COMMIT, c, sha=c["id"]) for c in fixture("commits.json")]
    for sha in (A, B, D):
        diff = [file_stat(e) for e in fixture(f"commit_diff_{sha}.json")]
        rows.append(raw(ep.EP_COMMIT_DIFF, diff, sha=sha))
    for mr in fixture("merge_requests.json"):
        iid = mr["iid"]
        rows.append(raw(ep.EP_MR, mr, iid=iid))
        rows.append(raw(ep.EP_MR_COMMITS, fixture(f"mr_{iid}_commits.json"), iid=iid))
        rows.append(raw(ep.EP_MR_NOTES, fixture(f"mr_{iid}_notes.json"), iid=iid))
        diffs = [file_stat(e) for e in fixture(f"mr_{iid}_diffs.json")]
        rows.append(raw(ep.EP_MR_DIFFS, diffs, iid=iid))
    return rows


def test_extract_reads_every_entity() -> None:
    data = extract(fixture_rows())
    assert data.default_branch == "main"
    assert set(data.commits) == {A, B, C, D}
    assert data.commits[C].is_merge and not data.commits[A].is_merge
    assert data.commits[B].author_email == "binh.personal@gmail.com"  # lower-cased
    assert set(data.merge_requests) == {1, 2}


def test_first_review_ignores_system_and_author_notes() -> None:
    mr = extract(fixture_rows()).merge_requests[1]
    assert first_review_at(mr) == datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
    assert first_review_at(extract(fixture_rows()).merge_requests[2]) is None


def test_review_rounds_counts_pushes_after_review() -> None:
    data = extract(fixture_rows())
    # push 08:05 (no review yet) -> 0; review 09:00, push 10:00 -> 1; reviews 11:00/11:05,
    # push 12:00 -> 2; approval is a system note, not a review.
    assert review_rounds(data.merge_requests[1]) == 2
    assert review_rounds(data.merge_requests[2]) == 0


def test_later_raw_row_wins() -> None:
    old = fixture("merge_requests.json")[1]
    new = {**old, "state": "merged"}
    data = extract([raw(ep.EP_MR, old, iid=2), raw(ep.EP_MR, new, iid=2)])
    assert data.merge_requests[2].state == "merged"


def count(session: Session, model: Any) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.integration
def test_convert_is_idempotent_and_filters_ignored_paths(db_session: Session) -> None:
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router, GitLabClient(BASE, "t") as client:
        mount(router)
        GitLabCollector(client).collect(db_session, project, None)

    for _ in range(2):
        rows = db_session.scalars(raw_rows_query(project)).all()
        Converter(db_session, project, IGNORE).run(extract(rows))

    assert count(db_session, Commit) == 4
    assert count(db_session, MergeRequest) == 2
    assert count(db_session, MrCommit) == 3
    assert count(db_session, MrNote) == 8
    assert count(db_session, Pipeline) == 2

    vendored = db_session.get(Commit, B)
    assert vendored is not None
    assert (vendored.additions, vendored.files_changed) == (20, 1)  # vendor/chart.min.js dropped
    merge = db_session.get(Commit, C)
    assert merge is not None and merge.is_merge

    mr = db_session.scalars(select(MergeRequest).where(MergeRequest.iid == 1)).one()
    assert (mr.additions, mr.deletions) == (28, 3)  # yarn.lock dropped
    assert mr.review_rounds == 2
    assert mr.first_review_at == datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
    assert project.default_branch == "main"

    emails = db_session.scalars(
        select(Account.external_key).where(Account.kind == "git_email")
    ).all()
    assert sorted(emails) == [
        "12-binh.tran@users.noreply.gitlab.com",
        "an.nguyen@company.vn",
        "binh.personal@gmail.com",
    ]
    gitlab_users = db_session.scalars(
        select(Account.display_name).where(Account.kind == "gitlab_user")
    ).all()
    assert sorted(n or "" for n in gitlab_users) == ["an.nguyen", "binh.tran"]
