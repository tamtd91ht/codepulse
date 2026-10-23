from dataclasses import dataclass

import pytest

from app.telegram.commands import (
    ReviewArgs,
    match_repo,
    parse,
    parse_gitlab_url,
    parse_review,
)


def test_parse_commands_and_bot_mentions() -> None:
    assert parse("/review mr 12 api") is not None
    cmd = parse("/review@CodePulseBot mr 12", "codepulsebot")
    assert cmd is not None and cmd.name == "review" and cmd.args == ("mr", "12")
    assert parse("/review@OtherBot mr 12", "codepulsebot") is None
    assert parse("hello there") is None
    assert parse("  /HELP ") is not None


def test_shorthands_expand_to_review() -> None:
    cmd = parse("/mr !12 omicrm/api")
    assert cmd is not None and cmd.name == "review" and cmd.args == ("mr", "!12", "omicrm/api")
    short, long = parse("/today @an"), parse("/review today @an")
    assert short is not None and long is not None
    assert (short.name, short.args) == (long.name, long.args) == ("review", ("today", "@an"))


def test_parse_review_mr_and_commit() -> None:
    assert parse_review(["mr", "!12", "api"]) == ReviewArgs("mr", ref="12", repo_hint="api")
    assert (
        parse_review(["mr", "abc"])
        == "Số MR phải là số (ví dụ 123 hoặc !123) hoặc link merge request."
    )
    assert parse_review(["commit", "a1b2c3d"]) == ReviewArgs("commit", ref="a1b2c3d")
    assert isinstance(parse_review(["commit", "xyz"]), str)
    assert isinstance(parse_review([]), str)
    assert isinstance(parse_review(["dance"]), str)
    assert isinstance(parse_review(["mr"]), str)


def test_parse_review_urls() -> None:
    url = "https://gitlab.company.vn/vihat/omicrm/api/-/merge_requests/412/diffs"
    assert parse_review(["mr", url]) == ReviewArgs("mr", ref="412", repo_hint="vihat/omicrm/api")
    commit = "https://gitlab.company.vn/vihat/omicrm/api/-/commit/0123abcd"
    assert parse_review(["commit", commit]) == ReviewArgs(
        "commit", ref="0123abcd", repo_hint="vihat/omicrm/api"
    )
    assert parse_review(["mr", commit]) == "Link đó là commit, không phải mr."
    assert parse_gitlab_url("not a url") is None


def test_parse_review_people_and_repo() -> None:
    assert parse_review(["today"]) == ReviewArgs("today")
    assert parse_review(["today", "api", "@an", "binh@x.vn"]) == ReviewArgs(
        "today", repo_hint="api", people=("@an", "binh@x.vn")
    )
    # A person-looking first word is a person, not a repo.
    assert parse_review(["last", "@an"]) == ReviewArgs("last", people=("@an",))
    assert parse_review(["last", "me"]) == ReviewArgs("last", people=("me",))
    assert parse_review(["open", "api"]) == ReviewArgs("open", repo_hint="api")


@dataclass
class Repo:
    path: str


REPOS = [Repo("vihat/omicrm/api"), Repo("vihat/omicrm/web"), Repo("vihat/shared/api-client")]


@pytest.mark.parametrize(
    ("hint", "expected"),
    [
        (None, ["vihat/omicrm/api", "vihat/omicrm/web", "vihat/shared/api-client"]),
        ("api", ["vihat/omicrm/api"]),  # suffix match beats substring match
        ("omicrm/web", ["vihat/omicrm/web"]),
        ("VIHAT/OMICRM/API", ["vihat/omicrm/api"]),
        ("omicrm", ["vihat/omicrm/api", "vihat/omicrm/web"]),
        ("nope", []),
    ],
)
def test_match_repo(hint: str | None, expected: list[str]) -> None:
    assert [r.path for r in match_repo(REPOS, hint, lambda r: r.path)] == expected
