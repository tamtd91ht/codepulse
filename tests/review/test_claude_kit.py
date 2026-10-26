from pathlib import Path

import pytest

from app.models import BusinessProject
from app.review.claude_kit import (
    SHARED_MOUNT,
    mount_shared,
    render_catalog,
    render_incomplete,
    scan,
    select,
)
from app.review.claude_repo import _mount
from app.review.prepare import parse_diff

TENANT_DIFF = """@@ -1,2 +1,3 @@
 class InvoiceRepository:
+    def find(self, tenant_id, invoice_id): return self.db.query(tenantId=tenant_id)
     pass
"""


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def kit(tmp_path: Path) -> Path:
    claude = tmp_path / ".claude"
    write(claude, "CLAUDE.md", "# Omicx\nShared rules for every service.")
    write(
        claude,
        "commands/review-mr-dev.md",
        "---\ndescription: Review a merge request\n---\n# Review MR\n"
        "1. Apply the file-upload-security skill to uploads.\n2. Check tenant isolation.",
    )
    write(claude, "commands/review-api.md", "---\ndescription: Review an API design\n---\nAPI")
    write(claude, "commands/debug.md", "---\ndescription: Debug a failure\n---\nsteps")
    write(
        claude,
        "rules/critical/tenant-isolation.md",
        "# Tenant isolation\nEvery repository query filters by tenantId.",
    )
    write(
        claude,
        "rules/frontend.md",
        '---\npaths: ["web/**/*.tsx"]\n---\n# Frontend\nUse the design system.',
    )
    write(
        claude,
        "skills/file-upload-security/SKILL.md",
        "---\nname: file-upload-security\ndescription: Validate uploaded files\n---\nMIME",
    )
    write(
        claude,
        "skills/kafka-architect/SKILL.md",
        "---\ndescription: Design Kafka topics\n---\nPartitions",
    )
    write(claude, "skills/kafka-architect/references/topics.md", "not catalogued")
    write(claude, "agents/api-architect.md", "---\nname: api-architect\n---\nREST design")
    return claude


def test_scan_reads_names_descriptions_and_paths(tmp_path: Path) -> None:
    files = {f.name: f for f in scan(kit(tmp_path), ".claude", "repo")}
    assert set(files) == {
        "CLAUDE.md",
        "/review-mr-dev",
        "/review-api",
        "/debug",
        "critical/tenant-isolation",
        "frontend",
        "file-upload-security",
        "kafka-architect",
        "api-architect",
    }
    assert files["/review-mr-dev"].procedure and files["/review-api"].procedure
    assert not files["/debug"].procedure and not files["api-architect"].procedure
    assert files["/review-mr-dev"].description == "Review a merge request"
    assert files["frontend"].globs == ["web/**/*.tsx"]
    assert files["kafka-architect"].path == ".claude/skills/kafka-architect/SKILL.md"
    assert files["critical/tenant-isolation"].rel == "rules/critical/tenant-isolation.md"


def test_select_follows_the_change(tmp_path: Path) -> None:
    files = scan(kit(tmp_path), ".claude", "repo")
    diff = [parse_diff("app/invoice/repository.py", TENANT_DIFF)]
    chosen = select(files, diff, "Filter invoices by tenant", None)
    # The review commands are the procedure, the MR one first.
    assert [f.name for f in chosen.procedure] == ["/review-mr-dev", "/review-api"]
    ranked = [f.name for f in chosen.ranked]
    assert ranked[0] == "CLAUDE.md"
    assert "critical/tenant-isolation" in ranked  # matches "tenant" in the diff
    assert "file-upload-security" in ranked  # the review command names it
    by_name = {f.name: f for f in files}
    assert "lệnh review nhắc tới" in by_name["file-upload-security"].why
    catalog = [f.name for f in chosen.catalog]
    # Unrelated, and a path-scoped rule whose paths the change does not touch.
    assert "kafka-architect" in catalog and "frontend" in catalog
    assert by_name["frontend"].why == ["paths không khớp"]


def test_a_file_in_both_sources_is_chosen_once(tmp_path: Path) -> None:
    shared = scan(kit(tmp_path / "shared"), SHARED_MOUNT, "shared")
    claude = kit(tmp_path / "repo")
    write(claude, "rules/critical/tenant-isolation.md", "# Tenant isolation\nRepo's own version.")
    repo = scan(claude, ".claude", "repo")
    diff = [parse_diff("app/invoice/repository.py", TENANT_DIFF)]
    chosen = select([*shared, *repo], diff, "Filter invoices by tenant", None)
    kept = [*chosen.procedure, *chosen.ranked, *chosen.catalog]
    # Equal content: the shared copy stays, the repo's is reported as its duplicate.
    assert all(f.source == "shared" for f in kept if f.name != "critical/tenant-isolation")
    copies = {copy.path: first.path for copy, first in chosen.duplicates}
    assert copies[".claude/skills/kafka-architect/SKILL.md"] == (
        f"{SHARED_MOUNT}/skills/kafka-architect/SKILL.md"
    )
    # A file with the same name but other content is a different file: both are kept.
    tenant = [f.source for f in kept if f.name == "critical/tenant-isolation"]
    assert sorted(tenant) == ["repo", "shared"]
    assert ".claude/rules/critical/tenant-isolation.md" not in copies


