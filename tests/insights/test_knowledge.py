"""docs/specs/20 §4.9 and §15: bus factor, modules, single-owner and orphaned flags."""

from datetime import UTC, datetime, timedelta

import pytest

from app.insights.knowledge import (
    ROOT_MODULE,
    Author,
    ModuleAuthorRow,
    ModuleTotalRow,
    bus_factor,
    module_of,
    modules,
)

END = datetime(2026, 11, 1, tzinfo=UTC)
AN, BINH, CHI = Author(1, None), Author(2, None), Author(3, None)
EMAIL = Author(None, 99)


@pytest.mark.parametrize(
    ("path", "depth", "module"),
    [
        ("README.md", 2, ROOT_MODULE),
        ("src/a.py", 2, "src"),
        ("src/api/v1/x.py", 2, "src/api"),
        ("src/api/v1/x.py", 1, "src"),
        ("src/api/v1/x.py", 3, "src/api/v1"),
    ],
)
def test_module_of(path: str, depth: int, module: str) -> None:
    assert module_of(path, depth) == module


@pytest.mark.parametrize(
    ("churns", "expected"),
    [([90, 10], 1), ([50, 50], 1), ([40, 35, 25], 2), ([20, 20, 20, 20, 20], 3), ([], 0), ([0], 0)],
)
def test_bus_factor(churns: list[int], expected: int) -> None:
    assert bus_factor(churns, 0.5) == expected


def run(
    totals: list[ModuleTotalRow],
    authors: list[ModuleAuthorRow],
    last: dict[Author, datetime] | None = None,
    inactive: set[int] | None = None,
) -> list[dict[str, object]]:
    found = modules(
        totals,
        authors,
        threshold=0.5,
        min_commits=10,
        end=END,
        last_commit=last if last is not None else {AN: END, BINH: END, CHI: END, EMAIL: END},
        inactive_users=inactive or set(),
    )
    return [m.as_dict() for m in found]


def test_single_owner_needs_enough_commits_and_comes_first() -> None:
    totals = [
        ModuleTotalRow(1, "src/core", 1000, 30, END),
        ModuleTotalRow(1, "src/tiny", 50, 3, END),
        ModuleTotalRow(1, "src/shared", 2000, 40, END),
    ]
    authors = [
        ModuleAuthorRow(1, "src/core", AN, 900, 25),
        ModuleAuthorRow(1, "src/core", BINH, 100, 5),
        ModuleAuthorRow(1, "src/tiny", AN, 50, 3),
        ModuleAuthorRow(1, "src/shared", AN, 800, 15),
        ModuleAuthorRow(1, "src/shared", BINH, 700, 15),
        ModuleAuthorRow(1, "src/shared", CHI, 500, 10),
    ]
    got = run(totals, authors)
    assert [m["module"] for m in got] == ["src/core", "src/shared", "src/tiny"]
    core, shared, tiny = got
    assert core["bus_factor"] == 1 and core["single_owner"] and core["top_share"] == 90.0
    assert shared["bus_factor"] == 2 and not shared["single_owner"]
    assert tiny["bus_factor"] == 1 and not tiny["single_owner"] and not tiny["orphaned"]


def test_orphaned_when_top_owner_left_or_went_quiet() -> None:
    totals = [ModuleTotalRow(1, "a", 100, 12, END), ModuleTotalRow(1, "b", 100, 12, END)]
    authors = [ModuleAuthorRow(1, "a", AN, 100, 12), ModuleAuthorRow(1, "b", BINH, 100, 12)]
    quiet = {AN: END, BINH: END - timedelta(days=61)}
    got = {m["module"]: m["orphaned"] for m in run(totals, authors, last=quiet)}
    assert got == {"a": False, "b": True}
    got = {m["module"]: m["orphaned"] for m in run(totals, authors, inactive={1})}
    assert got == {"a": True, "b": False}


def test_unmapped_emails_stay_separate_authors_and_renames_are_skipped() -> None:
    other_email = Author(None, 100)
    totals = [ModuleTotalRow(1, "x", 100, 12, END), ModuleTotalRow(1, "renamed", 0, 5, END)]
    authors = [ModuleAuthorRow(1, "x", EMAIL, 45, 6), ModuleAuthorRow(1, "x", other_email, 55, 6)]
    got = run(totals, authors, last={EMAIL: END, other_email: END})
    assert len(got) == 1 and got[0]["bus_factor"] == 1 and got[0]["top_share"] == 55.0
