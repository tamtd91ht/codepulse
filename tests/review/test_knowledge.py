"""Review knowledge (docs/specs/19 §3): schema clipping, budget, merge, edits, check selection,
rendering and batched compiling. No database, no model: a fake provider answers."""

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.review.discover import Doc, ProjectDocs
from app.review.knowledge import (
    BATCH_CHARS,
    COMPILE_SYSTEM,
    MAX_CHECKS,
    MAX_INVARIANTS,
    Check,
    Knowledge,
    KnowledgeBudgetError,
    apply_edits,
    basis_for,
    batches,
    claude_tree_sha,
    compile_documents,
    fit_budget,
    merge,
    parse_knowledge,
    render_for_review,
    select_checks,
)
from app.review.providers.base import ReviewPrompt, ReviewResult


def invariant(n: int, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"inv-{n}",
        "statement": f"Mọi truy vấn bảng t{n} phải lọc theo tenant_id",
        "severity": "high",
        "signals": ["db_query"],
        "source": {"path": ".claude/rules/tenant.md", "lines": "3-5"},
        "confidence": 0.9,
    }
    base.update(kw)
    return base


def check(n: int, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": f"chk-{n}",
        "name": f"Check {n}",
        "triggers": {"paths": [], "signals": [], "keywords": []},
        "items": [f"item {i} of check {n}" for i in range(5)],
        "source": ".claude/skills/x/SKILL.md:1-10",
    }
    base.update(kw)
    return base


SAMPLE: dict[str, Any] = {
    "invariants": [invariant(1), invariant(2, severity="critical", paths=["app/api/**"])],
    "domain": {
        "summary": "CRM đa kênh",
        "actors": ["agent", "admin"],
        "tenancy": "multi-tenant theo tenant_id",
        "critical_modules": [{"path": "app/billing", "why": "tiền"}],
        "glossary": {"tenant": "khách hàng doanh nghiệp"},
        "stack": ["FastAPI", "PostgreSQL"],
    },
    "severity_rules": [
        {
            "id": "sev-money",
            "when": {"paths": ["app/billing/**"], "signals": ["money"]},
            "effect": {"raise_to": "critical"},
            "reason": "tiền",
            "source": {"path": "CLAUDE.md", "lines": "10"},
        }
    ],
    "procedure": {
        "steps": ["Đọc diff", "Kiểm tra tenant"],
        "severity_map": {"tenant leak": "critical"},
        "source": {"path": ".claude/commands/review-mr.md"},
    },
    "checks": [
        check(1, triggers={"paths": ["migrations/**"], "signals": ["migration"]}),
        check(2, triggers={"signals": ["endpoint", "auth"], "keywords": ["tenant_id"]}),
        check(3, triggers={"keywords": ["upload"]}),
        check(4),  # no triggers: never loaded
    ],
    "index": [
        {
            "path": ".claude/rules/tenant.md",
            "source": "repo",
            "kind": "rules",
            "purpose": "tenant",
            "used_for": ["inv-1", "inv-2"],
        },
    ],
    "omitted": [{"path": ".claude/commands/commit.md", "reason": "git_workflow"}],
    "code_conventions": [
        {"id": "conv-1", "statement": "Repository pattern", "evidence": ["app/repo.py:1-20"]}
    ],
    "meta": {"confidence": 0.8},
}


def sample() -> Knowledge:
    return Knowledge.model_validate(SAMPLE)


# -- schema ----------------------------------------------------------------------------------------


def test_schema_clips_strings_caps_lists_and_drops_unknown_signals() -> None:
    raw: dict[str, Any] = {
        "invariants": [
            invariant(n, statement="x" * 1000, signals=["db_query", "made_up", "AUTH"])
            for n in range(MAX_INVARIANTS + 10)
        ],
        "checks": [check(n, items=[f"i{i}" for i in range(30)]) for n in range(MAX_CHECKS + 5)],
        "severity_rules": [{"id": "s", "effect": {"raise_to": "nope"}, "reason": "r"}],
        "omitted": [{"path": "p", "reason": "whatever"}],
        "unknown_key": {"ignored": True},
        "domain": "not an object",
        "meta": {"confidence": "high", "cost_usd": "free"},
    }
    k = Knowledge.model_validate(raw)
    assert len(k.invariants) == MAX_INVARIANTS
    assert len(k.invariants[0].statement) == 300
    assert k.invariants[0].signals == ["db_query", "auth"]
    assert len(k.checks) == MAX_CHECKS and len(k.checks[0].items) == 15
    assert k.severity_rules[0].effect.raise_to is None
    assert k.omitted[0].reason == "not_review"
    assert k.domain.summary == "" and k.meta.confidence == 0.5 and k.meta.cost_usd == 0.0
    assert k.schema_version == 1


