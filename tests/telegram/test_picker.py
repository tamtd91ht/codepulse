import json

import pytest

from app.telegram.picker import (
    MAX_ITEMS,
    PAGE_SIZE,
    Item,
    MemoryPickerStore,
    PickerState,
    decode,
    encode,
    keyboard,
    menu_keyboard,
    new_token,
    render_text,
    repo_keyboard,
)


def item(i: int, state: str = "open", kind: str = "commit") -> Item:
    return Item(
        kind="commit" if kind == "commit" else "mr",
        project_id=1,
        ref=f"{i:040x}" if kind == "commit" else str(i),
        sha=f"{i:040x}",
        label=f"demo/p {i:08x}",
        title=f"feat: change {i} <b>",
        author="An",
        author_user_id=5,
        when="2 giờ trước",
        state=state,
        result="APPROVE 82" if state == "done" else "",
    )


def state(n: int = 3, **states: str) -> PickerState:
    items = [item(i, states.get(f"s{i}", "open")) for i in range(n)]
    return PickerState(
        token=new_token(),
        owner_from_id=777,
        user_id=5,
        chat_id=-100,
        message_id=None,
        kind="commit",
        scope_label="demo/p · nhánh main · 7 ngày",
        items=items,
        raw="/review type=commit",
    )


def test_callback_data_round_trips_and_stays_short() -> None:
    token = new_token()
    assert len(token) == 8
    data = encode("pk", token, "t", 12)
    assert len(data.encode()) < 64
    action = decode(data)
    assert action is not None and (action.kind, action.token, action.op, action.arg) == (
        "pick",
        token,
        "t",
        "12",
    )
    assert decode("mn|commit") is not None and decode("mn|commit").kind == "menu"  # type: ignore[union-attr]
    assert decode("rp||mr|7").arg == "7"  # type: ignore[union-attr]
    assert decode("junk|x") is None and decode("") is None
    with pytest.raises(ValueError):
        encode("pk", "x" * 70)


def test_render_escapes_and_marks_states() -> None:
    st = state(4, s1="done", s2="running", s3="blocked")
    st.selected = [0]
    text = render_text(st)
    assert "<b>Commit gần đây</b>" in text and "4 mục" in text
    assert "☑ <b>1.</b> feat: change 0 &lt;b&gt; · An · 2 giờ trước" in text
    assert "✅ <b>2.</b>" in text and "đã review: APPROVE 82" in text
    assert "⏳ <b>3.</b>" in text and "đang review" in text
    assert "🚫 <b>4.</b>" in text and "ngoài quyền review" in text
    assert "đã chọn 1" in text


def test_keyboard_has_toggles_for_selectable_items_and_controls() -> None:
    st = state(3, s1="done")
    st.may_force = True  # a lead: may tick done items after the toggle
    st.selected = [2]
    kb = keyboard(st)["inline_keyboard"]
    toggles = [b for row in kb for b in row if "|t|" in b["callback_data"]]
    assert [b["text"] for b in toggles] == ["☐ 1", "☑ 3"]  # item 2 is done: no toggle
    # A done item exists: one button lets the owner tick it anyway (a new review round).
    assert [b["text"] for row in kb for b in row if b["callback_data"].endswith("|again")] == [
        "↻ Cho chọn mục đã review"
    ]
    st.again = True
    again = [b["text"] for row in keyboard(st)["inline_keyboard"] for b in row]
    assert "☐ 2" in again and "↩ Chỉ mục mới" in again
    assert toggles[0]["callback_data"] == encode("pk", st.token, "t", 0)
    controls, nav = kb[-3], kb[-1]
    assert controls[1]["text"] == "▶ Review đã chọn (1)"
    assert nav[-1]["text"] == "✖ Huỷ" and len(nav) == 1  # one page: no paging buttons


def test_paging_and_item_cap() -> None:
    st = state(PAGE_SIZE * 2 + 1)
    assert st.pages == 3 and [i for i, _ in st.page_items()] == list(range(PAGE_SIZE))
    st.page = 1
    assert render_text(st).splitlines()[0].endswith("trang 2/3")
    nav = keyboard(st)["inline_keyboard"][-1]
    assert [b["text"] for b in nav] == ["◀ Trước", "Sau ▶", "✖ Huỷ"]
    assert MAX_ITEMS == 40


def test_menu_and_repo_keyboards() -> None:
    menu = menu_keyboard()["inline_keyboard"]
    assert [b["callback_data"] for b in menu[0]] == ["mn||commit", "mn||mr"]
    repos = repo_keyboard("mr", [(1, "vihat/omicrm/api"), (2, "vihat/omicrm/web")])[
        "inline_keyboard"
    ]
    assert [row[0]["text"] for row in repos[:2]] == ["api", "web"]
    assert repos[0][0]["callback_data"] == "rp||mr|1"


def test_people_keyboard_offers_everyone_then_people() -> None:
    from app.telegram.picker import people_keyboard

    kb = people_keyboard("commit", 7, [(1, "An"), (2, "Bình"), (3, "Chi")], more=2)[
        "inline_keyboard"
    ]
    assert kb[0][0]["text"] == "👥 Mọi người" and kb[0][0]["callback_data"] == "us||commit|7:0"
    assert [b["text"] for b in kb[1]] == ["An", "Bình"] and kb[1][1][
        "callback_data"
    ] == "us||commit|7:2"
    assert kb[-2][0]["text"].startswith("… còn 2 người")
    action = decode("us||commit|7:2")
    assert action is not None and (action.kind, action.op, action.arg) == (
        "person",
        "commit",
        "7:2",
    )
    st = state(1)
    st.project_id = 7
    assert any(b["text"] == "👤 Người" for row in keyboard(st)["inline_keyboard"] for b in row)


def test_memory_store_round_trips_and_expires() -> None:
    now = [100.0]
    store = MemoryPickerStore(clock=lambda: now[0], ttl_s=60)
    st = state(2)
    st.selected = [1]
    store.save(st)
    loaded = store.load(st.token)
    assert (
        loaded is not None and loaded.selected == [1] and loaded.items[1].title == st.items[1].title
    )
    assert json.loads(st.to_json())["kind"] == "commit"
    now[0] += 61
    assert store.load(st.token) is None
    store.save(st)
    store.delete(st.token)
    assert store.load(st.token) is None
