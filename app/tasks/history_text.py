"""A task history row as a readable Vietnamese sentence (docs/specs/31 §21.0, F1): the timeline
never shows a raw field name ("đổi created thành OCX-2")."""

from typing import Any

LABELS: dict[str, str] = {
    "title": "tiêu đề",
    "description": "mô tả",
    "type": "loại",
    "priority": "mức ưu tiên",
    "labels": "nhãn",
    "components": "thành phần",
    "story_points": "story point",
    "estimate_min": "ước lượng",
    "spent_min": "giờ đã làm",
    "due_date": "hạn",
    "sprint": "sprint",
    "version": "phiên bản",
    "custom": "trường tuỳ biến",
    "parent": "task cha",
    "resolution": "lý do đóng",
}


def _v(value: str | None) -> str:
    return value if value not in (None, "") else "—"


def describe(field: str, old: str | None, new: str | None) -> tuple[str, str]:
    """(event, sentence) for one history row; `event` is a stable key the UI may style."""
    if field == "created":
        return "created", "tạo task"
    if field == "status":
        if old:
            return "status", f"chuyển {old} → {_v(new)}"
        return "status", f"chuyển sang {_v(new)}"
    if field == "assignee":
        if new:
            return "assignee", f"giao cho {new}"
        return "assignee", f"bỏ giao (trước đó: {_v(old)})"
    if field == "description":
        return "description", "sửa mô tả"
    if field == "comment":
        return "comment", "bình luận" + (" (smart commit)" if new == "(smart commit)" else "")
    if field == "worklog":
        return "worklog", f"ghi giờ {_v(new)}"
    if field == "attachment":
        if new and not old:
            return "attachment", f"đính kèm {new}"
        return "attachment", f"xoá file {_v(old)}"
    if field == "link":
        if new and not old:
            return "link", f"liên kết {new}"
        return "link", f"gỡ liên kết {_v(old)}"
    if field in ("branch", "commit", "mr"):
        what = {"branch": "nhánh", "commit": "commit", "mr": "MR"}[field]
        if new and not old:
            return "git", f"nối {what} {new}"
        return "git", f"gỡ {what} {_v(old)}"
    if field == "testcase":
        if new and not old:
            return "test", f"gắn testcase {new}"
        return "test", f"bỏ testcase {_v(old)}"
    if field == "criterion":
        return "criterion", f"tiêu chí chấp nhận: {_v(new or old)}"
    if field == "blocked":
        return "blocked", _v(new)
    label = LABELS.get(field, field.replace("_", " "))
    if old in (None, ""):
        return field, f"đặt {label}: {_v(new)}"
    if new in (None, ""):
        return field, f"bỏ {label} (trước đó: {old})"
    return field, f"đổi {label}: {old} → {new}"


def describe_row(row: Any) -> tuple[str, str]:
    return describe(str(row.field), row.old, row.new)