def test_schema_assigns_unique_ids_and_redacts_secrets() -> None:
    k = Knowledge.model_validate(
        {
            "invariants": [
                {"statement": 'a token="abcdefgh12345678" must not leak'},
                {"id": "dup", "statement": "b"},
                {"id": "dup", "statement": "c"},
            ],
            "code_conventions": [{"statement": "no id here"}],
        }
    )
    assert [i.id for i in k.invariants] == ["inv-1", "dup", "dup-2"]
    assert "abcdefgh12345678" not in k.invariants[0].statement
    assert k.code_conventions[0].id == "conv-1"


def test_parse_knowledge_accepts_text_with_json_and_rejects_junk() -> None:
    k = parse_knowledge('Here you go:\n{"invariants": [{"statement": "x"}]}\nthanks')
    assert len(k.invariants) == 1
    with pytest.raises(ValueError, match="not JSON"):
        parse_knowledge("no json here")
    with pytest.raises(ValueError, match="not a JSON object"):
        parse_knowledge([1, 2])


# -- budget ----------------------------------------------------------------------------------------


def test_fit_budget_keeps_higher_priority_sections_and_whole_items() -> None:
    k = Knowledge.model_validate(
        {
            "invariants": [invariant(n, statement="s" * 200) for n in range(10)],
            "domain": SAMPLE["domain"],
            "severity_rules": SAMPLE["severity_rules"],
            "procedure": SAMPLE["procedure"],
            "code_conventions": [{"statement": "c" * 200} for _ in range(10)],
            "checks": [check(n, items=["i" * 150] * 10) for n in range(20)],
            "index": [{"path": f"f{n}.md", "purpose": "p" * 100} for n in range(100)],
        }
    )
    full = fit_budget(k, max_tokens=100_000)
    assert full.counts() == k.counts() and len(full.index) == 100

    # Room for the invariants and a little more: what comes after them is cut first.
    tight = fit_budget(k, max_tokens=800)
    assert len(tight.invariants) == 10
    assert tight.domain.summary and tight.severity_rules and tight.procedure.steps
    assert len(tight.code_conventions) <= 1
    assert len(tight.checks) == 0 and len(tight.index) == 0
    # Never half an invariant: the statements are intact.
    assert all(len(i.statement) == 200 for i in tight.invariants)

    tiny = fit_budget(k, max_tokens=200)
    assert 0 < len(tiny.invariants) < 10 and not tiny.code_conventions
    rendered = render_for_review(tiny, [])
    assert len(rendered) // 4 <= 200


# -- merge -----------------------------------------------------------------------------------------


def test_merge_prefixes_ids_and_records_conflicts_with_repo_winning() -> None:
    shared = Knowledge.model_validate(
        {
            "invariants": [
                invariant(1, statement="Mọi truy vấn bảng t1 phải lọc theo tenant_id"),
                invariant(9, statement="Log phải có request_id"),
            ],
            "checks": [check(7, triggers={"signals": ["money"]})],
            "domain": {"summary": "shared summary", "stack": ["Java"]},
            "procedure": {"steps": ["shared step"]},
            "index": [{"path": "rules/log.md", "purpose": "log"}],
            "meta": {"cost_usd": 1.0, "sources": {"shared_sha": "abc"}},
        }
    )
    repo = Knowledge.model_validate(
        {
            "invariants": [
                invariant(1, severity="critical"),  # same id as shared
                invariant(5, statement="Log phải có request_id!"),  # same statement, new id
            ],
            "domain": {"summary": "", "tenancy": "multi"},
            "meta": {"cost_usd": 0.5},
        }
    )
    out = merge(shared, repo)
    ids = [i.id for i in out.invariants]
    assert ids == ["repo:inv-1", "repo:inv-5"]
    assert out.invariants[0].severity == "critical"
    assert [c.id for c in out.checks] == ["shared:chk-7"]
    assert out.checks[0].authority == "shared"
    assert {(c.a, c.chosen) for c in out.conflicts} == {
        ("shared:inv-1", "repo:inv-1"),
        ("shared:inv-9", "repo:inv-5"),
    }
    # The repo's domain wins; the shared one fills what it left empty.
    assert out.domain.tenancy == "multi" and out.domain.summary == "shared summary"
    assert out.domain.stack == ["Java"]
    assert out.procedure.steps == ["shared step"]
    assert out.index[0].source == "shared"
    assert out.meta.cost_usd == 1.5 and out.meta.sources["shared_sha"] == "abc"

    alone = merge(None, repo)
    assert [i.id for i in alone.invariants] == ["repo:inv-1", "repo:inv-5"]
    assert merge(None, alone).invariants[0].id == "repo:inv-1"  # idempotent