def test_the_first_copy_is_kept_and_a_pin_on_the_copy_still_counts(tmp_path: Path) -> None:
    shared = scan(kit(tmp_path / "shared"), SHARED_MOUNT, "shared")
    claude = tmp_path / "repo" / ".claude"
    # The shared rule copied into the repo as a skill: same text, another place.
    text = (tmp_path / "shared/.claude/rules/critical/tenant-isolation.md").read_text("utf-8")
    write(claude, "skills/tenant/SKILL.md", text)
    repo = scan(claude, ".claude", "repo")
    diff = [parse_diff("web/app/page.tsx", "@@ -1 +1 @@\n-a\n+b\n")]
    # Repo first (the shared kit is not mounted): the readable repo copy is the one kept.
    chosen = select([*repo, *shared], diff, "x", None, ["skills/tenant/SKILL.md"])
    assert {c.path: f.path for c, f in chosen.duplicates}[
        f"{SHARED_MOUNT}/rules/critical/tenant-isolation.md"
    ] == ".claude/skills/tenant/SKILL.md"
    # Pinned by the place of the shared copy's twin, the kept file is still loaded first.
    chosen = select([*shared, *repo], diff, "x", None, ["skills/tenant/SKILL.md"])
    assert chosen.ranked[0].path == f"{SHARED_MOUNT}/rules/critical/tenant-isolation.md"
    assert "nạp thêm" in chosen.ranked[0].why


def test_incomplete_quotes_name_the_real_size() -> None:
    text = render_incomplete([("a/SKILL.md", 8000, 15300), ("b.md", 0, 4200), ("c.md", 900, None)])
    assert text.splitlines() == [
        "- a/SKILL.md: only the first ~8000 of 15300 characters are quoted",
        "- b.md: not quoted (no room), 4200 characters",
        "- c.md: only the first ~900 characters are quoted",
    ]


def test_paths_and_pinned_globs_win(tmp_path: Path) -> None:
    files = scan(kit(tmp_path), ".claude", "repo")
    diff = [parse_diff("web/app/page.tsx", "@@ -1 +1 @@\n-a\n+b\n")]
    chosen = select(files, diff, "x", None, ["skills/kafka-*/SKILL.md"])
    ranked = [f.name for f in chosen.ranked]
    assert ranked[:2] == ["kafka-architect", "frontend"]
    assert "nạp thêm" in chosen.ranked[0].why
    assert "paths khớp file thay đổi" in chosen.ranked[1].why


def test_mount_copies_the_shared_folder_once(tmp_path: Path) -> None:
    claude = kit(tmp_path / "shared")
    root = tmp_path / "checkout"
    root.mkdir()
    mounted = mount_shared(claude, root)
    assert mounted == root / SHARED_MOUNT
    paths = [f.path for f in scan(mounted, SHARED_MOUNT, "shared")]
    assert f"{SHARED_MOUNT}/commands/review-mr-dev.md" in paths
    assert (root / SHARED_MOUNT / "commands" / "review-mr-dev.md").is_file()
    assert mount_shared(claude, root) is None  # already there: never overwritten


def test_catalog_lists_paths_and_relevance(tmp_path: Path) -> None:
    files = scan(kit(tmp_path), ".claude", "repo")
    select(files, [parse_diff("app/repo.py", TENANT_DIFF)], "tenant", None)
    text = render_catalog(files, {"repo": "của repo"})
    assert "- .claude/skills/kafka-architect/SKILL.md (skill kafka-architect, của repo)" in text
    assert "[liên quan:" in text


def test_a_failed_copy_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = kit(tmp_path / "shared")
    root = tmp_path / "checkout"
    root.mkdir()

    def half_copy(src: Path, dst: Path, **_: object) -> None:
        (Path(dst) / "rules").mkdir(parents=True)
        (Path(dst) / "rules" / "half.md").write_text("half")
        raise OSError("disk full")

    monkeypatch.setattr("app.review.claude_kit.shutil.copytree", half_copy)
    with pytest.raises(OSError):
        mount_shared(claude, root)
    assert not (root / SHARED_MOUNT).exists()


def test_the_shared_repo_is_still_used_when_it_cannot_be_copied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claude = kit(tmp_path / "shared")
    business = BusinessProject(name="x", claude_repo_path="demo/cfg")

    def broken(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("app.review.claude_repo.mount_shared", broken)
    assert _mount(claude, tmp_path / "checkout", business) == (None, "OSError")
    assert _mount(claude, None, business) == (None, None)  # preview: nothing to copy into
