"""DPI with Jira as the task source (docs/specs/27 §6, §9, §11, §12): only the chosen source is
read, sub-tasks without points are left out, R4 / defects / readiness follow the Jira projects'
dự án, and a Jira issue is named by its key."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.dpi.defects import suggest
from app.dpi.facts import Loader
from app.dpi.metrics import a1
from app.dpi.readiness import repo_readiness
from app.jira.settings import JIRA_ID_OFFSET, TASK_SOURCE_KEY
from app.models import (
    Account,
    AppSetting,
    BusinessProject,
    CommitFile,
    Issue,
    IssueMrLink,
    JiraProject,
    MergeRequest,
    MrCommit,
    PermissionSet,
    PermissionSetItem,
    ProdDefect,
    UserPermissionSet,
)
from tests.api.conftest import World

pytestmark = pytest.mark.integration
API = "/api/v1/dpi"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
CLOSED = datetime(2026, 10, 20, tzinfo=UTC)


def use_jira(session: Session) -> None:
    session.add(AppSetting(key=TASK_SOURCE_KEY, value="jira"))
    session.flush()


def jira_project(session: Session, key: str, bp_id: int | None) -> JiraProject:
    project = JiraProject(key=key, name=f"Team {key}", business_project_id=bp_id)
    session.add(project)
    session.flush()
    return project


def jira_issue(session: Session, project: JiraProject, number: int, **fields: Any) -> Issue:
    issue = Issue(
        id=JIRA_ID_OFFSET + project.id * 1000 + number,
        source="jira",
        jira_project_id=project.id,
        iid=number,
        key=f"{project.key}-{number}",
        title=f"{project.key} việc {number}",
        **fields,
    )
    session.add(issue)
    session.flush()
    return issue


@pytest.fixture
def accounts(world: World, db_session: Session) -> tuple[Account, Account]:
    """An's GitLab and Jira accounts."""
    gitlab = Account(kind="gitlab_user", external_key="77", user_id=world.an.id)
    jira = Account(kind="jira_user", external_key="JIRAUSER77", user_id=world.an.id)
    db_session.add_all([gitlab, jira])
    db_session.flush()
    return gitlab, jira


def closed(account: Account, **fields: Any) -> dict[str, Any]:
    return {
        "state": "closed",
        "closed_at_src": CLOSED,
        "assignee_account_ids": [account.id],
        "type": "feature",
        **fields,
    }


def test_facts_read_only_the_chosen_source_and_drop_pointless_subtasks(
    world: World, db_session: Session, accounts: tuple[Account, Account]
) -> None:
    gitlab, jira = accounts
    db_session.add(
        Issue(id=601, project_id=world.project.id, iid=1, **closed(gitlab, complexity_points=5))
    )
    abc = jira_project(db_session, "ABC", world.project.business_project_id)
    jira_issue(db_session, abc, 1, **closed(jira, complexity_points=3, story_points=Decimal(3)))
    jira_issue(db_session, abc, 2, parent_key="ABC-1", **closed(jira))  # points on the story
    jira_issue(db_session, abc, 3, parent_key="ABC-1", **closed(jira, complexity_points=2))
    month = date(2026, 10, 1)

    f = Loader(db_session, month, TZ, source="jira").facts(world.an.id, 10)
    assert sorted(i.key or "" for i in f.issues_closed) == ["ABC-1", "ABC-3"]
    assert f.issues_closed_all == 2 and all(i.project_id is None for i in f.issues_closed)
    result = a1(f)
    assert result.detail["points"] == 5
    assert sorted(e["ref"] for e in result.evidence) == ["ABC-1", "ABC-3"]

    f = Loader(db_session, month, TZ, source="gitlab").facts(world.an.id, 10)
    assert [i.id for i in f.issues_closed] == [601]
    assert [e["ref"] for e in a1(f).evidence] == ["#1"]