# -- edits -----------------------------------------------------------------------------------------


def test_apply_edits_disables_changes_severity_rewords_adds_and_reports_orphans() -> None:
    k = merge(None, sample())
    edited, orphans = apply_edits(
        k,
        [
            {"item_id": "repo:inv-1", "action": "disable", "payload": {}},
            {"item_id": "repo:inv-2", "action": "set_severity", "payload": {"severity": "low"}},
            {"item_id": "repo:conv-1", "action": "reword", "payload": {"statement": "Reworded"}},
            {"item_id": "repo:chk-1", "action": "reword", "payload": {"text": "New name"}},
            {"item_id": "gone", "action": "disable", "payload": {}},
            {
                "item_id": "manual-tenant",
                "action": "add_invariant",
                "payload": {"statement": "Thêm tay", "severity": "critical", "signals": ["auth"]},
            },
            {"item_id": "x", "action": "add_invariant", "payload": {}},  # no statement: ignored
        ],
    )
    assert orphans == ["gone"]
    ids = [i.id for i in edited.invariants]
    assert ids == ["repo:inv-2", "manual:manual-tenant"]
    assert edited.invariants[0].severity == "low" and edited.invariants[0].edited
    assert edited.invariants[1].authority == "manual" and edited.invariants[1].signals == ["auth"]
    assert edited.code_conventions[0].statement == "Reworded"
    assert edited.checks[0].name == "New name"
    # The original is untouched.
    assert [i.id for i in k.invariants] == ["repo:inv-1", "repo:inv-2"]


def test_apply_edits_caps_manual_invariants() -> None:
    edits = [
        {"item_id": f"m{n}", "action": "add_invariant", "payload": {"statement": f"s{n}"}}
        for n in range(15)
    ]
    edited, _ = apply_edits(Knowledge(), edits)
    assert len(edited.invariants) == 10


# -- selecting checks ------------------------------------------------------------------------------


def test_select_checks_matches_globs_signals_and_keywords_and_ranks_by_hits() -> None:
    k = sample()
    chosen = select_checks(
        k,
        ["migrations/versions/001.py", "app/api/users.py"],
        {"migration", "endpoint", "auth"},
        "+    q = select(User).where(User.tenant_id == tid)\n",
    )
    # chk-2: endpoint + auth + keyword = 3; chk-1: glob + signal = 2; chk-3: no upload; chk-4 never.
    assert [c.id for c in chosen] == ["chk-2", "chk-1"]
    assert select_checks(k, ["x.py"], set(), "") == []
    assert [c.id for c in select_checks(k, [], set(), "multipart UPLOAD")] == ["chk-3"]
    assert len(select_checks(k, ["migrations/a.py"], {"endpoint"}, "tenant_id", limit=1)) == 1


# -- rendering -------------------------------------------------------------------------------------


def test_render_shows_sections_and_ids_for_citation() -> None:
    k = merge(None, sample())
    checks = select_checks(k, ["migrations/a.py"], {"migration"}, "")
    text = render_for_review(k, checks)
    for section in (
        "## INVARIANTS",
        "## DOMAIN",
        "## SEVERITY RULES",
        "## PROCEDURE",
        "## CODE CONVENTIONS",
        "## CHECKS",
        "## INDEX",
    ):
        assert section in text
    assert "rk:repo:inv-1" in text and "rk:repo:sev-money" in text
    assert "rk:repo:conv-1" in text and "rk:repo:chk-1" in text
    assert "<.claude/rules/tenant.md:3-5>" in text
    assert "raise to critical" in text
    assert "1. Đọc diff" in text
    assert "rk:repo:chk-2" not in text  # not selected
    assert "commit.md" not in text  # omitted items never reach the prompt

    without = render_for_review(Knowledge(), [])
    assert without == ""


