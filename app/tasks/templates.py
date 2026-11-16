"""Project templates and board modes (docs/specs/31 §19.6).

A template is applied once, when Task is first turned on for a dự án: it gives the workflow
(statuses and which of them the git rules use), the task types, rule flags and the board mode.
Everything stays editable afterwards in Task > Cài đặt. Pure data: `service.enable_space` seeds
from it, so this module imports nothing from the service.
"""

from dataclasses import dataclass, field

from fastapi import HTTPException

from app.models.tasks import BOARD_MODES, TASK_TYPES

DEFAULT_TEMPLATE = "scrum"


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    # (name, category) in board order.
    statuses: tuple[tuple[str, str], ...]
    # Status names the git rules move tasks to; None: the rule has no status.
    start: str | None
    review: str | None
    done: str
    types: tuple[str, ...]
    board_mode: str
    # Overrides of the default rule flags.
    flags: dict[str, bool] = field(default_factory=dict)


TEMPLATES: dict[str, Template] = {
    "scrum": Template(
        key="scrum",
        name="Scrum",
        description="Làm theo sprint: Backlog, sprint, Board của sprint đang chạy.",
        statuses=(
            ("Cần làm", "todo"),
            ("Đang làm", "doing"),
            ("Đang review", "doing"),
            ("Xong", "done"),
        ),
        start="Đang làm",
        review="Đang review",
        done="Xong",
        types=TASK_TYPES,
        board_mode="scrum",
    ),
    "kanban": Template(
        key="kanban",
        name="Kanban",
        description="Luồng liên tục, không sprint: Board hiện mọi task đang mở.",
        statuses=(("Cần làm", "todo"), ("Đang làm", "doing"), ("Xong", "done")),
        start="Đang làm",
        review=None,
        done="Xong",
        types=("task", "bug", "subtask"),
        board_mode="kanban",
        # No review status: opening an MR leaves the task in Đang làm.
        flags={"review_on_mr": False},
    ),
    "qa": Template(
        key="qa",
        name="QA / xử lý lỗi",
        description="Theo dõi bug: xác minh, sửa, tester đóng sau khi kiểm lại.",
        statuses=(
            ("Mới", "todo"),
            ("Đang xác minh", "doing"),
            ("Đã sửa", "doing"),
            ("Đã đóng", "done"),
        ),
        start="Đang xác minh",
        review="Đã sửa",
        done="Đã đóng",
        types=("bug", "task", "subtask"),
        board_mode="scrum",
        # An MR moves the bug to Đã sửa; the tester closes it after retesting, not the merge.
        flags={"review_on_mr": True, "done_on_merge": False},
    ),
}


def template_or_422(key: str | None) -> Template:
    template = TEMPLATES.get(key or DEFAULT_TEMPLATE)
    if template is None:
        raise HTTPException(422, f"mẫu không biết: {key}")
    return template


def check_board_mode(mode: str) -> str:
    if mode not in BOARD_MODES:
        raise HTTPException(422, f"chế độ bảng không hợp lệ: {mode}")
    return mode
