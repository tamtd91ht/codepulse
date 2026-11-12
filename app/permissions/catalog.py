"""The permission catalog (docs/specs/21 §4): every right a permission set may hold, and what
the built-in sets hold by default. Adding a feature = adding a line here (and to its tests).

A permission is a `flag` (held or not) or `scoped`: held over own work, the teams one leads or
manages, the dự án one manages, or everything one can access (docs/specs/21 §2).
"""

from dataclasses import dataclass
from typing import Literal

Scope = Literal["own", "team", "project", "all"]
SCOPES: tuple[Scope, ...] = ("own", "team", "project", "all")
FLAG = "flag"  # the "scope" of a flag permission when held
RANK: dict[str, int] = {"own": 1, "team": 2, "project": 3, "all": 4, FLAG: 4}

AutoRole = Literal["member", "lead", "manager", "project_manager", "manage_all"]
AUTO_ROLES: tuple[AutoRole, ...] = ("member", "lead", "manager", "project_manager", "manage_all")


@dataclass(frozen=True)
class Permission:
    key: str
    group: str  # the feature, as the admin screen groups them
    label: str
    scopes: tuple[Scope, ...] = ()  # empty: a flag
    sensitive: bool = False  # touches one person's data: confirmed and audited when granted
    # Whether "team" also reaches the people of the teams one only manages (role manager), not
    # just those one leads: a manager sees team aggregates, never per-person data (Phase 0), so
    # only the close-request decisions a manager made before spec 21 say yes.
    managed_team: bool = False

    @property
    def is_flag(self) -> bool:
        return not self.scopes


def _p(
    key: str,
    group: str,
    label: str,
    scopes: str = "",
    sensitive: bool = False,
    managed_team: bool = False,
) -> Permission:
    return Permission(
        key,
        group,
        label,
        tuple(scopes.split()) if scopes else (),  # type: ignore[arg-type]
        sensitive,
        managed_team,
    )