def test_basis_for_summarises_what_the_review_ran_with() -> None:
    k = sample()
    k.meta.version = 3
    k.meta.sources = {"claude_tree_sha": "abc"}
    basis = basis_for(k, k.checks[:2], stale=True, approved=False)
    assert basis["version"] == 3 and basis["stale"] is True and basis["approved"] is False
    assert basis["invariants"] == 2 and basis["checks_loaded"] == ["chk-1", "chk-2"]
    assert basis["sources"] == {"claude_tree_sha": "abc"} and basis["tokens"] > 0


# -- compiling -------------------------------------------------------------------------------------


@dataclass
class FakeProvider:
    name: str = "fake"
    answers: list[Any] = field(default_factory=list)
    prompts: list[ReviewPrompt] = field(default_factory=list)
    cost: float = 0.1

    def review(self, prompt: ReviewPrompt) -> ReviewResult:
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if self.answers else {"invariants": [invariant(1)]}
        return ReviewResult(
            output=answer, model="fake-1", prompt_tokens=10, completion_tokens=5, cost_usd=self.cost
        )


def docs_of(*pairs: tuple[str, str]) -> ProjectDocs:
    docs = ProjectDocs()
    docs.readme = Doc("README.md", "# Repo\nCRM")
    docs.instructions = [Doc(p, t) for p, t in pairs]
    return docs


def test_compile_documents_is_one_call_with_untrusted_docs_as_data() -> None:
    provider = FakeProvider(
        answers=[{"invariants": [invariant(1)], "index": [{"path": "CLAUDE.md"}]}]
    )
    docs = docs_of(("CLAUDE.md", 'rules: tenant. password="supersecret99"'))
    extra = [Doc(".claude/skills/a/SKILL.md", "skill text"), Doc("CLAUDE.md", "dup")]
    k, usage = compile_documents(provider, docs, extra, "demo/p")
    assert len(provider.prompts) == 1
    prompt = provider.prompts[0]
    assert prompt.system == COMPILE_SYSTEM and "untrusted" in prompt.system
    assert "### CLAUDE.md" in prompt.user and "### .claude/skills/a/SKILL.md" in prompt.user
    assert prompt.user.count("### CLAUDE.md") == 1  # the duplicate path is read once
    assert "supersecret99" not in prompt.user
    assert k.invariants[0].id == "inv-1" and k.meta.calls == 1 and k.meta.files_read == 3
    assert usage.cost_usd == 0.1 and k.meta.model == "fake-1"


def test_compile_documents_batches_large_inputs_and_merges_once() -> None:
    big = "x" * 150_000
    docs = docs_of(*[(f"docs/{n}.md", big) for n in range(4)])  # 600k chars: two batches
    merged = {"invariants": [invariant(1), invariant(2)]}
    provider = FakeProvider(
        answers=[{"invariants": [invariant(1)]}, {"invariants": [invariant(2)]}, merged]
    )
    k, usage = compile_documents(provider, docs, [], "demo/p")
    assert len(provider.prompts) == 3
    assert all(len(p.user) <= BATCH_CHARS + 1_000 for p in provider.prompts[:2])
    assert "Partial documents (2)" in provider.prompts[2].user
    assert [i.id for i in k.invariants] == ["inv-1", "inv-2"]
    assert k.meta.calls == 3 and usage.cost_usd == pytest.approx(0.3)
    assert usage.prompt_tokens == 30

    runs = batches([Doc("a", "x" * 300_000), Doc("b", "y" * 300_000), Doc("c", "z")])
    assert [[d.path for d in run] for run in runs] == [["a"], ["b", "c"]]


def test_compile_documents_stops_at_the_cost_cap() -> None:
    big = "x" * 300_000
    docs = docs_of(("a.md", big), ("b.md", big), ("c.md", big))
    provider = FakeProvider(cost=2.0)
    with pytest.raises(KnowledgeBudgetError):
        compile_documents(provider, docs, [], "demo/p", max_cost_usd=3.0)
    assert len(provider.prompts) == 2  # the third call would pass the cap


def test_compile_documents_rejects_an_unusable_answer() -> None:
    provider = FakeProvider(answers=["not json at all"])
    with pytest.raises(ValueError, match="not JSON"):
        compile_documents(provider, docs_of(("a.md", "x")), [], "demo/p")


