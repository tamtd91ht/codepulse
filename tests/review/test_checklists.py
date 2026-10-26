from app.review.checklists import MAX_CHECKLIST_CHARS, render_checklists, select_checklists
from app.review.prepare import chunk, prepare
from app.review.prompt import ReviewTarget, build_prompt


def test_checklists_follow_the_languages_and_areas_of_the_change() -> None:
    assert select_checklists(["app/api.py"], ["Python"]) == ["General", "Python"]
    titles = select_checklists(
        ["migrations/versions/1_add_col.py", "web/Login.tsx", "src/auth/jwt.go"],
        ["Python", "TypeScript", "Go"],
    )
    assert titles == [
        "General",
        "Go",
        "Python",
        "TypeScript",
        "Database migration",
        "Authentication / authorization code",
    ]
    assert select_checklists(["README.md"], ["unknown"]) == ["General"]


def test_rendered_checklists_are_capped() -> None:
    text = render_checklists(
        ["a.py", "b.ts", "c.java", "d.go", "e.php", "x.sql", "Dockerfile", "auth.py"],
        ["Python", "TypeScript", "Java", "Go", "PHP", "Kotlin", "C#", "Ruby", "SQL"],
    )
    assert len(text) <= MAX_CHECKLIST_CHARS and text.startswith("### General")


def test_fast_prompt_carries_profile_guidance_and_checklist() -> None:
    diff = "@@ -1,0 +1,1 @@\n+x = 1\n"
    (piece,) = chunk(prepare([{"new_path": "app/x.py", "diff": diff}], [], []), 4000)
    target = ReviewTarget("demo/p", "t", profile="Kind: api", guidance="#### repo cfg\nrule A")
    prompt = build_prompt(target, piece)
    assert "Kind: api" in prompt.user and "rule A" in prompt.user
    assert "### Python\n- Mutable default arguments" in prompt.user
    assert "never the severity" in prompt.system
