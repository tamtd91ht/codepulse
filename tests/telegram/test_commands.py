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


def test_parse_review_keyed_form() -> None:
    assert parse_review(["type=mr", "id=412", "repo=global-api"]) == ReviewArgs(
        "mr", ref="412", repo_hint="global-api"
    )
    assert parse_review(["repo=global-api", "type=merge-request", "id=!412"]) == ReviewArgs(
        "mr", ref="412", repo_hint="global-api"
    )
    assert parse_review(["type=commit", "id=a1b2c3d"]) == ReviewArgs("commit", ref="a1b2c3d")
    assert parse_review(["type=commit", "user=tamtd"]) == ReviewArgs("last", people=("tamtd",))
    # Lists go through the button picker unless all=1 asks for everything at once.
    assert parse_review(["type=commit", "user=tamtd", "range=7d"]) == ReviewArgs(
        "today", people=("tamtd",), days=7, pick=True
    )
    assert parse_review(["type=commit", "range=today", "repo=api"]) == ReviewArgs(
        "today", repo_hint="api", days=1, pick=True
    )
    assert parse_review(["type=commit", "repo=api", "branch=dev"]) == ReviewArgs(
        "today", repo_hint="api", days=7, branch="dev", pick=True
    )
    assert parse_review(["type=commit", "repo=api", "all=1"]) == ReviewArgs(
        "today", repo_hint="api", days=7, pick=False
    )
    assert parse_review(["type=mr", "user=an"]) == ReviewArgs("lastmr", people=("an",))
    assert parse_review(["type=mr"]) == ReviewArgs("open", pick=True)
    url = "https://gitlab.company.vn/vihat/omicrm/api/-/merge_requests/412"
    assert parse_review(["type=mr", f"id={url}"]) == ReviewArgs(
        "mr", ref="412", repo_hint="vihat/omicrm/api"
    )
    # Errors are messages, not exceptions.
    assert "type" in str(parse_review(["repo=api"]))
    assert "range" in str(parse_review(["type=mr", "range=7d"]))
    assert "id" in str(parse_review(["type=mr", "id=abc"]))
    assert isinstance(parse_review(["type=commit", "range=soon"]), str)
    assert "Không hiểu" in str(parse_review(["type=commit", "extra"]))
    assert "bỏ user=" in str(parse_review(["type=mr", "id=5", "user=an"]))
    assert parse_review(["type=commit", "range=999d", "user=me"]).days == 90  # type: ignore[union-attr]