def test_select_checks_reads_dot_slash_and_root_slash_globs_as_repo_paths() -> None:
    k = Knowledge.model_validate(
        {
            "checks": [
                check(1, triggers={"paths": ["./migrations/**"]}),
                check(2, triggers={"paths": ["/.claude/**"]}),
                check(3, triggers={"paths": [".claude/**"]}),  # lstrip("./") used to eat the dot
            ]
        }
    )
    assert [c.id for c in select_checks(k, ["migrations/001.py"], set(), "")] == ["chk-1"]
    assert {c.id for c in select_checks(k, [".claude/rules/a.md"], set(), "")} == {
        "chk-2",
        "chk-3",
    }


def test_compile_documents_reports_every_paid_call_before_it_fails() -> None:
    paid: list[ReviewResult] = []
    provider = FakeProvider(answers=["not json at all"], cost=0.25)
    with pytest.raises(ValueError, match="not JSON"):
        compile_documents(provider, docs_of(("a.md", "x")), [], "demo/p", on_call=paid.append)
    assert [r.cost_usd for r in paid] == [0.25]

    big = "x" * 300_000
    paid = []
    provider = FakeProvider(cost=2.0)
    with pytest.raises(KnowledgeBudgetError):
        compile_documents(
            provider,
            docs_of(("a.md", big), ("b.md", big), ("c.md", big)),
            [],
            "demo/p",
            max_cost_usd=3.0,
            on_call=paid.append,
        )
    assert [r.cost_usd for r in paid] == [2.0, 2.0]


class PagedTree:
    """A GitLab tree listing served 100 entries a page."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries
        self.pages: list[int] = []

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert path == "/projects/1001/repository/tree" and params is not None
        page, per_page = int(params["page"]), int(params["per_page"])
        self.pages.append(page)
        return self.entries[(page - 1) * per_page : page * per_page]


def test_claude_tree_sha_pages_past_the_first_hundred_entries() -> None:
    blobs = [{"id": f"{n:040x}", "name": f"f{n:03}.py", "type": "blob"} for n in range(150)]
    folder = {"id": "a" * 40, "name": ".claude", "type": "tree"}
    client = PagedTree([*blobs, folder])
    assert claude_tree_sha(client, 1001, "main") == "a" * 40  # type: ignore[arg-type]
    assert client.pages == [1, 2]

    without = PagedTree(blobs[:100])  # a full first page, then nothing
    assert claude_tree_sha(without, 1001, "main") is None  # type: ignore[arg-type]
    assert without.pages == [1, 2]
    small = PagedTree(blobs[:3])
    assert claude_tree_sha(small, 1001, "main") is None  # type: ignore[arg-type]
    assert small.pages == [1]


def test_check_without_triggers_is_never_selected() -> None:
    k = Knowledge.model_validate({"checks": [check(1)]})
    assert isinstance(k.checks[0], Check) and k.checks[0].triggers.empty
    assert select_checks(k, ["anything"], {"endpoint"}, "anything") == []


def test_select_sources_keeps_rules_and_skills_and_only_review_agents_and_commands() -> None:
    from app.review.claude_kit import KitFile
    from app.review.knowledge import select_sources

    def kit(kind: str, name: str, description: str = "") -> KitFile:
        return KitFile(
            path=f".claude/{kind}s/{name}.md",
            rel=f"{kind}s/{name}.md",
            source="repo",
            kind=kind,
            name=name,
            description=description,
            globs=[],
            text="x",
            chars=1,
        )

    files = [
        kit("rules", "critical/tenant-isolation"),
        kit("skill", "deploy-helm", "how to ship a release"),
        kit("instructions", "CLAUDE.md"),
        kit("command", "/review-mr-dev", "review a merge request like a lead"),
        kit("command", "/new-feature", "scaffold a feature"),
        kit("agent", "backend-reviewer", "reviews services and APIs"),
        kit("agent", "frontend-builder", "builds screens"),
        kit("agent", "sec-scan", "Security audit of a change"),
    ]
    kept, omitted = select_sources(files)
    assert [f.name for f in kept] == [
        "critical/tenant-isolation",
        "deploy-helm",
        "CLAUDE.md",
        "/review-mr-dev",
        "backend-reviewer",
        "sec-scan",
    ]
    assert [(o.path, o.reason) for o in omitted] == [
        (".claude/commands//new-feature.md", "not_review"),
        (".claude/agents/frontend-builder.md", "not_review"),
    ]
