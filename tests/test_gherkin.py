"""Gherkin of BDD cases (docs/specs/30 §15.12, G8), exploratory sessions and parameter tables
(§15.15, G13): parsing, errors with their line, the steps a run shows, Examples as parameters,
writing a .feature back."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from app.testcases import exploratory, gherkin, params

FEATURE = '''# language: en
@web @auth
Feature: Đăng nhập
  Người dùng đăng nhập bằng email.

  Background:
    Given trang đăng nhập đang mở

  # the happy path
  @C12 @smoke
  Scenario: Đăng nhập đúng
    When nhập email "a@x.vn" và mật khẩu đúng
    And bấm Đăng nhập
    Then vào trang chủ
    And thấy tên người dùng

  Scenario Outline: Sai mật khẩu
    When nhập email <email> và mật khẩu <pw>
    Then hiện "<message>"

    Examples: tiếng Việt
      | email   | pw  | message      |
      | a@x.vn  | 1   | Sai mật khẩu |
    @extra
    Examples:
      | email  | pw |
      | b@x.vn | 2  |

  Scenario: Nhập bảng và docstring
    Given các tài khoản:
      | email  | role  |
      | a@x.vn | admin |
      | b\\|c  | user  |
    When gửi yêu cầu:
      """json
      {"email": "a@x.vn"}
        indented
      """
    Then trả về 200
'''


def test_parse_feature_background_tags_tables_docstrings() -> None:
    f = gherkin.parse(FEATURE)
    assert f.name == "Đăng nhập" and f.line == 3 and f.tags == ["web", "auth"]
    assert f.description == "Người dùng đăng nhập bằng email."
    assert f.background is not None and [s.text for s in f.background.steps] == [
        "trang đăng nhập đang mở"
    ]
    one, outline, tables = f.scenarios
    assert one.name == "Đăng nhập đúng" and one.tags == ["C12", "smoke"] and not one.outline
    assert [s.kind for s in one.steps] == ["when", "when", "then", "then"]
    assert one.line == 11 and one.end_line == 15
    assert outline.outline and len(outline.examples) == 2
    assert outline.examples[0].header == ["email", "pw", "message"]
    assert outline.examples[1].tags == ["extra"]
    given, when, _ = tables.steps
    assert given.table == [["email", "role"], ["a@x.vn", "admin"], ["b|c", "user"]]
    assert when.docstring == '{"email": "a@x.vn"}\n  indented'


def test_outline_examples_become_parameters() -> None:
    f = gherkin.parse(FEATURE)
    table = gherkin.outline_params(f.scenarios[1].examples)
    assert table == {
        "columns": ["email", "pw", "message"],
        "rows": [["a@x.vn", "1", "Sai mật khẩu"], ["b@x.vn", "2", ""]],
    }
    assert params.clean(table) == table
    # A header with a blank is a column with "_", and <user name> fills from it.
    text = (
        "Scenario Outline: x\n  When <user name>\n  Then ok\n"
        "  Examples:\n    | user name |\n    | An |"
    )
    case = gherkin.check_case(text)
    got = gherkin.outline_params(case.scenarios[0].examples)
    assert got == {"columns": ["user_name"], "rows": [["An"]]}
    assert params.variables("<user name>", outline=True) == {"user_name"}
    assert params.fill("When <user name>", {"user_name": "An"}, outline=True) == "When An"


@pytest.mark.parametrize(
    ("text", "line", "words"),
    [
        # A free line right under Scenario is its description (Gherkin); after a step it is not.
        ("Feature: a\n  Scenario: b\n    Given x\n    Thn y\n", 4, "không hiểu dòng"),
        ("Scenario: a\n  Given x\n  | a | b |\n  | c |\n", 4, "2 cột"),
        ('Scenario: a\n  Given x\n  """\n  never closed\n', 3, "chưa đóng"),
        ("Scenario Outline: a\n  Given <x>\n", 1, "Examples"),
        ("Scenario Outline: a\n  Given <y>\n  Examples:\n    | x |\n    | 1 |\n", 2, "<y>"),
        ("Scenario: a\n  Given x\n  Examples:\n    | x |\n", 3, "Scenario Outline"),
        ("Given x\n", 1, "nằm trong Scenario"),
        ("Scenario: a\n  Given x\n@tag\n", 3, "tag"),
        ("Scenario: a\n  Given x\nScenario: b\n  Given y\nBackground:\n  Given z", 5, "Background"),
        ("Feature: a\nFeature: b\n", 2, "một Feature"),
        ("Scenario: a\n  | x |\n", 2, "bảng"),
        ("Scenario: a\n  Given x\n  @bad tag!\n", 3, "tag"),
        ("Rule: a\n", 1, "Rule"),
        ("Scenario: a\n  Given\n", 2, "chưa có nội dung"),
    ],
)
def test_errors_name_their_line(text: str, line: int, words: str) -> None:
    with pytest.raises(gherkin.GherkinError) as err:
        gherkin.parse(text)
    assert err.value.line == line and words in err.value.message
    assert str(err.value).startswith(f"dòng {line}:")


