"""Choosing commits / MRs with buttons (docs/specs/15 §Chọn bằng nút)."""

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Account,
    Commit,
    MergeRequest,
    Project,
    Review,
    TelegramBot,
    TelegramRequest,
)
from app.review.rubric import RUBRIC_VERSION
from app.telegram.picker import MemoryPickerStore
from app.telegram.poller import poll_bot
from app.telegram.service import AllowAll, MemoryLinkCodes
from tests.api.conftest import FakeQueue, World, grant
from tests.api.test_telegram import FakeGitLab, FakeTelegram, link, no_gitlab

NOW = datetime(2026, 10, 24, 9, tzinfo=UTC)


def link_manager(db_session: Session, world: World, telegram_id: int = 777) -> None:
    """The Backend lead, made a manager of the bot's dự án: only managers (and admins) review
    other people's work (2026-11-02)."""
    link(db_session, world.lead, telegram_id)
    grant(db_session, world.lead, world.project.business_project_id, "manage")


def message_update(update_id: int, text: str, from_id: int = 777) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": -100, "type": "group"},
            "from": {"id": from_id, "username": "tam"},
            "text": text,
        },
    }


def tap(update_id: int, data: str, from_id: int = 777, message_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "from": {"id": from_id, "username": "tam"},
            "message": {"message_id": message_id, "chat": {"id": -100, "type": "group"}},
            "data": data,
        },
    }


def buttons(markup: dict[str, Any] | None) -> dict[str, str]:
    """text -> callback_data of every button in a keyboard."""
    return {
        b["text"]: b["callback_data"]
        for row in (markup or {}).get("inline_keyboard", [])
        for b in row
    }


def seed_commits(db_session: Session, world: World) -> None:
    an = Account(kind="git_email", external_key="an@x.vn", user_id=world.an.id)
    binh = Account(kind="git_email", external_key="binh@x.vn", user_id=world.binh.id)
    db_session.add_all([an, binh])
    db_session.flush()
    db_session.add_all(
        [
            Commit(
                sha="1" * 40,
                project_id=world.project.id,
                author_account_id=an.id,
                authored_at=NOW - timedelta(hours=1),
                message="feat: one\n",
            ),
            Commit(
                sha="2" * 40,
                project_id=world.project.id,
                author_account_id=binh.id,
                authored_at=NOW - timedelta(hours=5),
                message="fix: two\n",
            ),
            Commit(
                sha="3" * 40,
                project_id=world.project.id,
                author_account_id=an.id,
                authored_at=NOW - timedelta(days=2),
                message="chore: three\n",
            ),
            Commit(
                sha="4" * 40,
                project_id=world.project.id,
                author_account_id=an.id,
                authored_at=NOW - timedelta(days=20),
                message="old\n",
            ),
            Commit(
                sha="5" * 40,
                project_id=world.project.id,
                author_account_id=an.id,
                authored_at=NOW - timedelta(hours=2),
                is_merge=True,
                message="Merge\n",
            ),
        ]
    )
    db_session.add(
        Review(
            target_type="commit",
            commit_sha="3" * 40,
            reviewed_sha="3" * 40,
            status="done",
            rubric_version=RUBRIC_VERSION,
            score=82,
            verdict="approve",
        )
    )
    db_session.flush()


def run(
    db_session: Session,
    bot: TelegramBot,
    settings: Settings,
    queue: FakeQueue,
    tg: FakeTelegram,
    store: MemoryPickerStore,
) -> int:
    return poll_bot(
        db_session,
        bot,
        tg,
        settings,
        queue,
        no_gitlab,
        MemoryLinkCodes(),
        AllowAll(),
        lambda: NOW,
        picker=store,
    )


