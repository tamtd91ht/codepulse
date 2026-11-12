"""Bugs on Jira from test results (docs/specs/30a G3, docs/specs/30 §15).

- The issue made from a failed result: its summary, and a description in Jira wiki markup with
  the steps (expected vs actual), the environment, the comment, the attachments and a link back
  to the test in CodePulse.
- A bug's state: the synced `issues` row first, else a live read from Jira, kept ~5 minutes in
  this process (a status pill must not cost one Jira call per page view).
- "Cần retest": a failed test of an open run whose bugs are all closed.
- The per-dự án settings (TestProjectSettings): who records, approval, where bugs go.
"""

import re
import threading
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import Text, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.jira.scopes import jira_issue_in
from app.models import Issue, TestProjectSettings, TestResult, TestRun, TestRunTest
from app.testcases import content
from app.testcases import runs as rs

ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,9}-\d{1,7}$")
PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,9}$")
SUMMARY_MAX = 250  # Jira's limit is 255
DESCRIPTION_MAX = 30_000  # Jira's text field holds 32 767
STATUS_KEYS_MAX = 100
CACHE_TTL_S = 300.0
CACHE_MAX = 5_000
# Jira statusCategory.key of a finished issue.
DONE_CATEGORY = "done"
EN_DASH = chr(0x2013)  # the summary's separator (written as a code point: RUF001)

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


# -- project settings ------------------------------------------------------------------------------


def project_settings(session: Session, bp_id: int) -> TestProjectSettings:
    """The saved row, or an unsaved one with the defaults (not added to the session)."""
    row = session.get(TestProjectSettings, bp_id)
    if row is not None:
        return row
    return TestProjectSettings(
        business_project_id=bp_id,
        assigned_only=False,
        require_approval=False,
        jira_project_key=None,
        jira_issue_type="Bug",
        jira_components=[],
        jira_labels=[],
    )


def clean_list(values: Iterable[str], *, limit: int = 20, labels: bool = False) -> list[str]:
    """Trimmed, unique, at most `limit` names of at most 100 characters. A Jira label has no
    spaces: they become '-'."""
    out: list[str] = []
    for raw in values:
        name = rs.line(raw, 100)
        if labels:
            name = re.sub(r"\s+", "-", name)
        if name and name not in out:
            out.append(name)
    return out[:limit]


# -- the issue -------------------------------------------------------------------------------------


def summary(case_key: str | None, title: str) -> str:
    """`[C12] <title> - Failed`."""
    head = f"[{case_key}] " if case_key else ""
    return f"{head}{rs.line(title, SUMMARY_MAX)} {EN_DASH} Failed"[:SUMMARY_MAX]


# Jira wiki markup: links, images, lists, tables, macros, emphasis, colour, anchors. Escaped with
# a backslash. A backslash itself cannot be escaped (`\\` is a line break in Jira), so one typed
# by the user becomes a look-alike and never pairs with the escapes added here.
_WIKI_SPECIAL = re.compile(r"([\[\]!~*_^+\-?#|{}])")
_WIKI_LINE_START = re.compile(r"(?m)^(\s*)(h[1-6]|bq)\.")
_BACKSLASH_LOOKALIKE = chr(0xFF3C)
_NOFORMAT = re.compile(r"\{(\s*noformat)", re.IGNORECASE)


def _wiki(value: Any) -> str:
    """Plain text inside Jira wiki markup: every markup character escaped, and no heading or
    quote (`h1.`, `bq.`) at the start of a line."""
    text = str(value or "").strip().replace("\\", _BACKSLASH_LOOKALIKE)
    text = _WIKI_SPECIAL.sub(r"\\\1", text)
    return _WIKI_LINE_START.sub(r"\1\2\\.", text)


def _noformat(log: str) -> str:
    """A log for a {noformat} block: a `{noformat}` inside it would end the block early, so its
    brace is broken off (a space after it)."""
    return _NOFORMAT.sub(r"{ \1", log)


def attachment_url(dashboard_url: str, attachment_id: int) -> str:
    return f"{dashboard_url.rstrip('/')}/api/v1/testcases/attachments/{attachment_id}"