def test_readiness_has_a_jira_block_when_jira_is_the_source(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    accounts: tuple[Account, Account],
) -> None:
    _gitlab, jira = accounts
    stranger = Account(kind="jira_user", external_key="JIRAUSER99")  # not linked to anyone
    db_session.add(stranger)
    db_session.flush()
    bp = world.project.business_project_id
    abc = jira_project(db_session, "ABC", bp)
    abc.last_error = "401"
    lone = jira_project(db_session, "LONE", None)
    recent = datetime.now(UTC) - timedelta(days=3)
    jira_issue(
        db_session,
        abc,
        1,
        **closed(jira, closed_at_src=recent, complexity_points=8, story_points=Decimal(13)),
    )
    jira_issue(db_session, abc, 2, **closed(stranger, closed_at_src=recent))
    jira_issue(db_session, lone, 1, **closed(jira, closed_at_src=recent, complexity_points=2))
    db_session.add(
        Issue(id=602, project_id=world.project.id, iid=2, **closed(jira, closed_at_src=recent))
    )
    db_session.flush()

    gitlab_view = client.get(f"{API}/readiness", headers=as_user(world.admin)).json()
    assert gitlab_view["task_source"] == "gitlab" and gitlab_view["jira"] is None
    assert gitlab_view["repos"][0]["issues_closed"] == 1

    use_jira(db_session)
    body = client.get(f"{API}/readiness", headers=as_user(world.admin)).json()
    assert body["task_source"] == "jira"
    assert body["repos"][0]["issues_closed"] is None
    assert body["repos"][0]["complexity_pct"] is None
    rows = {p["key"]: p for p in body["jira"]["projects"]}
    assert set(rows) == {"ABC", "LONE"}  # the admin sees the one without a dự án too
    assert rows["ABC"]["issues_closed"] == 2 and rows["ABC"]["story_points_pct"] == 50.0
    assert rows["ABC"]["oversized"] == 1 and rows["ABC"]["last_error"] == "401"
    assert rows["ABC"]["business_project"] == {"id": bp, "name": "Demo"}
    assert rows["LONE"]["business_project"] is None
    assert body["jira"]["assignees"] == {"total": 2, "linked": 1}
    assert body["acceptance"]["issues_with_complexity_pct"] == round(100 * 2 / 3, 1)

    # Someone seeing only Demo: no LONE (no dự án), nor another dự án's project.
    other = BusinessProject(name="Khác")
    db_session.add(other)
    db_session.flush()
    jira_project(db_session, "OTH", other.id)
    seen = repo_readiness(db_session, recent - timedelta(days=1), "jira", visible={bp or 0})
    assert seen["jira"] is not None
    assert [p["key"] for p in seen["jira"]["projects"]] == ["ABC"]

    # A DPI admin who is no app admin sees every Jira project, LONE included.
    dpi_admin = PermissionSet(name="DPI admin only", builtin=False)
    db_session.add(dpi_admin)
    db_session.flush()
    db_session.add_all(
        [
            PermissionSetItem(set_id=dpi_admin.id, permission="dpi.admin", scope="flag"),
            UserPermissionSet(user_id=world.binh.id, set_id=dpi_admin.id),
        ]
    )
    db_session.flush()
    as_dpi_admin = client.get(f"{API}/readiness", headers=as_user(world.binh)).json()
    assert {p["key"] for p in as_dpi_admin["jira"]["projects"]} == {"ABC", "LONE", "OTH"}


