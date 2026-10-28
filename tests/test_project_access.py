"""Dự án grants on Principal (docs/specs/09 "Phân quyền theo dự án"): pure rules, no database."""

from sqlalchemy.dialects import postgresql

from app.api.access import review_scope
from app.api.security import Principal

OMI, CALL = 1, 2  # two dự án


def person(**kw: object) -> Principal:
    base: dict[str, object] = {"user_id": 10, "email": "a@x.vn", "is_admin": False}
    return Principal(**(base | kw))  # type: ignore[arg-type]


def test_admin_sees_and_manages_every_dự_án_and_the_unassigned_repos() -> None:
    admin = person(is_admin=True)
    assert admin.visible_projects() is None and admin.managed_projects() is None
    assert admin.can_view_project(None) and admin.can_manage_project(OMI)
    assert admin.can_see_work(99, None)
    assert review_scope(admin) is None


def test_without_a_grant_a_dự_án_and_repos_outside_one_are_hidden() -> None:
    dev = person()
    assert dev.visible_projects() == set()
    assert not dev.can_view_project(OMI) and not dev.can_view_project(None)
    assert not dev.can_see_work(99, OMI)
    assert not dev.may_review_work(dev.user_id, OMI)  # not even your own commit there
    assert dev.can_see_work(dev.user_id, OMI)  # but your own review stays yours


def test_view_keeps_the_team_rules_inside_the_dự_án() -> None:
    lead = person(lead_teams={5}, lead_members={10, 11}, project_roles={OMI: "view"})
    assert lead.can_view_project(OMI) and not lead.can_manage_project(OMI)
    assert lead.can_see_work(11, OMI)  # a member of the team they lead
    assert not lead.can_see_work(12, OMI)  # someone else in the same dự án
    assert not lead.can_see_work(11, CALL)  # their member, but in a dự án they lack
    assert not lead.can_see_work(None, OMI)  # unmapped author: managers only
    assert lead.may_review_work(None, OMI)  # leads may still ask for unmapped work
    assert not lead.may_review_work(12, OMI)


def test_manage_covers_everyone_in_that_dự_án_only() -> None:
    dev = person(project_roles={OMI: "manage", CALL: "view"})
    assert dev.managed_projects() == {OMI}
    assert dev.can_see_work(12, OMI) and dev.can_see_work(None, OMI)
    assert dev.may_review_work(12, OMI)
    assert not dev.can_see_work(12, CALL) and not dev.may_review_work(12, CALL)
    assert not dev.can_manage_project(CALL)


def test_review_scope_mirrors_the_rules_in_sql() -> None:
    def sql(p: Principal) -> str:
        scope = review_scope(p)
        assert scope is not None
        return str(scope.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]

    alone = sql(person())
    assert "reviews.author_user_id = " in alone and "merge_requests" not in alone
    # A lead with view only: their members, restricted to the dự án they see.
    led = sql(person(lead_members={10, 11}, project_roles={OMI: "view"}))
    assert "EXISTS" in led and "reviews.author_user_id IN" in led
    # A manager: the dự án's reviews whoever wrote them.
    managed = sql(person(project_roles={OMI: "manage"}))
    assert "EXISTS" in managed and "reviews.author_user_id IN" not in managed
