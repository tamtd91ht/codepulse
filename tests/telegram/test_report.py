"""/report grammar: periods and arguments (app/telegram/report.py)."""

from datetime import date

import pytest

from app.telegram.report import Period, ReportArgs, parse_period, parse_report

TODAY = date(2026, 10, 24)  # a Saturday


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        (None, date(2026, 10, 1), TODAY),  # no time: this month so far
        ("04-2026", date(2026, 4, 1), date(2026, 4, 30)),
        ("4/2026", date(2026, 4, 1), date(2026, 4, 30)),
        ("2026-02", date(2026, 2, 1), date(2026, 2, 28)),
        ("04-2026,10-2026", date(2026, 4, 1), TODAY),  # the end is clipped to today
        ("04-2026..06-2026", date(2026, 4, 1), date(2026, 6, 30)),
        ("04-2026 ~ 06-2026", date(2026, 4, 1), date(2026, 6, 30)),
        ("15-04-2026", date(2026, 4, 15), date(2026, 4, 15)),
        ("15/04/2026,2026-05-03", date(2026, 4, 15), date(2026, 5, 3)),
        ("15-04-2026,06-2026", date(2026, 4, 15), date(2026, 6, 30)),  # day to month
        ("2025", date(2025, 1, 1), date(2025, 12, 31)),
        ("q2-2026", date(2026, 4, 1), date(2026, 6, 30)),
        ("2026Q3", date(2026, 7, 1), date(2026, 9, 30)),
        ("7d", date(2026, 10, 18), TODAY),
        ("today", TODAY, TODAY),
        ("hôm nay", TODAY, TODAY),
        ("week", date(2026, 10, 19), TODAY),
        ("lastweek", date(2026, 10, 12), date(2026, 10, 18)),
        ("tháng", date(2026, 10, 1), TODAY),
        ("lastmonth", date(2026, 9, 1), date(2026, 9, 30)),
        ("year", date(2026, 1, 1), TODAY),
    ],
)
def test_periods(text: str | None, start: date, end: date) -> None:
    assert parse_period(text, TODAY) == Period(start, end)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("13-2026", "Không hiểu"),
        ("31-02-2026", "Không hiểu"),
        ("abc", "Không hiểu"),
        ("1,2,3", "Không hiểu"),
        ("10-2026,04-2026", "trước"),
        ("12-2026", "tương lai"),
        ("2024,2026", "tối đa"),
        ("1899", "Không hiểu"),
    ],
)
def test_bad_periods(text: str, error: str) -> None:
    result = parse_period(text, TODAY)
    assert isinstance(result, str) and error in result


def test_labels() -> None:
    assert Period(date(2026, 4, 1), date(2026, 4, 30)).label() == "tháng 04/2026"
    assert Period(date(2026, 4, 1), date(2026, 10, 24)).label() == "01/04/2026 - 24/10/2026"
    assert Period(TODAY, TODAY).label() == "24/10/2026"


def test_report_arguments() -> None:
    assert parse_report(["user=tamtd", "time=04-2026,10-2026"]) == ReportArgs(
        "tamtd", "04-2026,10-2026"
    )
    # "time=04-2026, 10-2026" arrives as two words.
    assert parse_report(["user=tamtd", "time=04-2026,", "10-2026"]) == ReportArgs(
        "tamtd", "04-2026,10-2026"
    )
    assert parse_report(["tamtd", "04-2026"]) == ReportArgs("tamtd", "04-2026")
    assert parse_report(["04-2026", "tamtd"]) == ReportArgs("tamtd", "04-2026")
    assert parse_report([]) == ReportArgs()
    assert parse_report(["from=01-04-2026", "to=15-10-2026", "repo=api"]) == ReportArgs(
        None, "01-04-2026,15-10-2026", "api"
    )
    assert parse_report(["from=04-2026"]) == ReportArgs(None, "04-2026,today")
    assert parse_report(["user=all"]).everyone  # type: ignore[union-attr]
    assert isinstance(parse_report(["time=1", "from=2"]), str)
    assert isinstance(parse_report(["a", "b"]), str)
    assert isinstance(parse_report(["user=a", "user=b"]), str)
