"""GỢI Ý KIỂM THỬ (docs/specs/30 §9): a change adding lines to source files without touching
any test file gets a short block in the review prompt, so the model may raise (at most) one
`testing` finding of severity `low` when the new logic is risky and untested. Not a scoring rule:
only words in the prompt.
"""

import re
from typing import Any

from app.review.prepare import parse_diff

SOURCE_EXT = frozenset(
    {
        "py", "ts", "tsx", "js", "jsx", "mjs", "cjs", "go", "java", "kt", "kts", "cs", "php",
        "rb", "rs", "swift", "dart", "scala", "c", "cc", "cpp", "h", "hpp", "m", "mm", "vue",
        "svelte", "ex", "exs", "lua",
    }
)  # fmt: skip
TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|specs?|testing|e2e|cypress|playwright|androidTest|testFixtures)/"
    r"|(^|/)test_[^/]+\.py$|_test\.(py|go|rb|exs?|dart)$|\.(test|spec|e2e)\.[a-z]+$"
    r"|(Test|Tests|Spec|IT)\.(java|kt|cs|scala|swift|php)$|_spec\.rb$",
    re.IGNORECASE,
)
NOT_LOGIC = re.compile(
    r"(^|/)(migrations?|alembic|db/migrate|generated|__generated__|vendor|node_modules|dist|build"
    r"|\.storybook|stories|docs?|i18n|locales?|assets|static|public)/"
    r"|\.(d\.ts|config\.[cm]?[jt]s|stories\.[jt]sx?)$|(^|/)(setup|conftest|manage)\.py$",
    re.IGNORECASE,
)
MIN_ADDED = 15  # fewer added lines of logic in all: too small to ask for tests
MAX_LISTED = 10


def is_test(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def is_logic(path: str) -> bool:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return ext in SOURCE_EXT and not is_test(path) and not NOT_LOGIC.search(path)


def testing_hint(entries: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """(the prompt block or "", what was seen for review.basis["tests"])."""
    tests = 0
    logic: list[tuple[str, int]] = []
    for entry in entries:
        path = entry.get("new_path") or entry.get("old_path") or ""
        if not path or not entry.get("diff"):
            continue
        if is_test(path):
            tests += 1
            continue
        if entry.get("deleted_file") or not is_logic(path):
            continue
        added = len(parse_diff(path, entry["diff"]).added_lines)
        if added:
            logic.append((path, added))
    total = sum(n for _, n in logic)
    info = {"source_files": len(logic), "test_files": tests, "added": total, "hinted": False}
    if tests or total < MIN_ADDED:
        return "", info
    logic.sort(key=lambda x: -x[1])
    listed = "\n".join(f"- {p} (+{n})" for p, n in logic[:MAX_LISTED])
    more = f"\n- … và {len(logic) - MAX_LISTED} file khác" if len(logic) > MAX_LISTED else ""
    info["hinted"] = True
    block = (
        "GỢI Ý KIỂM THỬ: thay đổi này thêm "
        f"{total} dòng vào {len(logic)} file mã nguồn nhưng không thêm/sửa file test tự động "
        f"nào:\n{listed}{more}\n"
        "Nếu trong đó có logic mới hoặc sửa logic có rủi ro thật (tính toán, điều kiện, xử lý "
        "lỗi, phân quyền, dữ liệu) mà không có test đi kèm, có thể nêu MỘT finding category "
        "`testing`, severity `low` (không cao hơn), chỉ ra phần logic nên có test. Không nêu "
        "finding này cho thay đổi nhỏ, hiển nhiên đúng, chỉ đổi giao diện/cấu hình/đặt tên, hoặc "
        "khi dự án rõ ràng không viết test tự động cho phần này."
    )
    return block, info