def test_a_case_holds_one_scenario() -> None:
    gherkin.check_case("Background:\n  Given x\n\nScenario: a\n  When y\n  Then z")
    with pytest.raises(gherkin.GherkinError, match="đúng một Scenario"):
        gherkin.check_case("Scenario: a\n  Given x\nScenario: b\n  Given y")
    with pytest.raises(gherkin.GherkinError, match="đúng một Scenario"):
        gherkin.check_case("# only a comment")
    with pytest.raises(gherkin.GherkinError, match="Feature"):
        gherkin.check_case("Feature: f\n  Scenario: a\n    Given x")


def test_scenario_steps_rule() -> None:
    text = (
        "Background:\n  Given đã đăng nhập\n\n"
        "Scenario: a\n"
        "  Then trang trống\n"  # a Then with no When before: a step of its own
        "  When bấm Thêm\n"
        "  Then hiện form\n"
        "  And nút Lưu mờ\n"  # continues the Then: same expected
        "  When nhập tên\n"
        "  And bấm Lưu\n"  # a When too: takes the next Then
        "  Then lưu xong\n"
        "  Given dữ liệu khác\n"
        "  Then không đổi\n"  # after a Given: its own step
        "  * ghi chú\n"  # continues a Then: its own step (no When before)
    )
    assert gherkin.scenario_steps(text) == [
        {"content": "Given đã đăng nhập", "expected": ""},
        {"content": "Then trang trống", "expected": ""},
        {"content": "When bấm Thêm", "expected": "Then hiện form\nAnd nút Lưu mờ"},
        {"content": "When nhập tên", "expected": ""},
        {"content": "And bấm Lưu", "expected": "Then lưu xong"},
        {"content": "Given dữ liệu khác", "expected": ""},
        {"content": "Then không đổi", "expected": ""},
        {"content": "* ghi chú", "expected": ""},
    ]
    assert gherkin.scenario_steps("Scenario: broken\n  Given x\n  Thn y") == []
    assert gherkin.scenario_steps(None) == []
    with_table = gherkin.scenario_steps('Scenario: t\n  When gửi:\n    """\n    {}\n    """\n')
    assert with_table == [{"content": 'When gửi:\n"""\n{}\n"""', "expected": ""}]


def test_feature_written_back_parses_to_the_same_cases() -> None:
    f = gherkin.parse(FEATURE)
    cases = [gherkin.case_text(f, sc) for sc in f.scenarios]
    assert cases[0].startswith("Background:\n  Given trang đăng nhập đang mở\n\nScenario: Đăng")
    written = gherkin.format_feature(
        "Đăng nhập",
        "Người dùng đăng nhập bằng email.",
        [(["C1", "smoke"], cases[0], "a"), (["C2"], cases[1], "b"), ([], cases[2], "c")],
    )
    back = gherkin.parse(written)
    assert back.name == "Đăng nhập" and back.description == "Người dùng đăng nhập bằng email."
    assert [sc.tags for sc in back.scenarios] == [["C1", "smoke"], ["C2"], []]
    assert [gherkin.case_text(back, sc) for sc in back.scenarios] == cases


def test_feature_with_different_backgrounds_inlines_them() -> None:
    one = "Background:\n  Given A\n\nScenario: x\n  When y\n  Then z"
    two = "Scenario: q\n  When r\n  Then s"
    written = gherkin.format_feature(
        "F", None, [(["C1"], one, "x"), (["C2"], two, "q"), ([], "", "Trống")]
    )
    back = gherkin.parse(written)
    assert back.background is None
    assert [s.text for s in back.scenarios[0].steps] == ["A", "y", "z"]
    assert back.scenarios[2].name == "Trống" and not back.scenarios[2].steps