def test_commit_picker_lists_everyone_and_queues_the_ticked_ones(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    world.project.default_branch = "main"
    link_manager(db_session, world)
    link(db_session, world.binh, 999)  # another linked person: may look, may not tap this list
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review type=commit repo=demo/p")]
    run(db_session, bot, settings, queue, tg, store)

    (listing,) = tg.sent
    text = listing["text"]
    assert (
        "<b>Commit gần đây</b> · demo/p · nhánh main · 7 ngày · 2 mục" in text
    )  # merge + 20-day-old left out; the reviewed one is hidden by default
    assert "feat: one · An" in text and "fix: two · Binh" in text
    assert "chore: three" not in text and "Đã ẩn 1 mục đã review" in text
    kb = buttons(listing["markup"])
    assert set(kb) >= {"☐ 1", "☐ 2", "☑ Chọn hết", "▶ Review đã chọn (0)", "✖ Huỷ"}
    assert "☐ 3" not in kb  # already reviewed: no toggle
    toggle_one = kb["☐ 1"]
    token = toggle_one.split("|")[1]

    # Someone else taps: refused; the owner taps: the list is redrawn with the tick.
    tg.updates = [tap(2, toggle_one, from_id=999), tap(3, toggle_one)]
    run(db_session, bot, settings, queue, tg, store)
    assert tg.answered[0]["alert"] is True and "người khác" in tg.answered[0]["text"]
    assert tg.edited and "☑ <b>1.</b> feat: one" in tg.edited[-1]["text"]
    assert "▶ Review đã chọn (1)" in buttons(tg.edited[-1]["markup"])

    # Go: the ticked commit is queued through the normal job, a request row remembers the chat.
    tg.updates = [tap(4, f"pk|{token}|go")]
    run(db_session, bot, settings, queue, tg, store)
    assert [args for func, args, _ in queue.jobs] == [(world.project.id, "1" * 40)]
    assert (
        queue.jobs[0][2]["trigger"] == "telegram"
        and queue.jobs[0][2]["requested_by"] == world.lead.id
    )
    assert "at_front" not in queue.jobs[0][2]  # own queue, first come first served
    assert "Đã đưa <b>1</b> review" in tg.edited[-1]["text"] and tg.edited[-1]["markup"] is None
    request = db_session.scalars(select(TelegramRequest)).one()
    assert request.targets[0]["sha"] == "1" * 40 and request.user_id == world.lead.id
    assert store.load(token) is None  # consumed

    # The list is gone: a late tap says so instead of crashing.
    tg.updates = [tap(5, f"pk|{token}|t|0")]
    run(db_session, bot, settings, queue, tg, store)
    assert "hết hạn" in tg.answered[-1]["text"]


def test_people_step_filters_the_list_to_one_person(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review")]
    run(db_session, bot, settings, queue, tg, store)
    menu = buttons(tg.sent[0]["markup"])
    tg.updates = [tap(2, menu["📝 Commit gần đây"])]
    run(db_session, bot, settings, queue, tg, store)
    who = buttons(tg.edited[-1]["markup"])
    tg.updates = [tap(3, who["Binh"])]
    run(db_session, bot, settings, queue, tg, store)
    text = tg.edited[-1]["text"]
    assert "1 mục" in text and "fix: two · Binh" in text and "feat: one" not in text
    assert "· Binh" in text.splitlines()[0]  # the scope line names the person
    # The "Người" button inside the list goes back to the people keyboard.
    kb = buttons(tg.edited[-1]["markup"])
    tg.updates = [tap(4, kb["👤 Người"])]
    run(db_session, bot, settings, queue, tg, store)
    assert "👥 Mọi người" in buttons(tg.edited[-1]["markup"])
    # A member only has themself: no people step, straight to their own list.
    tg2 = FakeTelegram()
    link(db_session, world.an, 778)
    db_session.commit()
    tg2.updates = [
        message_update(5, "/review", from_id=778),
    ]
    run(db_session, bot, settings, queue, tg2, store)
    assert tg2.sent[0]["text"] == "Review commit / MR nào của bạn?"
    tg2.updates = [tap(6, buttons(tg2.sent[0]["markup"])["📝 Commit của tôi"], from_id=778)]
    run(db_session, bot, settings, queue, tg2, store)
    text = tg2.edited[-1]["text"]
    assert "<b>Commit gần đây</b>" in text and "của bạn" in text.splitlines()[0]
    assert "feat: one · An" in text and "Binh" not in text
    # Tapping the manager's "Binh" button in the group does not show a member Binh's work.
    tg2.updates = [tap(7, who["Binh"], from_id=778)]
    run(db_session, bot, settings, queue, tg2, store)
    assert "Binh" not in tg2.edited[-1]["text"] and "feat: one" in tg2.edited[-1]["text"]


def test_a_members_list_holds_only_their_own_work(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    link(db_session, world.an, 777)
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review type=commit")]
    run(db_session, bot, settings, queue, tg, store)
    text = tg.sent[0]["text"]
    # Binh's commit is not even listed (no 🚫 rows): An's two, every branch by default.
    assert "Binh" not in text and "🚫" not in text and "1 mục" in text
    assert "Đã ẩn 1 mục đã review" in text  # 3333 is done: left out unless include_reviewed=1
    assert "mọi nhánh" in text.splitlines()[0] and "của bạn" in text.splitlines()[0]
    kb = buttons(tg.sent[0]["markup"])
    assert "☐ 1" in kb and "👤 Người" not in kb  # no switching to someone else's list
    token = kb["☐ 1"].split("|")[1]
    # A forged "whose?" tap is refused.
    tg.updates = [tap(2, f"pk|{token}|who")]
    run(db_session, bot, settings, queue, tg, store)
    assert "chính mình" in tg.answered[-1]["text"]
    tg.updates = [tap(3, f"pk|{token}|all"), tap(4, f"pk|{token}|go")]
    run(db_session, bot, settings, queue, tg, store)
    assert [args[1] for _, args, _ in queue.jobs] == ["1" * 40]  # 3333 is already reviewed
    # user= cannot widen the list to someone else.
    tg.updates = [message_update(5, "/review type=commit user=binh")]
    run(db_session, bot, settings, queue, tg, store)
    assert "chính mình" in tg.sent[-1]["text"]


def test_bare_review_opens_the_menu_and_mr_picker_lists_open_mrs(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link_manager(db_session, world)
    world.mr.state = "opened"
    world.mr.title = "Add parser"
    db_session.add(
        MergeRequest(
            project_id=world.project.id, iid=9, head_sha="9" * 40, state="opened", title="Fix auth"
        )
    )
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review")]
    run(db_session, bot, settings, queue, tg, store)
    menu = buttons(tg.sent[0]["markup"])
    assert "🔀 MR đang mở" in menu and tg.sent[0]["text"] == "Bạn muốn review gì?"
    # One repo in scope, but a manager may review everyone: pick whose MRs first.
    tg.updates = [tap(2, menu["🔀 MR đang mở"])]
    run(db_session, bot, settings, queue, tg, store)
    who = buttons(tg.edited[-1]["markup"])
    assert "🙋 Của tôi" in who and "👥 Mọi người" in who
    # Everyone granted the dự án (the manager is not listed twice).
    assert {"An", "Binh", "Outsider"} <= set(who) and "Lead" not in who
    tg.updates = [tap(20, who["👥 Mọi người"])]
    run(db_session, bot, settings, queue, tg, store)
    listed = tg.edited[-1]
    assert "<b>MR đang mở</b> · demo/p · 1 mục" in listed["text"]
    assert "!9 Fix auth" in listed["text"]
    assert "Đã ẩn 1 mục đã review" in listed["text"]  # !7 has a done review for its head
    kb = buttons(listed["markup"])
    toggle = next(v for k, v in kb.items() if k.startswith("☐"))
    token = toggle.split("|")[1]
    tg.updates = [tap(21, toggle), tap(22, f"pk|{token}|go")]
    run(db_session, bot, settings, queue, tg, store)
    (job,) = queue.jobs
    assert job[0].endswith("review_mr_job") and job[1] == (world.project.id, 9, "9" * 40)
    tg.updates = [tap(23, "mn||x")]
    run(db_session, bot, settings, queue, tg, store)
    assert tg.edited[-1]["text"] == "Đã huỷ."


def test_the_repo_step_pages_through_every_repo_of_the_dự_án(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link_manager(db_session, world)
    paths = [f"demo/svc-{i:02}" for i in range(1, 12)]
    db_session.add_all(
        Project(
            connection_id=world.project.connection_id,
            external_id=2000 + i,
            path_with_namespace=path,
            business_project_id=bot.business_project_id,
        )
        for i, path in enumerate(paths)
    )
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [tap(1, "mn||commit")]
    run(db_session, bot, settings, queue, tg, store)
    first = tg.edited[-1]
    assert first["text"] == "Chọn repo để xem commit gần đây (12 repo, trang 1/2):"
    page_one = buttons(first["markup"])
    tg.updates = [tap(2, page_one["Sau ▶"])]
    run(db_session, bot, settings, queue, tg, store)
    page_two = buttons(tg.edited[-1]["markup"])
    assert "trang 2/2" in tg.edited[-1]["text"] and "◀ Trước" in page_two
    listed = {k for k in (*page_one, *page_two) if k not in ("Sau ▶", "◀ Trước", "✖ Huỷ")}
    assert listed == {"p", *(p.split("/")[-1] for p in paths)}  # all 12, none dropped


def test_again_lets_the_owner_tick_a_reviewed_commit_as_a_new_round(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    world.project.default_branch = "main"
    link_manager(db_session, world)
    # A new round spends budget: managers of the dự án only (docs/specs/09).
    grant(db_session, world.lead, world.project.business_project_id, "manage")
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review type=commit repo=demo/p")]
    run(db_session, bot, settings, queue, tg, store)
    kb = buttons(tg.sent[0]["markup"])
    token = kb["☐ 1"].split("|")[1]
    tg.updates = [tap(2, kb["↻ Cho chọn mục đã review"])]
    run(db_session, bot, settings, queue, tg, store)
    kb = buttons(tg.edited[-1]["markup"])
    assert "☐ 3" in kb and "↩ Chỉ mục mới" in kb
    assert "review lại thành lần mới" in tg.edited[-1]["text"]
    tg.updates = [tap(3, kb["☐ 3"]), tap(4, f"pk|{token}|go")]
    run(db_session, bot, settings, queue, tg, store)
    ((_, args, kwargs),) = queue.jobs
    assert args == (world.project.id, "3" * 40)
    assert kwargs["force"] is True and kwargs["job_id"].endswith("-again-1")
    assert kwargs["expected_round"] == 1


def test_a_member_cannot_review_their_own_work_again(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    link(db_session, world.an, 777)
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review type=commit repo=demo/p")]
    run(db_session, bot, settings, queue, tg, store)
    kb = buttons(tg.sent[0]["markup"])
    assert "↻ Cho chọn mục đã review" not in kb  # re-rolling a score is a manager's call
    token = kb["☐ 1"].split("|")[1]
    tg.updates = [tap(2, f"pk|{token}|again")]
    run(db_session, bot, settings, queue, tg, store)
    assert "quản lý dự án" in tg.answered[-1]["text"]


class BranchGitLab(FakeGitLab):
    """GitLab that answers: an empty list of commits for whatever branch is asked."""

    asked: ClassVar[list[str]] = []

    def commits_on_ref(
        self, project_id: int, ref: str, since: Any, until: Any, limit: int
    ) -> list[Any]:
        BranchGitLab.asked.append(ref)
        return []


def test_the_repo_review_branch_is_listed_and_an_empty_branch_says_how_to_change_it(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)  # in the DB: synced commits of other branches
    world.project.default_branch = "main"
    world.project.review_branch = "develop"
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    BranchGitLab.asked = []
    tg.updates = [message_update(1, "/review type=commit repo=demo/p")]
    poll_bot(
        db_session,
        bot,
        tg,
        settings,
        queue,
        lambda connection: BranchGitLab(),
        MemoryLinkCodes(),
        AllowAll(),
        lambda: NOW,
        picker=MemoryPickerStore(),
    )
    assert BranchGitLab.asked == ["develop"]
    # GitLab answered "nothing on develop": no fallback to commits of other branches.
    (reply,) = tg.sent
    assert "không có commit nào trong 7 ngày qua trên nhánh develop" in reply["text"]
    assert "branch=all" in reply["text"]
    # branch=all asks GitLab for every branch.
    tg.updates = [message_update(2, "/review type=commit repo=demo/p branch=all")]
    poll_bot(
        db_session,
        bot,
        tg,
        settings,
        queue,
        lambda connection: BranchGitLab(),
        MemoryLinkCodes(),
        AllowAll(),
        lambda: NOW,
        picker=MemoryPickerStore(),
    )
    assert BranchGitLab.asked[-1] == "*" and "mọi nhánh" in tg.sent[-1]["text"]


class BusyGitLab(FakeGitLab):
    """A busy branch: 50 newer commits of Bình, then one of An under an email no account has."""

    limits: ClassVar[list[int]] = []

    def commits_on_ref(
        self, project_id: int, ref: str, since: Any, until: Any, limit: int
    ) -> list[Any]:
        BusyGitLab.limits.append(limit)
        raw = [
            {
                "id": f"{i:040x}",
                "title": f"binh {i}",
                "author_email": "binh@x.vn",
                "author_name": "binh",
                "authored_date": (NOW - timedelta(hours=1, minutes=i)).isoformat(),
                "parent_ids": ["p"],
            }
            for i in range(50)
        ]
        raw.append(
            {
                "id": "a" * 40,
                "title": "feat: an on dev",
                "author_email": "an@x.vn",  # An's primary email, never synced into an account
                "author_name": "AnND",
                "authored_date": (NOW - timedelta(days=3)).isoformat(),
                "parent_ids": ["p"],
            }
        )
        return raw[:limit]


def test_one_persons_commits_on_a_busy_branch_are_found(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    BusyGitLab.limits = []
    tg.updates = [
        message_update(1, "/review type=commit repo=demo/p branch=dev user=an@x.vn range=7d")
    ]
    poll_bot(
        db_session,
        bot,
        tg,
        settings,
        queue,
        lambda connection: BusyGitLab(),
        MemoryLinkCodes(),
        AllowAll(),
        lambda: NOW,
        picker=MemoryPickerStore(),
    )
    (reply,) = tg.sent
    assert "feat: an on dev" in reply["text"]
    assert "binh 0" not in reply["text"]
    assert BusyGitLab.limits[-1] > 51


class FeatureBranchGitLab(FakeGitLab):
    """The work is on a feature branch: the review branch is empty, every branch is not."""

    asked: ClassVar[list[str]] = []

    def commits_on_ref(
        self, project_id: int, ref: str, since: Any, until: Any, limit: int
    ) -> list[Any]:
        FeatureBranchGitLab.asked.append(ref)
        if ref != "*":
            return []
        return [
            {
                "id": "f" * 40,
                "parent_ids": ["e" * 40],
                "title": "Add invoice export",
                "author_email": "an@x.vn",
                "author_name": "An",
                "authored_date": (NOW - timedelta(hours=2)).isoformat(),
            }
        ]


def test_the_menu_offers_every_branch_when_the_review_branch_is_empty(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    world.project.default_branch = "main"
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    FeatureBranchGitLab.asked = []

    def step(update: dict[str, Any]) -> None:
        tg.updates = [update]
        poll_bot(
            db_session,
            bot,
            tg,
            settings,
            queue,
            lambda connection: FeatureBranchGitLab(),
            MemoryLinkCodes(),
            AllowAll(),
            lambda: NOW,
            picker=store,
        )

    step(message_update(1, "/review"))
    step(tap(2, buttons(tg.sent[0]["markup"])["📝 Commit gần đây"]))
    step(tap(3, buttons(tg.edited[-1]["markup"])["👥 Mọi người"]))
    empty = tg.edited[-1]
    assert "không có commit nào trong 7 ngày qua trên nhánh main" in empty["text"]
    step(tap(4, buttons(empty["markup"])["🌿 Xem mọi nhánh"]))
    listed = tg.edited[-1]
    assert FeatureBranchGitLab.asked == ["main", "*"]
    assert "mọi nhánh" in listed["text"] and "Add invoice export" in listed["text"]
    kb = buttons(listed["markup"])
    # The person step keeps the branch scope; the toggle goes back to the review branch.
    step(tap(5, kb["👤 Người"]))
    an = buttons(tg.edited[-1]["markup"])["An"]
    assert an.endswith(":*")
    step(tap(6, an))
    assert FeatureBranchGitLab.asked[-1] == "*" and "Add invoice export" in tg.edited[-1]["text"]
    step(tap(7, buttons(tg.edited[-1]["markup"])["🌿 Nhánh review"]))
    assert FeatureBranchGitLab.asked[-1] == "main"


def test_an_empty_typed_list_offers_every_branch_too(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    world.project.default_branch = "main"
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    tg.updates = [message_update(1, "/review type=commit repo=demo/p range=3d")]
    poll_bot(
        db_session,
        bot,
        tg,
        settings,
        queue,
        lambda connection: FeatureBranchGitLab(),
        MemoryLinkCodes(),
        AllowAll(),
        lambda: NOW,
        picker=MemoryPickerStore(),
    )
    (reply,) = tg.sent
    assert buttons(reply["markup"])["🌿 Xem mọi nhánh"].endswith(f"{world.project.id}:0:*:3")


def test_include_reviewed_lists_the_finished_items_too(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    world.project.default_branch = "main"
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review type=commit repo=demo/p includeReviewed=true")]
    run(db_session, bot, settings, queue, tg, store)
    text = tg.sent[0]["text"]
    assert "3 mục" in text and "Đã ẩn" not in text
    assert "✅ <b>3.</b> chore: three" in text and "đã review: APPROVE 82" in text
    # all=1 + include_reviewed: the finished one answers with its result, the rest is queued.
    tg.updates = [
        message_update(
            2, "/review type=commit repo=demo/p user=an range=7d all=1 include_reviewed=1"
        )
    ]
    run(db_session, bot, settings, queue, tg, store)
    assert "đã được review" in tg.sent[-1]["text"]
    assert [args[1] for _, args, _ in queue.jobs] == ["1" * 40]


def test_a_period_and_several_repos_make_one_list(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    world.project.default_branch = "main"
    other = Project(
        external_id=4242,
        connection_id=world.project.connection_id,
        path_with_namespace="demo/wallet-service",
        default_branch="main",
        business_project_id=world.project.business_project_id,
    )
    db_session.add(other)
    db_session.flush()
    an = db_session.scalars(select(Account).where(Account.external_key == "an@x.vn")).one()
    db_session.add(
        Commit(
            sha="6" * 40,
            project_id=other.id,
            author_account_id=an.id,
            authored_at=NOW - timedelta(days=3),
            message="feat: wallet\n",
        )
    )
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    start = (NOW - timedelta(days=25)).strftime("%d-%m-%Y")
    end = NOW.strftime("%d-%m-%Y")
    tg.updates = [
        message_update(1, f"/review type=commit repo=demo/p, wallet-service range={start}, {end}")
    ]
    run(db_session, bot, settings, queue, tg, store)
    text = tg.sent[0]["text"]
    first = text.splitlines()[0]
    assert first.startswith("<b>Commit</b> · p, wallet-service · nhánh main ·"), first
    # Both repos, newest first; the 20-day-old commit is inside the period now.
    assert "feat: wallet" in text and "feat: one" in text and "<b>4.</b> old" in text
    assert text.index("feat: one") < text.index("feat: wallet") < text.index("<b>4.</b> old")
    kb = buttons(tg.sent[0]["markup"])
    assert "👤 Người" not in kb  # the per-repo people step cannot carry two repos
    # The branch button keeps both repos and the period.
    tg.updates = [tap(2, kb["🌿 Mọi nhánh"])]
    run(db_session, bot, settings, queue, tg, store)
    again = tg.edited[-1]["text"].splitlines()[0]
    assert "p, wallet-service · mọi nhánh" in again
    token = kb["☐ 1"].split("|")[1]
    tg.updates = [tap(3, f"pk|{token}|all"), tap(4, f"pk|{token}|go")]
    run(db_session, bot, settings, queue, tg, store)
    assert {args[0] for _, args, _ in queue.jobs} == {world.project.id, other.id}


def test_the_latest_item_skips_reviewed_work_unless_include_reviewed(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    db_session.add(
        Review(
            target_type="commit",
            commit_sha="1" * 40,
            reviewed_sha="1" * 40,
            status="done",
            rubric_version=RUBRIC_VERSION,
            score=70,
            verdict="approve",
        )
    )
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    # 1111 and 3333 are done: An's latest commit still to review is the old 4444.
    tg.updates = [message_update(1, "/review type=commit user=an")]
    run(db_session, bot, settings, queue, tg, store)
    assert [args[1] for _, args, _ in queue.jobs] == ["4" * 40]
    # include_reviewed=1: the latest one whatever its state, answered with its result.
    tg.updates = [message_update(2, "/review type=commit user=an include_reviewed=1")]
    run(db_session, bot, settings, queue, tg, store)
    assert "đã được review" in tg.sent[-1]["text"] and len(queue.jobs) == 1
    # Several repos only make sense for a list.
    tg.updates = [message_update(3, "/review type=commit user=an repo=demo/p,other")]
    run(db_session, bot, settings, queue, tg, store)
    assert "một repo" in tg.sent[-1]["text"]


def test_listing_never_changes_a_failed_review(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    seed_commits(db_session, world)
    failed = Review(
        target_type="commit",
        commit_sha="2" * 40,
        reviewed_sha="2" * 40,
        status="failed",
        rubric_version=RUBRIC_VERSION,
    )
    db_session.add(failed)
    world.project.default_branch = "main"
    link_manager(db_session, world)
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review type=commit repo=demo/p")]
    run(db_session, bot, settings, queue, tg, store)
    assert "lần trước lỗi" in tg.sent[0]["text"]
    db_session.refresh(failed)
    assert failed.status == "failed" and not queue.jobs
