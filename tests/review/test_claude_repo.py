from pathlib import Path

from app.review.claude_repo import as_repo_layout, include_globs, pack_instructions
from app.review.discover import CLIPPED, Doc, discover


def test_a_repo_that_is_the_claude_folder_is_wrapped(tmp_path: Path) -> None:
    root = tmp_path / "src"
    (root / "skills" / "review").mkdir(parents=True)
    (root / "skills" / "review" / "SKILL.md").write_text("review skill")
    layout = as_repo_layout(root)
    assert (layout / ".claude" / "skills" / "review" / "SKILL.md").is_file()
    assert [d.path for d in discover(layout).instructions] == [".claude/skills/review/SKILL.md"]


def test_a_repo_with_a_claude_folder_is_used_as_it_is(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    assert as_repo_layout(tmp_path) == tmp_path


def test_discover_reports_claude_files_it_did_not_load(tmp_path: Path) -> None:
    for rel in (".claude/skills/review/SKILL.md", ".claude/skills/deploy/SKILL.md"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(rel)
    docs = discover(tmp_path)
    assert docs.not_loaded == [(".claude/skills/deploy/SKILL.md", "name")]
    docs = discover(tmp_path, ["skills/deploy/SKILL.md"])
    assert docs.not_loaded == [] and len(docs.instructions) == 2


def test_include_globs_are_relative_and_safe() -> None:
    assert include_globs(" skills/*/SKILL.md,\n/agents/x.md, ../etc/passwd ") == [
        "skills/*/SKILL.md",
        "agents/x.md",
    ]


def test_pack_shares_the_budget_and_reports_what_fit() -> None:
    shared = [Doc(f".claude/skills/s{i}/SKILL.md", "s" * 300) for i in range(5)]
    repo = [Doc("CLAUDE.md", "r" * 100), Doc(".claude/rules/x.md", "x" * 50 + CLIPPED)]
    packed = pack_instructions(
        [("shared", "Shared", "group/cfg:", shared), ("repo", "Repo", "", repo)], 1000
    )
    by_path = {r["path"]: r for r in packed.records}
    # The small repo group fits whole; the shared one gets the rest of the budget.
    assert by_path["CLAUDE.md"]["status"] == "used"
    assert by_path[".claude/rules/x.md"]["status"] == "partial"  # clipped by discover()
    statuses = [by_path[d.path]["status"] for d in shared]
    assert statuses[0] == "used" and statuses[-1] == "cut" and "partial" in statuses
    assert "### group/cfg:.claude/skills/s0/SKILL.md" in packed.text
    assert len(packed.text) < 1200
    assert pack_instructions([("repo", "Repo", "", [])], 1000).text == ""
