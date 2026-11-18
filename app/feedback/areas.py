"""The first level of the feedback tree (docs/specs/32 §4): CodePulse's areas, fixed so the tree
stays stable, each with the pages that suggest it and what it covers (told to the LLM)."""

import re
import unicodedata
from dataclasses import dataclass

from app.models.feedback import FEEDBACK_AREAS


@dataclass(frozen=True)
class Area:
    key: str
    label: str
    covers: str  # what the area holds, for the model
    pages: tuple[str, ...] = ()  # URL path prefixes that suggest it


AREAS: tuple[Area, ...] = (
    Area("overview", "Tổng quan & Của tôi", "trang tổng quan, trang cá nhân", ("/overview", "/me")),
    Area(
        "review",
        "Review & Finding",
        "AI code review, kết quả review, yêu cầu review, Critical Board, đóng finding, khiếu nại",
        ("/reviews", "/critical", "/closures", "/disputes"),
    ),
    Area(
        "tasks",
        "Task",
        "quản lý task: board, list, backlog, sprint, timeline, báo cáo task",
        ("/tasks",),
    ),
    Area("testcases", "Testcase", "testcase, test run, test plan, bug từ test", ("/testcases",)),
    Area("docs", "Tài liệu dự án", "tài liệu dự án, tổng hợp từ link, phiên bản", ("/docs",)),
    Area("dpi", "DPI hàng tháng", "DPI scorecard, đánh giá tháng, phân phối điểm", ("/dpi",)),
    Area(
        "reports",
        "Báo cáo & Thành viên",
        "báo cáo thành viên, đánh giá cá nhân, báo cáo MCP",
        ("/members", "/mcp-report"),
    ),
    Area(
        "insights",
        "Luồng làm việc & Team",
        "insights, luồng làm việc, team",
        ("/insights", "/teams"),
    ),
    Area("stetho", "Stetho (trợ lý)", "trợ lý AI Stetho, tri thức, lịch sử hỏi đáp", ("/stetho",)),
    Area(
        "admin",
        "Quản trị & Phân quyền",
        "admin, quản trị dự án, phân quyền, cấu hình, LLM, người & team",
        ("/admin", "/project-admin", "/settings"),
    ),
    Area(
        "integrations",
        "Tích hợp",
        "GitLab, webhook, MCP, Telegram, Jira, thông báo",
        ("/jira", "/notifications"),
    ),
    Area("general", "Chung / Khác", "giao diện chung, điều hướng, đăng nhập, hiệu năng, khác", ()),
)
BY_KEY: dict[str, Area] = {a.key: a for a in AREAS}
if tuple(BY_KEY) != FEEDBACK_AREAS:  # one list, two places (model check constraint)
    raise RuntimeError("feedback areas differ from the model's")

KIND_LABEL = {
    "bug": "Lỗi",
    "feature": "Đề xuất tính năng",
    "improvement": "Cải tiến",
    "question": "Câu hỏi",
}
STATUS_LABEL = {
    "new": "Mới",
    "accepted": "Đã nhận",
    "planned": "Đã lên kế hoạch",
    "done": "Đã xong",
    "rejected": "Từ chối",
}
PRIORITY_LABEL = {"p1": "P1 · Cao", "p2": "P2 · Vừa", "p3": "P3 · Thấp"}
NO_FEATURE = "Chưa phân loại"


def area_for_page(url: str | None) -> str:
    """The area a page suggests (the longest matching prefix), else general."""
    if not url:
        return "general"
    path = re.sub(r"^[a-z]+://[^/]+", "", url.strip(), flags=re.I).split("?", 1)[0]
    best, size = "general", 0
    for area in AREAS:
        for prefix in area.pages:
            if (path == prefix or path.startswith(prefix + "/")) and len(prefix) > size:
                best, size = area.key, len(prefix)
    return best


def slug(text: str, limit: int = 60) -> str:
    """ASCII, lower-case, dash-separated: a path segment of the virtual tree."""
    folded = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    ascii_ = "".join(c for c in folded if not unicodedata.combining(c))
    out = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    return out[:limit].rstrip("-") or "muc"


def tree_path(area: str, feature: str | None, item_id: int, title: str) -> str:
    """feedback/<area>/<feature>/<#id>-<slug>.md (docs/specs/32 §3)."""
    return f"feedback/{area}/{slug(feature or NO_FEATURE)}/{item_id}-{slug(title, 50)}.md"