def test_link(dashboard_url: str, run: TestRun, test: TestRunTest) -> str:
    query = urlencode(
        {"project": run.business_project_id, "tab": "runs", "run": run.id, "test": test.id}
    )
    return f"{dashboard_url.rstrip('/')}/testcases?{query}"


def description(
    shown: dict[str, Any],
    result: TestResult | None,
    run: TestRun,
    test: TestRunTest,
    *,
    dashboard_url: str,
    extra: str | None = None,
) -> str:
    """The bug's body in Jira wiki markup."""
    parts: list[str] = []
    if extra and extra.strip():
        parts += [_wiki(extra), ""]
    if (shown.get("preconditions") or "").strip():
        parts += ["h3. Tiền điều kiện", _wiki(shown["preconditions"]), ""]
    if (shown.get("test_data") or "").strip():
        parts += ["h3. Dữ liệu test", _wiki(shown["test_data"]), ""]
    steps = list(shown.get("steps_list") or []) if shown.get("template") == "steps" else []
    step_results = list(result.step_results or []) if result is not None else []
    if steps:
        parts += ["h3. Các bước", "||#||Bước||Kết quả mong đợi||Trạng thái||Thực tế||"]
        for i, step in enumerate(steps):
            got = step_results[i] if i < len(step_results) else {}
            status = _wiki(got.get("status"))
            mark = f"*{status}*" if status == "failed" else status
            cells = [
                str(i + 1),
                _wiki(step.get("content")) or " ",
                _wiki(step.get("expected")) or " ",
                mark or " ",
                _wiki(got.get("actual")) or " ",
            ]
            parts.append("|" + "|".join(c.replace("\n", " ") for c in cells) + "|")
        parts.append("")
    else:
        if (shown.get("steps") or "").strip():
            parts += ["h3. Các bước", _wiki(shown["steps"]), ""]
        if (shown.get("expected") or "").strip():
            parts += ["h3. Kết quả mong đợi", _wiki(shown["expected"]), ""]
    if result is not None and (result.comment or "").strip():
        parts += ["h3. Kết quả thực tế", _wiki(result.comment), ""]
    if result is not None and (result.log or "").strip():
        log = _noformat((result.log or "")[:5_000])
        parts += ["h3. Log lỗi", "{noformat}", log, "{noformat}", ""]
    env: list[str] = [f"* Test run: {_wiki(run.name)} ({content.key('run', run.id)})"]
    if run.config:
        env.append(f"* Cấu hình: {_wiki(run.config)}")
    if result is not None and result.version:
        env.append(f"* Phiên bản / build: {_wiki(result.version)}")
    ci = dict(result.ci or {}) if result is not None else {}
    if ci.get("branch"):
        env.append(f"* Nhánh: {_wiki(ci['branch'])}")
    if ci.get("commit_sha"):
        env.append(f"* Commit: {_wiki(ci['commit_sha'])}")
    if ci.get("mr_iid"):
        env.append(f"* MR: \\!{_wiki(ci['mr_iid'])}")
    parts += ["h3. Môi trường", *env, ""]
    attachments = list(result.attachments or []) if result is not None else []
    if attachments:
        parts.append("h3. Đính kèm")
        for a in attachments[:20]:
            name = _wiki(a.get("name") or "tệp")
            if isinstance(a.get("id"), int):  # a file uploaded to CodePulse (§15 G6)
                parts.append(f"* [{name}|{attachment_url(dashboard_url, a['id'])}]")
                continue
            where = str(a.get("url") or a.get("path") or "")
            link = where if where.startswith(("http://", "https://")) else ""
            link = "" if any(c in link for c in "|[] \n") else link
            parts.append(f"* [{name}|{link}]" if link else f"* {name} ({_wiki(where)})")
        parts.append("")
    back = test_link(dashboard_url, run, test)
    test_key = content.key("test", test.id)
    parts.append(f"Tạo từ CodePulse: [{test_key} trong {content.key('run', run.id)}|{back}]")
    return "\n".join(parts)[:DESCRIPTION_MAX]


