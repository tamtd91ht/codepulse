"""The key=value review command with abbreviated repo/user hints (docs/specs/15 §Lệnh)."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Account, BusinessProject, Commit, Project, TelegramBot
from app.telegram.resolve import Kind
from tests.api.conftest import FakeQueue, World
from tests.api.test_telegram import link, message, service


class PickLast:
    """A resolver that always chooses the last candidate and remembers what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[str]]] = []

    def pick(self, kind: Kind, hint: str, candidates: Sequence[str]) -> int | None:
        self.calls.append((kind, hint, list(candidates)))
        return len(candidates) - 1


def add_repo(session: Session, world: World, path: str, bp: int) -> Project:
    project = Project(
        connection_id=world.project.connection_id,
        external_id=abs(hash(path)) % 100000 + 2000,
        path_with_namespace=path,
        business_project_id=bp,
    )
    session.add(project)
    session.flush()
    return project


def test_abbreviated_repo_and_user_resolve_deterministically(
    db_session: Session,
    bot: TelegramBot,
    world: World,
    settings: Settings,
    queue: FakeQueue,
) -> None:
    bp = db_session.get(BusinessProject, bot.business_project_id)
    assert bp is not None
    api = add_repo(db_session, world, "vihat/cloud-saas-global-api", bp.id)
    add_repo(db_session, world, "vihat/cloud-saas-global-web", bp.id)
    # binh's email is binh@x.vn: "binh" is its local part; the lead may review binh.
    db_session.add(
        Account(kind="git_email", external_key="binh.personal@gmail.com", user_id=world.binh.id)
    )
    db_session.flush()
    account = db_session.query(Account).filter_by(external_key="binh.personal@gmail.com").one()
    now = datetime(2026, 10, 24, 9, tzinfo=UTC)
    db_session.add_all(
        [
            Commit(
                sha="1" * 40,
                project_id=api.id,
                author_account_id=account.id,
                authored_at=now - timedelta(hours=2),
            ),
            Commit(
                sha="2" * 40,
                project_id=api.id,
                author_account_id=account.id,
                authored_at=now - timedelta(days=3),
            ),
            Commit(
                sha="3" * 40,
                project_id=api.id,
                author_account_id=account.id,
                authored_at=now - timedelta(days=20),
            ),
        ]
    )
    db_session.flush()
    link(db_session, world.lead, 888)
    svc = service(db_session, bot, settings, queue, clock=now)

    reply = svc.handle(message("/review type=commit repo=global-api user=binh", from_id=888)) or ""
    assert "Đã đưa <b>1</b> review" in reply and "cloud-saas-global-api 11111111" in reply
    assert queue.jobs[-1][1] == (api.id, "1" * 40)

    reply = (
        svc.handle(
            message("/review type=commit user=binh range=7d repo=global-api all=1", from_id=888)
        )
        or ""
    )
    # 2222 is inside 7 days, 3333 is not; 1111 is queued again (same RQ job id: one run).
    assert "Đã đưa <b>2</b> review" in reply and "22222222" in reply and "33333333" not in reply

    # A member cannot widen scope with user=: an may not review binh.
    link(db_session, world.an, 777)
    denied = svc.handle(message("/review type=commit user=binh repo=global-api")) or ""
    assert "không review được" in denied

    # type=mr without id: the latest MR of the sender (none here -> a clear note, nothing queued).
    before = len(queue.jobs)
    note = svc.handle(message("/review type=mr repo=global-api")) or ""
    assert len(queue.jobs) == before and note


def test_ambiguous_hints_go_to_the_resolver_or_are_asked_back(
    db_session: Session,
    bot: TelegramBot,
    world: World,
    settings: Settings,
    queue: FakeQueue,
) -> None:
    bp = db_session.get(BusinessProject, bot.business_project_id)
    assert bp is not None
    add_repo(db_session, world, "vihat/x-api", bp.id)
    y_api = add_repo(db_session, world, "vihat/y-api", bp.id)
    link(db_session, world.lead, 888)

    asked = service(db_session, bot, settings, queue)
    reply = asked.handle(message("/review type=mr id=5 repo=api", from_id=888)) or ""
    assert "nhiều repo khớp" in reply and "vihat/x-api" in reply and "vihat/y-api" in reply

    picker = PickLast()
    resolved = service(db_session, bot, settings, queue)
    resolved._resolver = picker
    reply = resolved.handle(message("/review type=mr id=5 repo=api", from_id=888)) or ""
    assert picker.calls and picker.calls[0][0] == "repo" and picker.calls[0][1] == "api"
    assert "vihat/y-api" in reply and y_api.path_with_namespace == "vihat/y-api"  # resolved to it
