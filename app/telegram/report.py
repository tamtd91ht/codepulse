"""`/report`: one person's (or everyone's) review results over a period, in the chat.

Grammar (order-free; every part optional):

    /report [user=<hint>|all] [time=<period>] [repo=<hint>]
    /report tamtd 04-2026,10-2026          bare words work too: a period, else a person

A period is one point or two joined by "," / ".." / "~" (from the start of the first to the
end of the second); `from=` / `to=` are accepted as well. A point is:

    04-2026  4/2026  2026-04               a month
    15-04-2026  15/04/2026  2026-04-15     a day
    2026                                   a year
    q2-2026  2026-q2                       a quarter
    today  yesterday  week  lastweek  month  lastmonth  year  7d  30d (hôm nay, tuần, tháng...)

No period means this month. The end is clipped to today; a period may cover at most
MAX_REPORT_DAYS. Pure functions: the bot (app/telegram/service.py) loads and renders.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

MAX_REPORT_DAYS = 366
MIN_YEAR, MAX_YEAR = 2000, 2100
EVERYONE = {"all", "*", "team", "everyone", "tatca", "tất-cả"}

REPORT_KEYS = {
    "user": "user",
    "author": "user",
    "u": "user",
    "time": "time",
    "range": "time",
    "period": "time",
    "t": "time",
    "from": "from",
    "to": "to",
    "repo": "repo",
    "project": "repo",
}

SEP = re.compile(r"\s*(?:,|\.\.|~)\s*")
MONTH_YEAR = re.compile(r"^(?P<m>\d{1,2})[-/.](?P<y>\d{4})$")
YEAR_MONTH = re.compile(r"^(?P<y>\d{4})[-/.](?P<m>\d{1,2})$")
DAY_MONTH_YEAR = re.compile(r"^(?P<d>\d{1,2})[-/.](?P<m>\d{1,2})[-/.](?P<y>\d{4})$")
YEAR_MONTH_DAY = re.compile(r"^(?P<y>\d{4})[-/.](?P<m>\d{1,2})[-/.](?P<d>\d{1,2})$")
YEAR = re.compile(r"^(?P<y>\d{4})$")
QUARTER = re.compile(r"^(?:q(?P<q1>[1-4])[-/.]?(?P<y1>\d{4})|(?P<y2>\d{4})[-/.]?q(?P<q2>[1-4]))$")
DAYS = re.compile(r"^(?P<n>\d{1,3})\s*(?:d|days?|ngay|ngày)$")

KEYWORDS = {
    "today": "today",
    "homnay": "today",
    "hômnay": "today",
    "yesterday": "yesterday",
    "homqua": "yesterday",
    "hômqua": "yesterday",
    "week": "week",
    "thisweek": "week",
    "tuan": "week",
    "tuần": "week",
    "tuannay": "week",
    "tuầnnày": "week",
    "lastweek": "lastweek",
    "tuantruoc": "lastweek",
    "tuầntrước": "lastweek",
    "month": "month",
    "thismonth": "month",
    "thang": "month",
    "tháng": "month",
    "thangnay": "month",
    "thángnày": "month",
    "lastmonth": "lastmonth",
    "thangtruoc": "lastmonth",
    "thángtrước": "lastmonth",
    "year": "year",
    "thisyear": "year",
    "nam": "year",
    "năm": "year",
    "namnay": "year",
    "nămnay": "year",
}


# Keywords a bare word may be (no "=" needed); the Vietnamese ones also are given names.
BARE_KEYWORDS = {
    "today",
    "yesterday",
    "week",
    "thisweek",
    "lastweek",
    "month",
    "thismonth",
    "lastmonth",
    "year",
    "thisyear",
}


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def label(self) -> str:
        if self.start.day == 1 and self.end == month_end(self.start):
            return f"tháng {self.start:%m/%Y}"
        if self.start == self.end:
            return f"{self.start:%d/%m/%Y}"
        return f"{self.start:%d/%m/%Y} - {self.end:%d/%m/%Y}"


@dataclass(frozen=True)
class ReportArgs:
    user: str | None = None  # a person hint; None = the sender; EVERYONE words = everyone
    time: str | None = None  # the period text, parsed by parse_period
    repo: str | None = None

    @property
    def everyone(self) -> bool:
        return self.user is not None and self.user.casefold() in EVERYONE


def month_end(day: date) -> date:
    first_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next - timedelta(days=1)


def _day(y: int, m: int, d: int) -> date | None:
    if not MIN_YEAR <= y <= MAX_YEAR:
        return None
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_point(text: str, today: date) -> Period | None:
    """One point of a period as [start, end] days, or None when the text is not one."""
    word = text.strip().casefold().replace(" ", "")
    if not word:
        return None
    if (kw := KEYWORDS.get(word)) is not None:
        monday = today - timedelta(days=today.weekday())
        first = today.replace(day=1)
        match kw:
            case "today":
                return Period(today, today)
            case "yesterday":
                return Period(today - timedelta(days=1), today - timedelta(days=1))
            case "week":
                return Period(monday, monday + timedelta(days=6))
            case "lastweek":
                return Period(monday - timedelta(days=7), monday - timedelta(days=1))
            case "month":
                return Period(first, month_end(first))
            case "lastmonth":
                prev = (first - timedelta(days=1)).replace(day=1)
                return Period(prev, month_end(prev))
            case _:
                return Period(today.replace(month=1, day=1), today.replace(month=12, day=31))
    if m := DAYS.match(word):
        n = int(m["n"])
        if n < 1:
            return None
        return Period(today - timedelta(days=n - 1), today)
    for pattern in (DAY_MONTH_YEAR, YEAR_MONTH_DAY):
        if m := pattern.match(word):
            day = _day(int(m["y"]), int(m["m"]), int(m["d"]))
            return Period(day, day) if day else None
    for pattern in (MONTH_YEAR, YEAR_MONTH):
        if m := pattern.match(word):
            first_day = _day(int(m["y"]), int(m["m"]), 1)
            return Period(first_day, month_end(first_day)) if first_day else None
    if m := QUARTER.match(word):
        year, quarter = int(m["y1"] or m["y2"]), int(m["q1"] or m["q2"])
        first_day = _day(year, 3 * quarter - 2, 1)
        if first_day is None:
            return None
        return Period(first_day, month_end(first_day.replace(month=3 * quarter)))
    if m := YEAR.match(word):
        first_day = _day(int(m["y"]), 1, 1)
        return Period(first_day, date(first_day.year, 12, 31)) if first_day else None
    return None


PERIOD_HELP = (
    "Thời gian: 04-2026 (tháng), 15-04-2026 (ngày), 2026, q2-2026, 7d, week, month, lastmonth;"
    " khoảng: time=04-2026,10-2026 hoặc from=01-04-2026 to=15-10-2026."
)


def parse_period(text: str | None, today: date) -> Period | str:
    """The period a text means, clipped to today; an error message when it means nothing."""
    if not text or not text.strip():
        first = today.replace(day=1)
        return Period(first, today)
    parts = [p for p in SEP.split(text.strip()) if p != ""]
    if not 1 <= len(parts) <= 2:
        return f"Không hiểu thời gian '{text}'. {PERIOD_HELP}"
    points: list[Period] = []
    for part in parts:
        point = parse_point(part, today)
        if point is None:
            return f"Không hiểu thời gian '{part}'. {PERIOD_HELP}"
        points.append(point)
    start, end = points[0].start, points[-1].end
    if start > end:
        return "Thời gian bắt đầu phải trước thời gian kết thúc."
    if start > today:
        return "Khoảng thời gian nằm trong tương lai; chưa có gì để báo cáo."
    end = min(end, today)
    if (end - start).days + 1 > MAX_REPORT_DAYS:
        return f"Một báo cáo tối đa {MAX_REPORT_DAYS} ngày; hãy chia nhỏ khoảng thời gian."
    return Period(start, end)


def parse_report(args: Sequence[str]) -> ReportArgs | str:
    """The words after /report; an error message on bad usage."""
    # "time=04-2026, 10-2026" arrives as two words: glue the separators back first.
    text = SEP.sub(lambda m: m.group(0).strip(), " ".join(args))
    pairs: dict[str, str] = {}
    free: list[str] = []
    for word in text.split():
        key, sep, value = word.partition("=")
        name = REPORT_KEYS.get(key.casefold()) if sep else None
        if name is None:
            free.append(word)
        elif name in pairs:
            return f"'{key}' bị lặp lại."
        else:
            pairs[name] = value.strip().strip("\"'")
    time = pairs.get("time")
    if "from" in pairs or "to" in pairs:
        if time:
            return "Dùng time= hoặc from=/to=, không dùng cả hai."
        time = f"{pairs.get('from') or pairs.get('to')},{pairs.get('to') or 'today'}"
    user = pairs.get("user") or None
    for word in free:
        # A bare word: a period when it reads as one unambiguously, else the person. Vietnamese
        # keywords double as given names (Tuấn, Nam, Thắng): those need time=.
        first = SEP.split(word)[0]
        plain = any(ch.isdigit() for ch in first) or first.casefold() in BARE_KEYWORDS
        if time is None and plain and parse_point(first, date(2026, 1, 1)) is not None:
            time = word
        elif user is None:
            user = word
        else:
            return (
                f"Không hiểu '{word}'. Cú pháp: /report [user=tên|all] [time=04-2026,10-2026]"
                " [repo=…]"
            )
    return ReportArgs(user=user, time=time or None, repo=pairs.get("repo") or None)