CATALOG: tuple[Permission, ...] = (
    _p("overview.view", "Tổng quan", "Xem Tổng quan dự án"),
    _p("insights.view", "Luồng làm việc", "Xem Luồng làm việc"),
    _p("members.view", "Thành viên", "Xem trang Thành viên"),
    _p("evaluation.view", "Thành viên", "Xem đánh giá từng người", "own team project all", True),
    _p("metrics.person", "Thành viên", "Xem điểm tuần lưu sẵn của một người", "own team all", True),
    _p(
        "metrics.team",
        "Thành viên",
        "Xem chỉ số team, hotspot, bảng xếp hạng, báo cáo",
        "own team all",
        managed_team=True,
    ),
    # docs/specs/23 §9: the member report (overview of many people, one person's detail) and its
    # Excel export. The scope says whose numbers one sees; export needs the view as well.
    _p(
        "report.member",
        "Báo cáo thành viên",
        "Xem báo cáo thành viên (tổng quan và chi tiết)",
        "own team project all",
        True,
    ),
    _p("report.export", "Báo cáo thành viên", "Xuất Excel báo cáo thành viên"),
    # docs/specs/26 §13.2: the Claude Code (MCP) report, scoped like `report.member`; its call
    # log is admins' only. A coaching signal, never part of DPI or review scores.
    _p(
        "report.mcp",
        "Tích hợp MCP",
        "Xem báo cáo tích hợp MCP (Claude Code) và xuất Excel",
        "own team project all",
        True,
    ),
    _p("review.view", "Review", "Xem kết quả review và finding", "own team project all"),
    _p("review.request", "Review", "Yêu cầu review commit/MR", "own project all"),
    _p("review.rerun", "Review", "Chạy lại review (tốn ngân sách LLM)", "project all"),
    _p("review.author", "Review", "Gắn tác giả cho review", "project all"),
    _p("finding.close_request", "Đóng finding", "Gửi yêu cầu đóng finding của mình", "own"),
    _p(
        "finding.close_decide",
        "Đóng finding",
        "Chạy review / từ chối / xác nhận yêu cầu đóng",
        "team project all",
        managed_team=True,
    ),
    _p(
        "finding.lower_far",
        "Đóng finding",
        "Hạ critical thẳng xuống low",
        "team project all",
        managed_team=True,
    ),
    _p("finding.close_manual", "Đóng finding", "Đóng / mở lại finding thủ công", "all"),
    _p("dispute.raise", "Khiếu nại", "Khiếu nại finding của mình", "own"),
    _p("dispute.decide", "Khiếu nại", "Duyệt khiếu nại", "team project all"),
    _p("dispute.ai_check", "Khiếu nại", "Nhờ AI phân tích khiếu nại", "team project all"),
    _p("evidence.view", "Hồ sơ tháng", "Xem hồ sơ tháng", "own team all", True),
    _p("evidence.edit_self", "Hồ sơ tháng", "Viết phần của mình trong hồ sơ", "own"),
    _p("evidence.lead", "Hồ sơ tháng", "Nhận xét, chốt, bản nháp AI của hồ sơ", "team all", True),
    # docs/specs/25 §7: the monthly DPI scorecard and its cycle.
    _p("dpi.view", "DPI", "Xem scorecard DPI", "own team project all", True),
    _p("dpi.self", "DPI", "Tự khai, gửi bước tự đánh giá", "own"),
    _p("dpi.dispute", "DPI", "Khiếu nại một chỉ số DPI của mình", "own"),
    _p("dpi.assess", "DPI", "Chấm 30 điểm quản lý, duyệt tự khai", "team project all", True),
    _p("dpi.distribution", "DPI", "Xem phân phối điểm DPI (không theo từng người)", "team all"),
    _p("dpi.calibrate", "DPI", "Hiệu chỉnh điểm DPI từng người", "team project all", True),
    _p("dpi.final_view", "DPI", "Xem scorecard DPI đã chốt (HR)", sensitive=True),
    _p("dpi.defects", "DPI", "Xác nhận RCA lỗi production", "team project all"),
    _p("dpi.admin", "DPI", "Cấu hình rubric, profile, level, cờ chạy thử"),
    # docs/specs/27 §11: the Jira connection (URL, token), Jira projects, field mapping, the
    # DPI task source and "sync now". No built-in set holds it: admins, or a set granting it.
    _p(
        "jira.admin",
        "Tích hợp Jira",
        "Cấu hình kết nối Jira (URL, token), dự án Jira, quy đổi trường, nguồn task DPI",
    ),
    # docs/specs/29: Tài liệu dự án. Reading covers the dự án one sees; editing: the dự án one
    # manages (project) or every dự án one sees (all).
    _p("docs.view", "Tài liệu dự án", "Xem tài liệu dự án"),
    _p(
        "docs.edit",
        "Tài liệu dự án",
        "Thêm, sửa, tải phiên bản mới, lưu trữ tài liệu dự án",
        "project all",
    ),
    # docs/specs/30 §2 (user 2026-11-12: "bổ sung module testcase vào bộ quyền"), roles as in
    # TestRail: who designs cases, who runs tests, who leads the testing.
    _p("tests.view", "Testcase", "Xem testcase, test run, test plan, milestone"),
    _p(
        "tests.edit",
        "Testcase",
        "Thiết kế testcase: thêm, sửa, nhập, thư mục, bước dùng chung, AI sinh / rà soát",
        "project all",
    ),
    # §15.9 (G7): deleting and approving are their own rights.
    _p(
        "tests.delete",
        "Testcase",
        "Xoá testcase, bộ testcase, thư mục",
        "project all",
    ),
    _p(
        "tests.approve",
        "Testcase",
        "Duyệt / từ chối testcase khi dự án bật quy trình duyệt",
        "project all",
    ),
    _p(
        "tests.run",
        "Testcase",
        "Ghi kết quả test (Passed / Failed / Blocked / Retest) cho mọi test của dự án",
        "project all",
    ),
    _p(
        "tests.manage",
        "Testcase",
        "Tạo, sửa, đóng, xoá test run / test plan, milestone, cấu hình; phân tích MR bằng AI",
        "project all",
    ),
    _p("knowledge.view", "Tiền đề review", "Xem Tiền đề review của repo"),
    _p(
        "knowledge.edit",
        "Tiền đề review",
        "Làm mới, duyệt, sửa Tiền đề review và hồ sơ dự án",
        "project all",
    ),
    _p("knowledge.shared", "Tiền đề review", "Repo .claude dùng chung và làm mới hồ sơ dự án"),
    # docs/specs/22 §5: making the sets is an admin's; giving them to a dự án is its manager's.
    _p("criteria.manage", "Bộ tiêu chí", "Tạo, sửa, nhân bản, lưu trữ bộ tiêu chí"),
    _p("criteria.assign", "Bộ tiêu chí", "Gán bộ tiêu chí cho dự án, tính lại điểm", "project all"),
    _p("gitlab_members.import", "Người & Team", "Tra cứu, nhập người từ GitLab"),
    _p("telegram.self", "Telegram", "Liên kết Telegram của mình"),
    _p("assistant.use", "Trợ lý", "Dùng trợ lý Stetho"),
    # docs/specs/28: Stetho's knowledge base (documents, saved answers, web lookup). No built-in
    # set holds it: admins, or a set an admin grants it in.
    _p(
        "assistant.train",
        "Trợ lý",
        "Dạy Stetho: nhập tài liệu (file, link), lưu câu trả lời làm tri thức, bật tra web",
    ),
    _p("admin.connections", "Quản trị", "Kết nối GitLab"),
    _p(
        "admin.projects",
        "Quản trị",
        "Dự án và repo: tạo, gom, theo dõi, sync, webhook, cấp quyền dự án",
    ),
    _p("admin.people", "Quản trị", "Người, team, tài khoản Git", sensitive=True),
    _p("admin.llm", "Quản trị", "LLM, token, chi phí"),
    _p("admin.review_settings", "Quản trị", "Cấu hình review"),
    _p("admin.insights", "Quản trị", "Đồng bộ Insights, cấu trúc repo bằng AI"),
    _p("admin.notifications", "Quản trị", "Webhook, bot Telegram"),
    _p("admin.commands", "Quản trị", "Chạy command .claude"),
    _p("admin.purge", "Quản trị", "Xoá dữ liệu"),
    _p("admin.permissions", "Quản trị", "Tạo, sửa, gán bộ quyền", sensitive=True),
)
BY_KEY: dict[str, Permission] = {p.key: p for p in CATALOG}


