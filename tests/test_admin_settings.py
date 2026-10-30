import pytest

from app.admin_settings import clean, normalize_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("abc.xyz", "https://abc.xyz"),
        ("abc.xyz/codepulse/", "https://abc.xyz/codepulse"),
        ("10.0.0.5:3000", "https://10.0.0.5:3000"),
        ("http://10.0.0.5:3000/", "http://10.0.0.5:3000"),
        (" https://codepulse.company.vn ", "https://codepulse.company.vn"),
    ],
)
def test_public_url_is_normalised(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "javascript:alert(1)", "https://", "a b.xyz", "https://x.y#top", "x.y:99999"]
)
def test_bad_public_urls_are_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(raw)


@pytest.mark.parametrize("model", ["claude-opus-5-5", "opus", "claude-sonnet-5-5[1m]"])
def test_model_ids(model: str) -> None:
    assert clean("llm_model", model) == model


@pytest.mark.parametrize("model", ["-p", "", "a;rm -rf /", 5])
def test_bad_model_ids(model: object) -> None:
    with pytest.raises(ValueError):
        clean("llm_model", model)


def test_monthly_budget_takes_whole_dollars_and_zero_means_no_limit() -> None:
    assert clean("monthly_llm_budget_usd", 0) == 0
    assert clean("monthly_llm_budget_usd", 350) == 350
    for bad in (-1, 1.5, "200", True):
        with pytest.raises(ValueError):
            clean("monthly_llm_budget_usd", bad)