def test_exploratory_session_and_summary() -> None:
    t0 = datetime(2026, 11, 12, 9, tzinfo=UTC)
    state = exploratory.act({}, "start", t0)
    assert state["running"] and state["elapsed_s"] == 0
    with pytest.raises(exploratory.SessionError):
        exploratory.act(state, "start", t0)
    state = exploratory.add_note(
        state, "bug", "Nút Lưu không phản hồi", 7, t0 + timedelta(seconds=75)
    )
    state = exploratory.act(state, "pause", t0 + timedelta(minutes=5))
    assert state["elapsed_s"] == 300 and not state["running"]
    with pytest.raises(exploratory.SessionError):
        exploratory.act(state, "pause", t0 + timedelta(minutes=6))
    state = exploratory.act(state, "resume", t0 + timedelta(minutes=10))
    state = exploratory.add_note(state, "idea", "Thêm phím tắt", 7, t0 + timedelta(minutes=11))
    assert [n["offset_s"] for n in state["notes"]] == [75, 360]
    with pytest.raises(ValueError):
        exploratory.add_note(state, "rant", "x", 7, t0)
    with pytest.raises(ValueError):
        exploratory.add_note(state, "note", "x" * (exploratory.NOTE_MAX + 1), 7, t0)
    comment, seconds = exploratory.session_summary(state, t0 + timedelta(minutes=12))
    assert seconds == 420
    assert comment == (
        "Phiên exploratory (7m):\n[01:15] Bug: Nút Lưu không phản hồi\n"
        "[06:00] Ý tưởng: Thêm phím tắt"
    )
    assert exploratory.session_summary({}) == ("", None)
    with pytest.raises(exploratory.SessionError):
        exploratory.add_note({}, "note", "x", 7, t0)


def test_a_case_has_no_description_lines() -> None:
    with pytest.raises(gherkin.GherkinError) as err:
        gherkin.check_case("Scenario: a\n  Givn x\n  When y")
    assert err.value.line == 2
    # From a .feature, a scenario's description is kept as a comment of the case.
    f = gherkin.parse("Feature: F\n  Scenario: a\n    Vì sao có nó\n    Given x\n")
    text = gherkin.case_text(f, f.scenarios[0])
    assert text == "Scenario: a\n  # Vì sao có nó\n  Given x"
    gherkin.check_case(text)


def test_long_descriptions_parse_in_linear_time() -> None:
    # Descriptions are joined once (they used to be rebuilt per line: quadratic).
    n = gherkin.LINES_MAX // 2 - 10
    desc = "\n".join(f"  dòng mô tả số {i}" for i in range(n))
    text = f"Feature: F\n{desc}\n  Scenario: a\n{desc}\n    Given x\n"
    t0 = time.perf_counter()
    f = gherkin.parse(text)
    assert time.perf_counter() - t0 < 1.0
    assert f.description.count("\n") == n - 1 and f.description.startswith("dòng mô tả số 0\n")
    sc = f.scenarios[0]
    assert sc.description.split("\n")[-1] == f"dòng mô tả số {n - 1}"
    assert len(sc.description_lines) == n

    # Past the line cap: refused at once, whatever it holds (200 000 description lines).
    huge = "Feature: F\n" + "\n".join(f"  mô tả {i}" for i in range(200_000))
    t0 = time.perf_counter()
    with pytest.raises(gherkin.GherkinError) as err:
        gherkin.parse(huge)
    assert time.perf_counter() - t0 < 1.0
    assert err.value.line == gherkin.LINES_MAX + 1


def test_background_rendered_once_joins_each_scenario() -> None:
    f = gherkin.parse(
        "Feature: F\n  Background:\n    Given mở\n  Scenario: a\n    When b\n  Scenario: c\n"
        "    When d\n"
    )
    bg = gherkin.background_text(f)
    assert bg == "Background:\n  Given mở"
    for sc in f.scenarios:
        assert gherkin.join_case(bg, gherkin.scenario_text(f, sc)) == gherkin.case_text(f, sc)
    assert gherkin.join_case(None, "Scenario: x") == "Scenario: x"
