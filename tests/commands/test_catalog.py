from pathlib import Path, PurePosixPath

import httpx
import pytest
import respx

from app.collector.gitlab.client import GitLabClient
from app.commands import catalog
from app.commands.catalog import COMMAND_NAME, Command, command_name, merge, parse, scan
from tests.review.test_checkout import API, BASE, make_archive


@pytest.mark.parametrize(
    ("rel", "name"),
    [
        ("review.md", "review"),
        ("report/weekly.md", "report:weekly"),
        ("a/b/c.md", "a:b:c"),
        ("fix-issue.md", "fix-issue"),
        ("README.txt", None),
        ("bad name.md", None),
    ],
)
def test_command_names_follow_claude_code(rel: str, name: str | None) -> None:
    assert command_name(PurePosixPath(rel)) == name


@pytest.mark.parametrize("name", ["-p", "a b", "a;rm", "", "../x", "a::b"])
def test_names_that_could_pass_for_something_else_are_refused(name: str) -> None:
    assert not COMMAND_NAME.match(name)


def test_frontmatter_is_read() -> None:
    text = (
        "---\n"
        "description: Weekly report\n"
        "argument-hint: <team> [week]\n"
        "allowed-tools: Bash(git log:*, git diff:*), Read, Write\n"
        "model: claude-haiku-5-5\n"
        "---\n"
        "Write the report for $ARGUMENTS.\n"
    )
    command = parse(PurePosixPath("report/weekly.md"), text, "repo", ".claude/commands")
    assert command is not None
    assert command.name == "report:weekly"
    assert command.path == ".claude/commands/report/weekly.md"
    assert command.description == "Weekly report"
    assert command.argument_hint == "<team> [week]"
    assert command.allowed_tools == ["Bash(git log:*, git diff:*)", "Read", "Write"]
    assert command.model == "claude-haiku-5-5"
    assert command.body.startswith("Write the report")


def test_description_falls_back_to_the_first_line() -> None:
    command = parse(PurePosixPath("x.md"), "# Title\n\nDoes the thing.\n", "shared", "commands")
    assert command is not None and command.description == "Does the thing."
    assert command.allowed_tools == [] and command.model == ""


def test_scan_reads_markdown_only(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.md").write_text("A command", encoding="utf-8")
    (tmp_path / "b.md").write_text("B command", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a command", encoding="utf-8")
    assert [c.name for c in scan(tmp_path, "repo", ".claude/commands")] == ["b", "sub:a"]
    assert scan(tmp_path / "missing", "repo", ".claude/commands") == []


def test_the_repo_command_replaces_the_shared_one_of_the_same_name() -> None:
    own = [Command(name="report", source="repo", path=".claude/commands/report.md")]
    shared = [
        Command(name="report", source="shared", path="commands/report.md"),
        Command(name="audit", source="shared", path="commands/audit.md"),
    ]
    merged = merge(own, shared)
    assert [(c.name, c.source, c.overridden) for c in merged] == [
        ("audit", "shared", False),
        ("report", "repo", False),
        ("report", "shared", True),
    ]


@respx.mock
def test_shared_commands_fall_back_to_a_root_commands_folder() -> None:
    # A shared .claude repo laid out as the .claude folder itself: no `.claude/commands`, so
    # GitLab hands back an empty archive for it, then the real one for `commands`.
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("path") == "commands":
            text = "---\ndescription: Báo cáo tuần\n---\nDo it"
            body = make_archive({"commands/weekly.md": text.encode()})
            return httpx.Response(200, content=body)
        return httpx.Response(200, content=b"")

    respx.get(f"{API}/projects/7/repository/archive.tar.gz").mock(side_effect=answer)
    with GitLabClient(BASE, "t") as client:
        found = catalog.shared_commands(client, 7, "main", [])
    assert [(c.name, c.source, c.description) for c in found] == [
        ("weekly", "shared", "Báo cáo tuần")
    ]
