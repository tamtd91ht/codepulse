"""Reading a pasted GitLab link, path or project ID (app/collector/gitlab/repo_ref.py)."""

import pytest

from app.collector.gitlab.repo_ref import RepoRefError, parse_repo_ref, resolve_repo_ref
from app.models import Connection


def conn(ident: int, base_url: str, name: str = "gl") -> Connection:
    return Connection(id=ident, name=name, base_url=base_url, token_encrypted="x")


GL = conn(1, "https://gitlab.vihat.vn")
OTHER = conn(2, "https://git.example.com/gitlab", "other")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1234", 1234),
        (" vihat/omicrm/api ", "vihat/omicrm/api"),
        ("https://gitlab.vihat.vn/vihat/omicrm/api", "vihat/omicrm/api"),
        ("https://gitlab.vihat.vn/vihat/omicrm/api/", "vihat/omicrm/api"),
        ("https://gitlab.vihat.vn/vihat/api.git", "vihat/api"),
        ("https://gitlab.vihat.vn/vihat/api/-/merge_requests/7?tab=diffs", "vihat/api"),
        ("https://GitLab.Vihat.vn/vihat/api/-/tree/main", "vihat/api"),
        ("git@gitlab.vihat.vn:vihat/sub/api.git", "vihat/sub/api"),
        ("ssh://git@gitlab.vihat.vn:2222/vihat/api.git", "vihat/api"),
        ("gitlab.vihat.vn/vihat/api", "vihat/api"),
    ],
)
def test_links_paths_and_ids_resolve_on_the_only_connection(text: str, expected: object) -> None:
    connection, ref = resolve_repo_ref(parse_repo_ref(text), [GL], None)
    assert (connection, ref) == (GL, expected)


def test_the_link_host_picks_the_connection_and_its_sub_path_is_dropped() -> None:
    both = [GL, OTHER]
    assert resolve_repo_ref(parse_repo_ref("https://gitlab.vihat.vn/a/b"), both, None) == (
        GL,
        "a/b",
    )
    link = "https://git.example.com/gitlab/team/app/-/commits/main"
    assert resolve_repo_ref(parse_repo_ref(link), both, None) == (OTHER, "team/app")
    with pytest.raises(RepoRefError, match="không nằm dưới"):
        resolve_repo_ref(parse_repo_ref("https://git.example.com/team/app"), both, None)


def test_ambiguous_or_foreign_references_are_refused() -> None:
    both = [GL, OTHER]
    with pytest.raises(RepoRefError, match="nhiều kết nối"):
        resolve_repo_ref(parse_repo_ref("1234"), both, None)
    assert resolve_repo_ref(parse_repo_ref("1234"), both, OTHER) == (OTHER, 1234)
    with pytest.raises(RepoRefError, match="chưa có kết nối GitLab nào tới"):
        resolve_repo_ref(parse_repo_ref("https://github.com/a/b"), both, None)
    with pytest.raises(RepoRefError, match="không phải kết nối"):
        resolve_repo_ref(parse_repo_ref("https://gitlab.vihat.vn/a/b"), both, OTHER)
    twin = conn(3, "https://gitlab.vihat.vn", "bot 2")
    with pytest.raises(RepoRefError, match="chọn một kết nối"):
        resolve_repo_ref(parse_repo_ref("https://gitlab.vihat.vn/a/b"), [GL, twin], None)
    assert resolve_repo_ref(parse_repo_ref("https://gitlab.vihat.vn/a/b"), [GL, twin], twin) == (
        twin,
        "a/b",
    )


@pytest.mark.parametrize(
    "text", ["", "  ", "0", "api", "https://gitlab.vihat.vn/vihat", "ftp://x/a/b", "a b/c"]
)
def test_text_that_is_not_a_repo_is_refused(text: str) -> None:
    with pytest.raises(RepoRefError):
        resolve_repo_ref(parse_repo_ref(text), [GL], None)


def test_a_dotted_group_stays_a_path_unless_it_names_a_connection_host() -> None:
    assert resolve_repo_ref(parse_repo_ref("my.group/sub/repo"), [GL], None) == (
        GL,
        "my.group/sub/repo",
    )
    assert resolve_repo_ref(parse_repo_ref("gitlab.vihat.vn/sub/repo"), [GL], None) == (
        GL,
        "sub/repo",
    )


def test_an_ssh_clone_url_works_on_a_gitlab_under_a_sub_path() -> None:
    link = "git@git.example.com:team/app.git"
    assert resolve_repo_ref(parse_repo_ref(link), [GL, OTHER], None) == (OTHER, "team/app")


def test_a_refused_link_does_not_echo_its_token() -> None:
    with pytest.raises(RepoRefError) as err:
        parse_repo_ref("ftp://user:glpat-secret@x/a/b")
    assert "glpat" not in str(err.value)
