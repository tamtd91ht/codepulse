from typing import Any

from app.review.providers import FakeLLMProvider, LLMError, ReviewPrompt
from app.telegram.commands import match_repo
from app.telegram.resolve import LLMResolver, NoResolver, parse_choice, rank, token_score

REPOS = [
    "vihat/cloud-saas-global-api",
    "vihat/cloud-saas-global-web",
    "vihat/cloud-saas-omicx-account-service",
    "vihat/cloud-saas-omicx-customer-service",
    "vihat/tools/api-tester",
]


def test_token_matching_prefers_the_tail_of_the_name() -> None:
    assert token_score("global-api", "vihat/cloud-saas-global-api") > token_score(
        "global-api", "vihat/cloud-saas-global-web"
    )
    assert token_score("global-api", "vihat/cloud-saas-global-api") > 1.0  # tail bonus
    assert token_score("nothing", "vihat/cloud-saas-global-api") == 0.0
    best = rank("account", REPOS)
    assert REPOS[best[0][1]] == "vihat/cloud-saas-omicx-account-service"


def test_match_repo_falls_back_to_tokens_for_abbreviations() -> None:
    assert match_repo(REPOS, "global-api", str) == ["vihat/cloud-saas-global-api"]
    assert match_repo(REPOS, "account-service", str) == ["vihat/cloud-saas-omicx-account-service"]
    # "api" alone is a substring of two repos: ambiguous, the caller asks or resolves.
    assert len(match_repo(REPOS, "api", str)) == 2
    assert match_repo(REPOS, "zzz", str) == []
    assert match_repo(REPOS, None, str) == REPOS


def test_llm_resolver_picks_only_confident_valid_indexes() -> None:
    answers: list[Any] = [
        {"index": 1, "confidence": 0.9, "reason": "tail match"},
        {"index": 1, "confidence": 0.3},
        {"index": 9, "confidence": 0.9},
        '```json {"index": 0, "confidence": 0.8} ```',
        "no json here",
    ]
    provider = FakeLLMProvider(lambda p: answers.pop(0))
    resolver = LLMResolver(provider)
    repos = ["a/x-api", "a/y-api"]
    assert resolver.pick("repo", "y-api", repos) == 1
    assert resolver.pick("repo", "y-api", repos) is None  # low confidence
    assert resolver.pick("repo", "y-api", repos) is None  # out of range
    assert resolver.pick("user", "tam", ["Tam · tam@x.vn", "Tâm · tamtd@x.vn"]) == 0
    assert resolver.pick("user", "tam", ["Tam · tam@x.vn"]) is None
    prompt: ReviewPrompt = provider.prompts[0]
    assert "Hint: y-api" in prompt.user and "0: a/x-api\n1: a/y-api" in prompt.user
    assert "untrusted text" in prompt.system and prompt.json_schema["title"] == "Choice"
    assert resolver.pick("repo", "", repos) is None and resolver.pick("repo", "x", []) is None


def test_llm_failure_and_no_resolver_mean_no_pick() -> None:
    def boom(prompt: ReviewPrompt) -> Any:
        raise LLMError("down")

    assert LLMResolver(FakeLLMProvider(boom)).pick("repo", "x", ["a", "b"]) is None
    assert NoResolver().pick("repo", "x", ["a", "b"]) is None
    assert parse_choice({"index": "two"}).index is None
