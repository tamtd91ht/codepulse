from pathlib import Path

import pytest

from app.models import BusinessProject
from app.review.claude_kit import (
    SHARED_MOUNT,
    mount_shared,
    render_catalog,
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