def test_delivery_counts_the_jira_issues_of_the_projects_one_sees(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    use_jira(db_session)
    other = BusinessProject(name="Khác")
    db_session.add(other)
    db_session.flush()
    bp = world.project.business_project_id
    abc = jira_project(db_session, "ABC", bp)
    oth = jira_project(db_session, "OTH", other.id)
    lone = jira_project(db_session, "LONE", None)
    old = datetime.now(UTC) - timedelta(days=40)
    yesterday = date.today() - timedelta(days=1)
    jira_issue(db_session, abc, 1, state="opened", created_at_src=old, due_date=yesterday)
    jira_issue(
        db_session,
        abc,
        2,
        state="opened",
        created_at_src=old,
        labels=["blocked::impediment", "Blocked"],  # two labels: blocked once on its row
    )
    jira_issue(db_session, oth, 1, state="opened", created_at_src=old)
    jira_issue(db_session, lone, 1, state="opened", created_at_src=old)
    db_session.add(Issue(id=603, project_id=world.project.id, iid=3, state="opened"))
    db_session.flush()

    body = client.get(f"{API}/delivery", headers=as_user(world.lead)).json()
    assert body["task_source"] == "jira"
    assert body["open_issues"] == 2 and body["overdue_issues"] == 1  # not OTH, LONE, GitLab's
    assert body["blockers"] == [
        {"reason": "impediment", "issues": 1},
        {"reason": "không ghi lý do", "issues": 1},
    ]
    jira_row = next(r for r in body["repos"] if r["project"] == "Jira · ABC")
    assert jira_row == {
        "project": "Jira · ABC",
        "wip_mrs": 0,
        "open_issues": 2,
        "overdue": 1,
        "blocked": 1,
    }
    admin = client.get(f"{API}/delivery", headers=as_user(world.admin)).json()
    assert admin["open_issues"] == 4  # the admin sees every Jira project
    only = client.get(
        f"{API}/delivery?business_project_id={other.id}", headers=as_user(world.admin)
    ).json()
    assert only["open_issues"] == 1


def test_defects_follow_the_source_and_the_jira_projects_scope(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    use_jira(db_session)
    other = BusinessProject(name="Khác")
    db_session.add(other)
    db_session.flush()
    abc = jira_project(db_session, "ABC", world.project.business_project_id)
    oth = jira_project(db_session, "OTH", other.id)
    created = datetime(2026, 10, 15, tzinfo=UTC)
    mine = jira_issue(db_session, abc, 7, prod_severity="P1", created_at_src=created)
    theirs = jira_issue(db_session, oth, 7, prod_severity="P2", created_at_src=created)
    gitlab_bug = Issue(
        id=604, project_id=world.project.id, iid=4, prod_severity="P2", created_at_src=created
    )
    db_session.add(gitlab_bug)
    db_session.flush()
    for issue in (mine, theirs, gitlab_bug):
        db_session.add(ProdDefect(issue_id=issue.id, severity=issue.prod_severity))
    db_session.flush()

    body = client.get(f"{API}/defects", headers=as_user(world.lead)).json()
    assert body["task_source"] == "jira"
    assert [(d["issue"]["key"], d["issue"]["source"]) for d in body["items"]] == [("ABC-7", "jira")]
    admin = client.get(f"{API}/defects", headers=as_user(world.admin)).json()["items"]
    assert {d["issue"]["key"] for d in admin} == {"ABC-7", "OTH-7"}
    filtered = client.get(
        f"{API}/defects?business_project_id={other.id}", headers=as_user(world.admin)
    ).json()["items"]
    assert [d["issue"]["key"] for d in filtered] == ["OTH-7"]

    hidden = db_session.query(ProdDefect).filter_by(issue_id=theirs.id).one()
    url = f"{API}/defects/{hidden.id}/rca"
    assert client.put(url, headers=as_user(world.lead), json={"status": "process"}).status_code == (
        404
    )
    own = db_session.query(ProdDefect).filter_by(issue_id=mine.id).one()
    url = f"{API}/defects/{own.id}/rca"
    assert client.put(url, headers=as_user(world.lead), json={"status": "process"}).status_code == (
        200
    )


def test_rca_suggestions_of_a_jira_bug_come_from_the_mrs_that_close_it(
    world: World, db_session: Session
) -> None:
    abc = jira_project(db_session, "ABC", world.project.business_project_id)
    created = datetime(2026, 10, 15, tzinfo=UTC)
    bug = jira_issue(db_session, abc, 9, prod_severity="P1", created_at_src=created)
    defect = ProdDefect(issue_id=bug.id, severity="P1")
    db_session.add(defect)
    db_session.flush()
    assert suggest(db_session, defect, bug) == []  # no MR linked: no repo, no candidates

    culprit = MergeRequest(
        project_id=world.project.id,
        iid=30,
        title="đổi thanh toán",
        state="merged",
        merged_at=created - timedelta(days=2),
    )
    fix = MergeRequest(project_id=world.project.id, iid=31, title="ABC-9 sửa", state="merged")
    db_session.add_all([culprit, fix])
    db_session.flush()
    from app.models import Commit

    for sha, mr in (("c" * 40, culprit), ("d" * 40, fix)):
        db_session.add(Commit(sha=sha, project_id=world.project.id))
        db_session.flush()
        db_session.add_all(
            [
                MrCommit(mr_id=mr.id, sha=sha),
                CommitFile(sha=sha, path="pay.py", additions=1, deletions=0),
            ]
        )
    db_session.add(IssueMrLink(issue_id=bug.id, mr_id=fix.id, source="closes"))
    db_session.flush()
    assert [s["ref"] for s in suggest(db_session, defect, bug)] == ["!30"]


def test_the_ai_suggestion_sees_jira_titles_of_the_chosen_source_only(
    world: World, db_session: Session, accounts: tuple[Account, Account]
) -> None:
    from app.dpi.suggest import gather

    gitlab, jira = accounts
    world.project.allow_external_llm = False  # the repo gate is GitLab's, not Jira's
    db_session.add(
        Issue(id=605, project_id=world.project.id, iid=5, title="GitLab", **closed(gitlab))
    )
    abc = jira_project(db_session, "ABC", world.project.business_project_id)
    jira_issue(db_session, abc, 4, **closed(jira))
    month = date(2026, 10, 1)
    data, refs = gather(Loader(db_session, month, TZ, source="jira"), world.an.id, month, {})
    assert [i["title"] for i in data["issue_da_dong"]] == ["ABC việc 4"]
    assert refs[data["issue_da_dong"][0]["ref"]]["label"] == "ABC-4 ABC việc 4"
