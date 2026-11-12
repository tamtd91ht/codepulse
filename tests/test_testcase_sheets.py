"""Testcase sheets and matching (docs/specs/30 §4, §5, §8.1, §9): header guessing, row mapping,
values, xlsx/csv in and out, globs, the review's testing hint."""

import io

import pytest
from openpyxl import Workbook, load_workbook

from app.models import TestCase
from app.review.testing_hint import is_logic, is_test
from app.review.testing_hint import testing_hint as hint_of
from app.testcases import sheets
from app.testcases.impact import Changed, glob_match, match


def _sheet(rows: list[list[str]]) -> sheets.Sheet:
    return sheets.guess(sheets.Sheet(name="Đăng nhập", rows=rows))


def test_header_found_below_a_title_and_columns_guessed() -> None:
    s = _sheet(
        [
            ["BỘ TESTCASE ĐĂNG NHẬP", "", "", "", "", "", "", ""],
            ["", "", "", "", "", "", "", ""],
            [
                "STT",
                "Mã TC",
                "Chức năng",
                "Mô tả testcase",
                "Điều kiện tiên quyết",
                "Các bước thực hiện",
                "Kết quả mong đợi",
                "Kết quả thực tế",
                "Độ ưu tiên",
                "Jira",
            ],
        ]
    )
    assert s.header_row == 2
    assert s.mapping == {
        "code": 1,
        "section": 2,
        "title": 3,
        "preconditions": 4,
        "steps": 5,
        "expected": 6,
        "priority": 8,
        "jira_keys": 9,
    }


@pytest.mark.parametrize(
    ("header", "field"),
    [
        ("Test Case ID", "code"),
        ("Test case", "title"),
        ("Expected Result", "expected"),
        ("Pre-condition", "preconditions"),
        ("Test Steps", "steps"),
        ("Test data", "test_data"),
        ("Priority", "priority"),
        ("Loại", "kind"),
        ("Nhãn", "tags"),
        ("Bộ testcase", None),
        ("Vùng code", "code_paths"),
        ("Automation", "kind"),
        ("Ghi chú", "notes"),
        ("Màn hình", "section"),
        ("Trạng thái", None),
        ("Người test", None),
        ("Actual result", None),
    ],
)
def test_header_words(header: str, field: str | None) -> None:
    assert sheets.field_of(header) == field


def test_rows_become_cases_with_merged_sections_and_values() -> None:
    rows = [
        ["ID", "Chức năng", "Tiêu đề", "Các bước", "Kết quả mong đợi", "Ưu tiên", "Loại", "Jira"],
        ["", "Đăng nhập", "", "", "", "", "", ""],  # a group row
        ["TC1", "", "Đăng nhập đúng", "1. Nhập\n2. Bấm", "Vào trang chủ", "Cao", "Auto", "OMX-12"],
        ["TC2", "", "Sai mật khẩu", "1. Nhập sai", "Báo lỗi", "P0", "Smoke", "xem OMX-13, OMX-12"],
        ["", "", "", "", "chỉ có kết quả", "", "", ""],  # no title: skipped
        ["", "Quên mật khẩu", "Gửi mail", "", "", "thấp", "", ""],
    ]
    mapping = sheets.guess(sheets.Sheet("x", rows)).mapping
    out = sheets.to_cases(rows, 0, mapping)
    assert out.skipped == 1
    assert [c["section"] for c in out.cases] == ["Đăng nhập", "Đăng nhập", "Quên mật khẩu"]
    first, second, third = out.cases
    assert first["code"] == "TC1" and first["priority"] == "high" and first["kind"] == "automated"
    assert first["steps"] == "1. Nhập\n2. Bấm" and first["jira_keys"] == ["OMX-12"]
    assert second["priority"] == "critical" and second["tags"] == ["Smoke"]
    assert second["kind"] == "manual" and second["jira_keys"] == ["OMX-13", "OMX-12"]
    assert third["priority"] == "low"


def test_without_a_title_column_import_is_refused() -> None:
    with pytest.raises(sheets.SheetError):
        sheets.to_cases([["a", "b"]], -1, {"code": 0})


def test_xlsx_with_links_and_csv_with_bom() -> None:
    book = Workbook()
    ws = book.active
    assert ws is not None
    ws.title = "Thanh toán"
    ws.append(["Mã", "Tiêu đề", "Kết quả mong đợi"])
    ws.append(["TC-1", "Thanh toán thẻ", "Thành công"])
    ws["B2"].hyperlink = "https://wiki.example.org/pay"
    buf = io.BytesIO()
    book.save(buf)
    [sheet] = sheets.read_file("tc.xlsx", buf.getvalue())
    assert sheet.name == "Thanh toán"
    assert sheet.rows[1][1] == "[Thanh toán thẻ](https://wiki.example.org/pay)"

    data = "Mã;Tiêu đề;Kết quả mong đợi\nTC-1;Hoàn tiền;OK\n".encode("utf-8-sig")
    [csv_sheet] = sheets.read_file("tc.csv", data)
    assert sheets.guess(csv_sheet).mapping == {"code": 0, "title": 1, "expected": 2}
    assert csv_sheet.rows[1] == ["TC-1", "Hoàn tiền", "OK"]

    with pytest.raises(sheets.SheetError):
        sheets.read_file("tc.pdf", b"%PDF")


def _case(**kw: object) -> TestCase:
    base: dict[str, object] = {
        "id": 1,
        "suite_id": 1,
        "code": "TC-1",
        "title": "Đăng nhập",
        "section": "Auth",
        "priority": "high",
        "kind": "manual",
        "status": "approved",
        "tags": ["smoke"],
        "jira_keys": [],
        "code_paths": [],
    }
    base.update(kw)
    return TestCase(**base)


