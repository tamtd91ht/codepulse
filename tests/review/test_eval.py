from collections import Counter

from app.review.eval import load_cases, oracle_provider, patch_to_entries, run
from app.review.prepare import parse_diff
from app.review.providers import FakeLLMProvider

CASES = load_cases()


def test_case_set_matches_the_spec_minimum() -> None:
    kinds = Counter(case.id.split("-")[0] for case in CASES)
    assert kinds == {"sec": 5, "bug": 5, "perf": 3, "maint": 3, "clean": 4}
    assert all(not c.expected for c in CASES if c.id.startswith("clean"))


def test_expected_lines_are_added_lines_of_the_diff() -> None:
    for case in CASES:
        files = {
            e["new_path"]: parse_diff(e["new_path"], e["diff"])
            for e in patch_to_entries(case.patch)
        }
        for expected in case.expected:
            added = files[expected.file_path].added_lines
            assert expected.line_start in added and expected.line_end in added, case.id


def test_patch_parsing_handles_modified_and_new_files() -> None:
    patch = (
        "diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n"
        "@@ -1,2 +1,2 @@\n a = 1\n-b = 2\n+b = 3\n"
        "diff --git a/y.py b/y.py\nnew file mode 100644\n--- /dev/null\n+++ b/y.py\n"
        "@@ -0,0 +1 @@\n+print(1)\n"
    )
    first, second = patch_to_entries(patch)
    assert (first["new_path"], first["new_file"]) == ("x.py", False)
    assert first["diff"].startswith("@@ -1,2 +1,2 @@")
    assert (second["new_path"], second["new_file"]) == ("y.py", True)


def test_a_perfect_reviewer_passes() -> None:
    report = run(oracle_provider(CASES), CASES, lambda r: None)
    assert (report.precision, report.recall, report.passed) == (1.0, 1.0, True)


def test_a_silent_reviewer_fails_on_recall() -> None:
    report = run(FakeLLMProvider(), CASES, lambda r: None)
    assert report.recall == 0.0
    assert not report.passed
