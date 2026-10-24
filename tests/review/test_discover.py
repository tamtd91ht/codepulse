from pathlib import Path

from app.review.discover import MAX_FILE_CHARS, discover, render
from app.review.profile import ProjectProfile, parse_profile, profile_is_fresh, render_profile


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_discover_collects_readme_instructions_manifests_and_shape(tmp_path: Path) -> None:
    write(tmp_path, "README.md", "# OmiCRM\nCRM đa kênh cho SME.")
    write(tmp_path, "CLAUDE.md", 'Rules: tenantId everywhere. token="abcdefgh12345"')
    write(tmp_path, ".claude/rules/critical/tenant-isolation.md", "# Tenant\n- always")
    write(tmp_path, ".claude/agents/backend-reviewer.md", "review agent")
    write(tmp_path, ".claude/agents/module-generator.md", "not a reviewer")
    write(tmp_path, ".cursor/rules/style.mdc", "cursor rules")
    write(tmp_path, "package.json", '{"dependencies": {"next": "16", "react": "19", "pg": "8"}}')
    write(tmp_path, "pom.xml", "<project><artifactId>spring-boot-starter</artifactId></project>")
    write(tmp_path, "app/main.py", "print(1)")
    write(tmp_path, "app/api/routes.py", "x = 1")
    write(tmp_path, "web/src/index.tsx", "export {}")
    write(tmp_path, "node_modules/left-pad/index.js", "ignored")
    write(tmp_path, ".env", "SECRET=1")  # not an instruction file: never collected

    docs = discover(tmp_path)
    assert docs.readme is not None and "OmiCRM" in docs.readme.text
    paths = docs.instruction_paths
    assert paths[:2] == ["CLAUDE.md", ".claude/rules/critical/tenant-isolation.md"]
    assert ".claude/agents/backend-reviewer.md" in paths
    assert ".claude/agents/module-generator.md" not in paths
    assert ".cursor/rules/style.mdc" in paths
    assert ".env" not in paths
    assert dict(docs.languages)["Python"] == 2 and dict(docs.languages)["TypeScript"] == 1
    assert {"Next.js", "React", "Spring Boot", "Java (Maven)"} <= set(docs.frameworks)
    assert any(e.startswith("app/") for e in docs.tree) and "app/api/" in docs.tree
    assert not any("node_modules" in e for e in docs.tree)
    # Secrets in instruction files are redacted before anything reaches a model.
    claude = next(d for d in docs.instructions if d.path == "CLAUDE.md")
    assert "abcdefgh12345" not in claude.text and "REDACTED" in claude.text
    text = render(docs)
    assert "### CLAUDE.md" in text and "Repository shape" in text


def test_discover_caps_file_size(tmp_path: Path) -> None:
    write(tmp_path, "AGENTS.md", "y" * (MAX_FILE_CHARS * 3))
    docs = discover(tmp_path)
    assert len(docs.instructions[0].text) <= MAX_FILE_CHARS + 20
    assert docs.instructions[0].text.endswith("(… rút gọn)")


def test_profile_parsing_is_lenient_and_rendering_is_compact() -> None:
    profile = parse_profile(
        '```json {"kind": "backend-service", "summary": "CRM API", "domain": "CRM", '
        '"features": ["tickets", "calls"], '
        '"stack": {"languages": ["Java"], "datastores": ["MongoDB"]}, '
        '"security": {"tenancy": "tenantId from JWT on every query", "notes": ["x"]}, '
        '"risk_areas": ["wallet/"], "confidence": 0.8, "extra": 1}```'
    )
    assert profile.kind == "backend-service" and profile.stack.datastores == ["MongoDB"]
    text = render_profile(profile.model_dump())
    assert "Kind: backend-service · Domain: CRM" in text
    assert "tenancy: tenantId from JWT" in text and "Risk areas:\n  - wallet/" in text
    weird = parse_profile({"kind": "spaceship", "confidence": "high", "features": "no"})
    assert weird.kind == "other" and weird.confidence == 0.5 and weird.features == []
    import pytest

    with pytest.raises(ValueError):
        parse_profile("not json at all")
    assert ProjectProfile().summary == ""


def test_profile_freshness_follows_ttl_and_version() -> None:
    from datetime import UTC, datetime, timedelta

    from app.models import Project

    now = datetime(2026, 10, 26, tzinfo=UTC)
    project = Project(external_id=1, path_with_namespace="p")
    assert not profile_is_fresh(project, timedelta(days=7), now)
    project.profile, project.profile_at = {"version": 1}, now - timedelta(days=2)
    assert profile_is_fresh(project, timedelta(days=7), now)
    project.profile_at = now - timedelta(days=8)
    assert not profile_is_fresh(project, timedelta(days=7), now)
    project.profile, project.profile_at = {"version": 0}, now
    assert not profile_is_fresh(project, timedelta(days=7), now)


def test_review_skills_are_read_and_every_instruction_has_a_kind(tmp_path: Path) -> None:
    from app.review.discover import discover, instruction_kind

    for rel in (
        "CLAUDE.md",
        ".claude/skills/security-review/SKILL.md",
        ".claude/skills/release-notes/SKILL.md",  # not a review skill: not read
        ".claude/agents/backend-reviewer.md",
        ".claude/rules/critical/tenant.md",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# x" + chr(10), encoding="utf-8")
    paths = discover(tmp_path).instruction_paths
    assert ".claude/skills/security-review/SKILL.md" in paths
    assert ".claude/skills/release-notes/SKILL.md" not in paths
    assert {instruction_kind(p) for p in paths} == {"instructions", "skill", "agent", "rules"}
    assert instruction_kind("docs/specs/00_README.md") == "doc"
    assert instruction_kind(".claude/commands/review-pr.md") == "command"