def target_result(
    session: Session, test: TestRunTest, result_id: int | None, *, failed_only: bool
) -> TestResult | None:
    """The result a bug goes to: the one asked for (of this test), else the latest failed one
    (`failed_only`) or the latest with a status."""
    if result_id is not None:
        row = session.get(TestResult, result_id)
        return row if row is not None and row.test_id == test.id else None
    query = select(TestResult).where(TestResult.test_id == test.id)
    query = query.where(
        TestResult.status == "failed" if failed_only else TestResult.status.is_not(None)
    )
    return session.scalar(query.order_by(TestResult.created_at.desc(), TestResult.id.desc()))


def add_defect(result: TestResult, key: str) -> bool:
    """Append `key` to the result's defects; False when it was there."""
    current = list(result.defects or [])
    if key in current:
        return False
    result.defects = [*current, key][: rs.DEFECTS_MAX]
    return True


# -- states ----------------------------------------------------------------------------------------


def synced_states(
    session: Session, keys: Iterable[str], bps: Iterable[int] | None = None
) -> dict[str, dict[str, Any]]:
    """key -> {state, title, web_url, source: "sync"} from the synced issues (one query); with
    `bps`, only the issues that count for those dự án (§15 scopes)."""
    wanted = sorted(set(keys))
    if not wanted:
        return {}
    query = select(Issue.key, Issue.state, Issue.title, Issue.web_url).where(Issue.key.in_(wanted))
    if bps is not None:
        query = query.where(jira_issue_in(bps))
    rows = session.execute(query).all()
    return {
        str(k): {"state": state or "opened", "title": title, "web_url": url, "source": "sync"}
        for k, state, title, url in rows
    }


def project_defect_keys(session: Session, bp_id: int, keys: Iterable[str]) -> set[str]:
    """The keys of `keys` written in the defects of a result of the dự án (one query)."""
    wanted = sorted(set(keys))
    if not wanted:
        return set()
    rows = session.scalars(
        select(TestResult.defects)
        .join(TestRunTest, TestRunTest.id == TestResult.test_id)
        .join(TestRun, TestRun.id == TestRunTest.run_id)
        .where(
            TestRun.business_project_id == bp_id,
            TestResult.defects.has_any(postgresql.array(wanted, type_=Text)),
        )
    )
    asked = set(wanted)
    return {str(k) for defects in rows for k in (defects or []) if k in asked}


def cached(key: str) -> dict[str, Any] | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit is None:
            return None
        if hit[0] < time.monotonic():
            _cache.pop(key, None)
            return None
        return dict(hit[1])


def remember(key: str, value: dict[str, Any]) -> None:
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            now = time.monotonic()
            for k in [k for k, (at, _v) in _cache.items() if at < now] or list(_cache)[:500]:
                _cache.pop(k, None)
        _cache[key] = (time.monotonic() + CACHE_TTL_S, dict(value))


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def from_jira(issue: dict[str, Any], web_url: str) -> dict[str, Any]:
    """A live issue as a state: closed when its status is in the "done" category."""
    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    category = (status.get("statusCategory") or {}).get("key")
    return {
        "state": "closed" if category == DONE_CATEGORY else "opened",
        "status": status.get("name"),
        "title": fields.get("summary"),
        "web_url": web_url,
        "source": "jira",
    }


def known_states(session: Session, keys: Iterable[str]) -> dict[str, str]:
    """key -> opened|closed, from the synced issues and the live cache (no Jira call)."""
    wanted = set(keys)
    out = {k: str(v["state"]) for k, v in synced_states(session, wanted).items()}
    for k in wanted - set(out):
        hit = cached(k)
        if hit and hit.get("state") in ("opened", "closed"):
            out[k] = str(hit["state"])
    return out


def retest_ids(
    session: Session, run: TestRun, tests: list[TestRunTest], defects: dict[int, list[str]]
) -> set[int]:
    """Tests of an open run to run again (AC2): failed now, with bugs, every one closed. One
    query for every bug of the page."""
    if run.completed_at is not None:
        return set()
    failed = [t for t in tests if t.status == "failed" and defects.get(t.id)]
    if not failed:
        return set()
    states = known_states(session, {k for t in failed for k in defects[t.id]})
    return {t.id for t in failed if all(states.get(k) == "closed" for k in defects[t.id])}
