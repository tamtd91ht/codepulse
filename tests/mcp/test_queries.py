"""What the MCP read tools return (docs/specs/26 §3-6): the filter "still to fix", the top
order, whose findings, a commit's / an MR's review, the repeat count."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.mcp import queries
from app.mcp.queries import McpError
from app.models import BusinessProject, Commit, FindingClosure, Project, Review, ReviewFinding
from app.review.rubric import RUBRIC_VERSION
from tests.mcp.conftest import Env, caller_for

FINISHED = datetime(2026, 10, 6, 9, tzinfo=UTC)


def add_finding(session: Session, review: Review, **values: Any) -> ReviewFinding:
    row = ReviewFinding(
        review_id=review.id,
        severity=values.pop("severity", "high"),
        category=values.pop("category", "bug"),
        file_path=values.pop("file_path", "app/b.py"),
        line_start=values.pop("line_start", 10),
        title=values.pop("title", "A bug"),
        **values,
    )
    session.add(row)
    session.flush()
    return row


def commit_review(
    session: Session, project: Project, sha: str, author_id: int, **values: Any
) -> Review:
    if session.get(Commit, sha) is None:
        session.add(Commit(sha=sha, project_id=project.id))
        session.flush()
    review = Review(
        target_type="commit",
        commit_sha=sha,
        reviewed_sha=sha,
        status=values.pop("status", "done"),
        author_user_id=author_id,
        finished_at=values.pop("finished_at", FINISHED),
        rubric_version=RUBRIC_VERSION,
        **values,
    )
    session.add(review)
    session.flush()
    return review


def ids(views: list[queries.FindingView]) -> list[int]:
    return [v.finding.id for v in views]


def test_default_is_own_open_critical_high_sorted(env: Env, world: Any) -> None:
    s = env.session
    r = world.an_review
    confirmed = add_finding(
        s, r, severity="critical", verified="confirmed", confidence=Decimal("0.5")
    )
    high = add_finding(s, r, severity="high", confidence=Decimal("0.99"))
    high_low = add_finding(s, r, severity="high", confidence=Decimal("0.3"))
    add_finding(s, r, severity="medium")  # not "to fix" here
    add_finding(s, r, severity="high", verified="rejected")
    add_finding(s, r, severity="high", status="fixed")
    disputed = add_finding(s, r, severity="high", status="disputed")
    pending = add_finding(s, r, severity="critical")
    s.add(
        FindingClosure(
            finding_id=pending.id, kind="request", status="pending", requested_by=world.an.id
        )
    )
    superseded = Review(
        target_type="mr",
        mr_id=world.mr.id,
        reviewed_sha="9" * 40,
        status="superseded",
        author_user_id=world.an.id,
        rubric_version=RUBRIC_VERSION,
    )
    s.add(superseded)
    s.flush()
    add_finding(s, superseded, severity="critical")
    # someone else's
    other = commit_review(s, world.project, "e" * 40, world.binh.id)
    add_finding(s, other, severity="critical")
    s.commit()
    caller = caller_for(s, env.settings, world.an)

    views, owner = queries.list_findings(s, caller.principal)
    assert owner == world.an.id
    # critical (confirmed first, then by confidence) before high (by confidence)
    assert ids(views) == [confirmed.id, world.an_finding.id, high.id, high_low.id]

    views, _ = queries.list_findings(s, caller.principal, include_disputed=True)
    assert disputed.id in ids(views)
    views, _ = queries.list_findings(s, caller.principal, include_pending=True)
    assert pending.id in ids(views)
    shown = next(v for v in views if v.finding.id == pending.id)
    assert shown.pending is not None and shown.pending.status == "pending"

    views, _ = queries.list_findings(s, caller.principal, severity=["high"], limit=1)
    assert ids(views) == [high.id]


def test_severity_is_never_widened(env: Env, world: Any) -> None:
    caller = caller_for(env.session, env.settings, world.an)
    with pytest.raises(McpError) as err:
        queries.list_findings(env.session, caller.principal, severity=["medium"])
    assert err.value.code == "invalid_argument"


def test_repo_and_since_filters(env: Env, world: Any) -> None:
    s = env.session
    caller = caller_for(s, env.settings, world.an)
    views, _ = queries.list_findings(s, caller.principal, repo="demo/p")
    assert ids(views) == [world.an_finding.id]
    views, _ = queries.list_findings(s, caller.principal, repo=str(world.project.id))
    assert ids(views) == [world.an_finding.id]
    views, _ = queries.list_findings(s, caller.principal, since="2026-12-01")
    assert views == []
    with pytest.raises(McpError) as err:
        queries.list_findings(s, caller.principal, repo="nope/x")
    assert err.value.code == "not_found"
    with pytest.raises(McpError):
        queries.list_findings(s, caller.principal, since="yesterday")


def test_user_scope(env: Env, world: Any) -> None:
    s = env.session
    other = commit_review(s, world.project, "e" * 40, world.binh.id)
    binh_finding = add_finding(s, other, severity="critical")
    s.commit()
    # A member's review.view is "own": nobody else.
    an = caller_for(s, env.settings, world.an)
    with pytest.raises(McpError) as err:
        queries.list_findings(s, an.principal, user="binh@x.vn")
    assert err.value.code == "forbidden"
    # The lead of Backend sees the team: by email or id.
    lead = caller_for(s, env.settings, world.lead)
    views, owner = queries.list_findings(s, lead.principal, user="BINH@x.vn")
    assert owner == world.binh.id and ids(views) == [binh_finding.id]
    views, _ = queries.list_findings(s, lead.principal, user=str(world.an.id))
    assert ids(views) == [world.an_finding.id]
    # Not in anyone's reach: the outsider.
    outsider = caller_for(s, env.settings, world.outsider)
    with pytest.raises(McpError):
        queries.list_findings(s, outsider.principal, user="an@x.vn")
    with pytest.raises(McpError) as err:
        queries.list_findings(s, lead.principal, user="ghost@x.vn")
    assert err.value.code == "not_found"


def test_commit_findings_latest_round_short_sha_and_link(env: Env, world: Any) -> None:
    s = env.session
    sha = "d" * 40
    first = commit_review(s, world.project, sha, world.an.id, round=1, status="superseded")
    add_finding(s, first, severity="critical", title="old")
    latest = commit_review(s, world.project, sha, world.an.id, round=2)
    now = add_finding(s, latest, severity="high", title="new")
    add_finding(s, latest, severity="low")
    s.commit()
    caller = caller_for(s, env.settings, world.an)
    review, views = queries.commit_findings(s, caller.principal, sha[:7])
    assert review.id == latest.id and ids(views) == [now.id]
    link = f"https://gitlab.example.com/demo/p/-/commit/{sha}"
    review, _ = queries.commit_findings(s, caller.principal, link)
    assert review.id == latest.id
    with pytest.raises(McpError) as err:
        queries.commit_findings(s, caller.principal, "zz")
    assert err.value.code == "invalid_argument"
    with pytest.raises(McpError) as err:
        queries.commit_findings(s, caller.principal, "f" * 40)
    assert err.value.code == "not_found"


def test_short_sha_in_two_repos_needs_repo(env: Env, world: Any) -> None:
    s = env.session
    twin = Project(
        connection_id=world.project.connection_id,
        external_id=1002,
        path_with_namespace="demo/q",
        business_project_id=world.project.business_project_id,
    )
    s.add(twin)
    s.flush()
    commit_review(s, world.project, "1234567" + "a" * 33, world.an.id)
    in_q = commit_review(s, twin, "1234567" + "b" * 33, world.an.id)
    s.commit()
    caller = caller_for(s, env.settings, world.an)
    with pytest.raises(McpError) as err:
        queries.commit_findings(s, caller.principal, "1234567")
    assert err.value.code == "ambiguous_sha" and "demo/q" in err.value.message
    review, _ = queries.commit_findings(s, caller.principal, "1234567", repo="demo/q")
    assert review.id == in_q.id


def test_mr_findings_and_visibility(env: Env, world: Any) -> None:
    s = env.session
    caller = caller_for(s, env.settings, world.an)
    review, views = queries.mr_findings(s, caller.principal, "demo/p", 7)
    assert review.id == world.an_review.id and ids(views) == [world.an_finding.id]
    with pytest.raises(McpError) as err:
        queries.mr_findings(s, caller.principal, "demo/p", 99)
    assert err.value.code == "not_found"
    # Somebody whose review.view does not reach an's work.
    outsider = caller_for(s, env.settings, world.outsider)
    with pytest.raises(McpError) as err:
        queries.mr_findings(s, outsider.principal, "demo/p", 7)
    assert err.value.code == "forbidden"
    with pytest.raises(McpError) as err:
        queries.finding_view(s, outsider.principal, world.an_finding.id)
    assert err.value.code == "forbidden"


def test_repeat_counts_by_change_over_90_days(env: Env, world: Any) -> None:
    s = env.session
    world.an_finding.fingerprint = "fp1"
    for n, sha in enumerate(("1" * 40, "2" * 40)):
        review = commit_review(s, world.project, sha, world.an.id)
        add_finding(s, review, fingerprint="fp1", title=f"copy {n}")
        add_finding(s, review, fingerprint="fp1", title=f"copy again {n}")  # same change
    s.commit()
    assert queries.repeat_counts(s, ["fp1", None]) == {"fp1": 3}
    caller = caller_for(s, env.settings, world.an)
    view = queries.finding_view(s, caller.principal, world.an_finding.id)
    assert view.repeats == 3


def test_a_sha_only_in_unseen_repos_does_not_exist(env: Env, world: Any) -> None:
    s = env.session
    secret = BusinessProject(name="Secret")
    s.add(secret)
    s.flush()
    hidden = Project(
        connection_id=world.project.connection_id,
        external_id=2001,
        path_with_namespace="secret/x",
        business_project_id=secret.id,
    )
    s.add(hidden)
    s.flush()
    seen = commit_review(s, world.project, "7654321" + "a" * 33, world.an.id)
    commit_review(s, hidden, "7654321" + "b" * 33, world.binh.id)
    commit_review(s, hidden, "abcdef1" + "b" * 33, world.binh.id)
    s.commit()
    an = caller_for(s, env.settings, world.an)
    # The repo an cannot see does not make the short SHA ambiguous, nor leak its path.
    review, _ = queries.commit_findings(s, an.principal, "7654321")
    assert review.id == seen.id
    with pytest.raises(McpError) as err:
        queries.commit_findings(s, an.principal, "abcdef1")
    assert err.value.code == "not_found" and "secret/x" not in err.value.message


def test_own_only_reach_is_forbidden_for_anyone_existing_or_not(env: Env, world: Any) -> None:
    s = env.session
    an = caller_for(s, env.settings, world.an)
    for who in ("binh@x.vn", "ghost@x.vn", str(world.binh.id), "987654"):
        with pytest.raises(McpError) as err:
            queries.list_findings(s, an.principal, user=who)
        assert err.value.code == "forbidden", who
    _views, owner = queries.list_findings(s, an.principal, user="AN@x.vn")
    assert owner == world.an.id


def test_a_number_that_is_two_repos_is_ambiguous(env: Env, world: Any) -> None:
    s = env.session
    twin = Project(
        connection_id=world.project.connection_id,
        external_id=world.project.id,  # GitLab's id of this repo = CodePulse's id of demo/p
        path_with_namespace="demo/twin",
        business_project_id=world.project.business_project_id,
    )
    s.add(twin)
    s.commit()
    an = caller_for(s, env.settings, world.an)
    with pytest.raises(McpError) as err:
        queries.resolve_repo(s, an.principal, str(world.project.id))
    assert err.value.code == "ambiguous_repo"
    assert "demo/p" in err.value.message and "demo/twin" in err.value.message
    assert queries.resolve_repo(s, an.principal, "1001").id == world.project.id
    assert queries.resolve_repo(s, an.principal, "demo/twin").id == twin.id
