from pathlib import Path

import pytest
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collector.sync import sync_project
from app.identity.autolink import autolink, is_bot, unmapped_accounts
from app.identity.csv_import import CsvImportError, import_identity
from app.models import Account, Team, TeamMember, User
from tests.collector.fake_gitlab import mount
from tests.collector.test_collect import make_project
from tests.collector.test_sync import make_client

pytestmark = pytest.mark.integration


def owner(session: Session, kind: str, key: str) -> str | None:
    account = session.scalars(
        select(Account).where(Account.kind == kind, Account.external_key == key)
    ).one()
    user = session.get(User, account.user_id) if account.user_id else None
    return user.full_name if user else None


def test_sync_links_accounts_by_every_rule(db_session: Session) -> None:
    db_session.add_all(
        [
            User(full_name="An", primary_email="an.nguyen@company.vn"),
            User(full_name="Binh", primary_email="binh.tran@company.vn"),
        ]
    )
    project = make_project(db_session)
    with respx.mock(assert_all_called=False) as router:
        mount(router)
        result = sync_project(
            db_session,
            project,
            make_client,
            backfill_days=90,
            ignore_paths=[],
            allowed_email_domains=["company.vn"],
        )

    assert owner(db_session, "gitlab_user", "11") == "An"  # public email on GitLab profile
    assert owner(db_session, "gitlab_user", "12") == "Binh"  # username@allowed domain
    assert owner(db_session, "git_email", "an.nguyen@company.vn") == "An"  # primary email
    noreply = "12-binh.tran@users.noreply.gitlab.com"
    assert owner(db_session, "git_email", noreply) == "Binh"  # noreply -> GitLab user 12
    assert [a.external_key for a in unmapped_accounts(db_session)] == ["binh.personal@gmail.com"]
    assert result.counts is not None and result.counts["identity.linked"] == 4


def test_autolink_never_overrides_and_parks_bots(db_session: Session) -> None:
    an = User(full_name="An", primary_email="an@company.vn")
    other = User(full_name="Other", primary_email="other@company.vn")
    db_session.add_all([an, other])
    db_session.flush()
    manual = Account(kind="git_email", external_key="an@company.vn", user_id=other.id)
    bot = Account(kind="git_email", external_key="renovate-bot@example.com")
    gl_bot = Account(kind="gitlab_user", external_key="99", display_name="project_7_bot_abc")
    db_session.add_all([manual, bot, gl_bot])
    db_session.flush()

    result = autolink(db_session, ["company.vn"])
    assert manual.user_id == other.id
    assert result.bots == 2
    for account in (bot, gl_bot):
        user = db_session.get(User, account.user_id)
        assert user is not None and user.active is False


@pytest.mark.parametrize(
    ("key", "name", "bot"),
    [
        ("dependabot[bot]@users.noreply.github.com", None, True),
        ("123", "group_4_bot_x", True),
        ("ci-bot@company.vn", None, True),
        ("an.nguyen@company.vn", "an.nguyen", False),
        ("robot.lee@company.vn", None, False),
    ],
)
def test_bot_detection(key: str, name: str | None, bot: bool) -> None:
    assert is_bot(Account(kind="git_email", external_key=key, display_name=name)) is bot


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_csv_import_is_idempotent(db_session: Session, tmp_path: Path) -> None:
    teams = write(
        tmp_path, "teams.csv", "team_name,parent_team\nBackend,Engineering\nEngineering,\n"
    )
    users = write(
        tmp_path,
        "users.csv",
        "full_name,primary_email,team_name,role,joined_at\n"
        "Nguyen Van A,A@company.vn,Backend,member,2025-01-01\n",
    )
    accounts = write(
        tmp_path,
        "accounts.csv",
        "kind,external_key,primary_email\n"
        "git_email,A.Personal@gmail.com,a@company.vn\n"
        "gitlab_user,1234567,a@company.vn\n",
    )
    for _ in range(2):
        result = import_identity(db_session, teams=teams, users=users, accounts=accounts)
        assert result.errors == []

    backend = db_session.scalars(select(Team).where(Team.name == "Backend")).one()
    engineering = db_session.scalars(select(Team).where(Team.name == "Engineering")).one()
    assert backend.parent_id == engineering.id  # parent listed after child still resolves
    assert len(db_session.scalars(select(TeamMember)).all()) == 1
    assert owner(db_session, "git_email", "a.personal@gmail.com") == "Nguyen Van A"
    assert owner(db_session, "gitlab_user", "1234567") == "Nguyen Van A"


def test_csv_errors_carry_file_and_line(db_session: Session, tmp_path: Path) -> None:
    users = write(
        tmp_path,
        "users.csv",
        "full_name,primary_email,team_name,role,joined_at\n"
        "X,not-an-email,,member,2025-01-01\n"
        "Y,y@company.vn,Nope,member,2025-01-01\n",
    )
    accounts = write(
        tmp_path,
        "accounts.csv",
        "kind,external_key,primary_email\nslack,abc,y@company.vn\ngitlab_user,alice,y@company.vn\n",
    )
    result = import_identity(db_session, users=users, accounts=accounts)
    assert result.errors == [
        "users.csv:2: invalid primary_email 'not-an-email'",
        "users.csv:3: unknown team 'Nope'",
        "accounts.csv:2: kind must be one of ['git_email', 'gitlab_user']",
        "accounts.csv:3: gitlab_user external_key is the numeric id",
    ]


def test_csv_missing_columns_is_rejected(db_session: Session, tmp_path: Path) -> None:
    with pytest.raises(CsvImportError, match="missing columns"):
        import_identity(db_session, teams=write(tmp_path, "teams.csv", "name\nBackend\n"))