def test_export_round_trips_and_never_writes_a_formula() -> None:
    cases = [_case(steps='=HYPERLINK("x")', expected="- vào trang chủ")]
    data = sheets.write_xlsx([("Đăng/nhập", cases)], {1: "!7: Jira OMX-1"})
    book = load_workbook(io.BytesIO(data))
    ws = book.worksheets[0]
    assert ws.title == "Đăng nhập"
    head = [c.value for c in ws[1]]
    assert head[:3] == ["Mã", "Chức năng", "Tiêu đề"] and "Kết quả thực tế" in head
    row = [c.value for c in ws[2]]
    # Written as text, never as a formula.
    assert row[4] == '=HYPERLINK("x")' and ws.cell(2, 5).data_type == "s"
    assert row[0] == "TC-1" and row[6] == "- vào trang chủ"
    assert row[7] == "Cao" and "!7: Jira OMX-1" in row
    csv_data = sheets.write_csv([("Auth", cases)])
    assert csv_data.startswith("﻿".encode()) and b"'=HYPERLINK" in csv_data


def test_export_then_import_keeps_every_field() -> None:
    case = _case(
        steps="1. Mở\n2. Bấm",
        expected="Vào",
        kind="automated",
        automation="tests/test_login.py::test_ok",
        tags=["smoke", "regression"],
        jira_keys=["OMX-1"],
        code_paths=["src/auth/**"],
    )
    for data, read in (
        (sheets.write_xlsx([("Auth", [case])], {1: "!7"}), sheets.read_xlsx),
        (sheets.write_csv([("Auth", [case])], {1: "!7"}), sheets.read_csv),
    ):
        [back] = read(data)
        sheets.guess(back)
        [got] = sheets.to_cases(back.rows, back.header_row, back.mapping).cases
        assert got["title"] == "Đăng nhập" and got["section"] == "Auth" and got["code"] == "TC-1"
        assert got["tags"] == ["smoke", "regression"] and got["kind"] == "automated"
        assert got["automation"] == "tests/test_login.py::test_ok"
        assert got["jira_keys"] == ["OMX-1"] and got["code_paths"] == ["src/auth/**"]
        assert got["priority"] == "high" and got["steps"] == "1. Mở\n2. Bấm"


def test_a_partial_sheet_sets_only_its_columns() -> None:
    rows = [["Mã", "Tiêu đề"], ["TC-1", "Đăng nhập"]]
    [got] = sheets.to_cases(rows, 0, {"code": 0, "title": 1}).cases
    assert got == {"code": "TC-1", "title": "Đăng nhập"}


def test_control_characters_and_wild_globs() -> None:
    from app.testcases import service as tc

    assert tc.clean({"title": "a\x01b\x00"})["title"] == "ab"
    with pytest.raises(tc.FieldError):
        tc.globs(["*a" * 13])
    assert tc.globs(["./src/***/x", ".github/**"]) == ["src/**/x", ".github/**"]


@pytest.mark.parametrize(
    ("pattern", "path", "hit"),
    [
        ("src/payment/**", "src/payment/a/b.ts", True),
        ("src/payment/**", "src/pay/a.ts", False),
        ("src/payment/", "src/payment/x.py", True),
        ("checkout*.ts", "web/app/checkout-page.ts", True),
        ("**/refund.py", "app/billing/refund.py", True),
        ("app/*.py", "app/billing/refund.py", False),
        ("", "a.py", False),
    ],
)
def test_globs(pattern: str, path: str, hit: bool) -> None:
    assert glob_match(pattern, path) is hit


def test_match_by_jira_glob_and_automation() -> None:
    cases = [
        _case(id=1, jira_keys=["OMX-1"]),
        _case(id=2, code_paths=["src/payment/**"]),
        _case(id=3, automation="tests/test_refund.py::test_full"),
        _case(id=4, code_paths=["src/other/**"]),
    ]
    found = match(
        cases,
        ["OMX-1"],
        [Changed("src/payment/card.ts", 10, 2), Changed("tests/test_refund.py", 5, 0)],
    )
    assert set(found) == {1, 2, 3}
    assert found[1] == ["Jira OMX-1"]
    assert found[2] == ["src/payment/card.ts khớp src/payment/**"]
    assert found[3] == ["đổi file test tests/test_refund.py"]


def _diff(n: int) -> str:
    return f"@@ -0,0 +1,{n} @@\n" + "".join(f"+line {i}\n" for i in range(n))


def test_testing_hint() -> None:
    assert is_test("tests/api/test_x.py") and is_test("web/a.test.tsx") and is_test("FooTest.java")
    assert is_logic("app/billing/refund.py") and not is_logic("migrations/versions/x.py")
    assert not is_logic("README.md") and not is_logic("web/next.config.js")

    block, info = hint_of([{"new_path": "app/billing/refund.py", "diff": _diff(30)}])
    assert "GỢI Ý KIỂM THỬ" in block and "app/billing/refund.py (+30)" in block
    assert info == {"source_files": 1, "test_files": 0, "added": 30, "hinted": True}

    with_test = [
        {"new_path": "app/billing/refund.py", "diff": _diff(30)},
        {"new_path": "tests/test_refund.py", "diff": _diff(10)},
    ]
    assert hint_of(with_test)[0] == ""
    assert hint_of([{"new_path": "app/a.py", "diff": _diff(5)}])[0] == ""  # too small
    assert hint_of([{"new_path": "docs/a.md", "diff": _diff(50)}])[0] == ""
