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
from app.telegram.picker import MemoryPickerStore
from app.telegram.poller import poll_bot
from app.telegram.service import AllowAll, MemoryLinkCodes
from tests.api.conftest import FakeQueue, World
from tests.api.test_telegram import FakeGitLab, FakeTelegram, link, no_gitlab

NOW = datetime(2026, 10, 24, 9, tzinfo=UTC)


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
            rubric_version="v2",
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
    link(db_session, world.lead, 777)
    link(db_session, world.binh, 999)  # another linked person: may look, may not tap this list
    db_session.commit()
    tg = FakeTelegram()
    store = MemoryPickerStore()
    tg.updates = [message_update(1, "/review type=commit repo=demo/p")]
    run(db_session, bot, settings, queue, tg, store)

    (listing,) = tg.sent
    text = listing["text"]
    assert (
        "<b>Commit gần đây</b> · demo/p · nhánh main · 7 ngày · 3 mục" in text
    )  # merge + 20-day-old left out
    assert "feat: one · An" in text and "fix: two · Binh" in text
    assert "✅ <b>3.</b> chore: three" in text and "đã review: APPROVE 82" in text
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
    assert queue.jobs[0][2]["at_front"] is True  # reviews do not wait behind syncs
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
    link(db_session, world.lead, 777)
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
    # A member only has themself: no people step, straight to the list.
    tg2 = FakeTelegram()
    link(db_session, world.an, 778)
    db_session.commit()
    tg2.updates = [
        message_update(5, "/review", from_id=778),
    ]
    run(db_session, bot, settings, queue, tg2, store)
    tg2.updates = [tap(6, buttons(tg2.sent[0]["markup"])["📝 Commit gần đây"], from_id=778)]
    run(db_session, bot, settings, queue, tg2, store)
    assert "<b>Commit gần đây</b>" in tg2.edited[-1]["text"]


def test_member_sees_others_as_blocked_and_select_all_respects_it(
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
    assert "🚫 <b>2.</b> fix: two · Binh" in text and "ngoài quyền review" in text
    kb = buttons(tg.sent[0]["markup"])
    assert "☐ 2" not in kb and "☐ 1" in kb
    token = kb["☐ 1"].split("|")[1]
    tg.updates = [tap(2, f"pk|{token}|all"), tap(3, f"pk|{token}|go")]
    run(db_session, bot, settings, queue, tg, store)
    assert [args[1] for _, args, _ in queue.jobs] == ["1" * 40]  # binh's commit never queued


def test_bare_review_opens_the_menu_and_mr_picker_lists_open_mrs(
    db_session: Session, bot: TelegramBot, world: World, settings: Settings, queue: FakeQueue
) -> None:
    link(db_session, world.lead, 777)
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
    # One repo in scope, but a lead may review several people: pick whose MRs first.
    tg.updates = [tap(2, menu["🔀 MR đang mở"])]
    run(db_session, bot, settings, queue, tg, store)
    who = buttons(tg.edited[-1]["markup"])
    assert "👥 Mọi người" in who and "An" in who and "Binh" in who and "Outsider" not in who
    tg.updates = [tap(20, who["👥 Mọi người"])]
    run(db_session, bot, settings, queue, tg, store)
    listed = tg.edited[-1]
    assert "<b>MR đang mở</b> · demo/p · 2 mục" in listed["text"]
    assert "!9 Fix auth" in listed["text"]
    assert "✅" in listed["text"]  # !7 already has a done review for its head
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
    link(db_session, world.lead, 777)
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
    link(db_session, world.lead, 777)
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
    assert "↻ Cho chọn mục đã review" not in kb  # re-rolling one's own score is a lead's call
    token = kb["☐ 1"].split("|")[1]
    tg.updates = [tap(2, f"pk|{token}|again")]
    run(db_session, bot, settings, queue, tg, store)
    assert "lead/admin" in tg.answered[-1]["text"]


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
    link(db_session, world.lead, 777)
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
    link(db_session, world.lead, 777)
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