@dataclass(frozen=True)
class BuiltinSet:
    auto_role: AutoRole
    name: str
    description: str
    items: dict[str, str]  # key -> scope, or FLAG


# docs/specs/21 §4 "Mặc định": the behaviour before spec 21, one set per role.
BUILTIN_SETS: tuple[BuiltinSet, ...] = (
    BuiltinSet(
        "member",
        "Thành viên",
        "Mọi người đang hoạt động: việc của chính mình.",
        {
            "overview.view": FLAG,
            "evaluation.view": "own",
            "report.member": "own",
            "report.mcp": "own",
            "report.export": FLAG,
            "metrics.person": "own",
            "review.view": "own",
            "review.request": "own",
            "metrics.team": "own",
            "finding.close_request": "own",
            "dispute.raise": "own",
            "evidence.view": "own",
            "evidence.edit_self": "own",
            "dpi.view": "own",
            "dpi.self": "own",
            "dpi.dispute": "own",
            "knowledge.view": FLAG,
            "telegram.self": FLAG,
            "docs.view": FLAG,
            "tests.view": FLAG,
        },
    ),
    BuiltinSet(
        "lead",
        "Lead",
        "Lead của một team: thấy và duyệt việc của người trong team mình.",
        {
            "insights.view": FLAG,
            "members.view": FLAG,
            "evaluation.view": "team",
            "report.member": "team",
            "report.mcp": "team",
            "report.export": FLAG,
            "metrics.person": "team",
            "metrics.team": "team",
            "review.view": "team",
            "finding.close_decide": "team",
            "dispute.decide": "team",
            "dispute.ai_check": "team",
            "evidence.view": "team",
            "evidence.lead": "team",
            "dpi.view": "team",
            "dpi.assess": "team",
            "dpi.defects": "team",
        },
    ),
    BuiltinSet(
        "manager",
        "Manager",
        "Manager của team (và team con): chỉ số tổng hợp của team, duyệt yêu cầu đóng finding.",
        {
            "insights.view": FLAG,
            "members.view": FLAG,
            "metrics.team": "team",
            "finding.close_decide": "team",
            "finding.lower_far": "team",
            "assistant.use": FLAG,
            "dpi.distribution": "team",
        },
    ),
    BuiltinSet(
        "project_manager",
        "Quản lý dự án",
        "Có quyền manage ở dự án: thấy và xử lý việc của mọi người trong dự án đó.",
        {
            "insights.view": FLAG,
            "members.view": FLAG,
            "evaluation.view": "project",
            "report.member": "project",
            "report.mcp": "project",
            "report.export": FLAG,
            "review.view": "project",
            "review.request": "project",
            "review.rerun": "project",
            "review.author": "project",
            "knowledge.edit": "project",
            "criteria.assign": "project",
            "dpi.view": "project",
            "dpi.defects": "project",
            "docs.view": FLAG,
            "docs.edit": "project",
            "tests.view": FLAG,
            "tests.edit": "project",
            "tests.delete": "project",
            "tests.approve": "project",
            "tests.run": "project",
            "tests.manage": "project",
        },
    ),
    BuiltinSet(
        "manage_all",
        "Quản lý tổng",
        "Quản lý mọi dự án (không phải admin: không vào màn Quản trị).",
        {
            "insights.view": FLAG,
            "members.view": FLAG,
            "evaluation.view": "all",
            "report.member": "all",
            "report.mcp": "all",
            "report.export": FLAG,
            "review.view": "all",
            "review.request": "all",
            "review.rerun": "all",
            "review.author": "all",
            "knowledge.edit": "all",
            "criteria.assign": "all",
            "dpi.view": "all",
            "dpi.distribution": "all",
            "dpi.calibrate": "all",
            "dpi.defects": "all",
            "docs.view": FLAG,
            "docs.edit": "all",
            "tests.view": FLAG,
            "tests.edit": "all",
            "tests.delete": "all",
            "tests.approve": "all",
            "tests.run": "all",
            "tests.manage": "all",
        },
    ),
)


def valid_item(key: str, scope: str) -> bool:
    perm = BY_KEY.get(key)
    if perm is None:
        return False
    return scope == FLAG if perm.is_flag else scope in perm.scopes


def merge(items: list[tuple[str, str]]) -> dict[str, str]:
    """The union of set items: each permission at its widest scope; unknown or invalid items
    (a permission removed from the catalog) are dropped."""
    out: dict[str, str] = {}
    for key, scope in items:
        if not valid_item(key, scope):
            continue
        if key not in out or RANK[scope] > RANK[out[key]]:
            out[key] = scope
    return out


def everything() -> dict[str, str]:
    """An admin's permissions: all of them, at their widest."""
    return {p.key: FLAG if p.is_flag else p.scopes[-1] for p in CATALOG}
