"""Built-in review checklists per stack (outside the spec, requested by the user 2026-10-26; see
PROGRESS.md): what a senior reviewer of that language checks on every change, so a repository
without its own skills or rules is still reviewed against more than the generic rubric.

Chosen by the files a change touches; both passes get them. They say WHAT to look for: the
rubric decides severity, and a repository's own rules win where they disagree.
"""

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import PurePosixPath

CHECKLIST_VERSION = 1
MAX_CHECKLIST_CHARS = 9_000

# Every change, whatever the language.
GENERAL = """\
- Every new endpoint, handler, job or query: who may call it, and is ownership / tenant checked
  on the object it reads or writes (not only "is logged in")?
- Input from outside (request, message, file, env): validated, size-limited, never concatenated
  into SQL, shell, paths, HTML, URLs, regexes or templates.
- Errors: no empty catch, no catch-and-continue that leaves data half-written, no error swallowed
  on money, auth or delete paths; the user sees a safe message, the log keeps the cause.
- Several writes that must all happen: one transaction, or an idempotent retry path.
- Outbound calls (HTTP, DB, queue, cache): timeout set, retries bounded, failure handled.
- Loops over data that grows (users, rows, files): bounded, paginated, no query per item.
- Secrets, tokens, PII: not hard-coded, not logged, not returned in responses or errors.
- Changed logic without a test that would fail on the old behaviour; tests that assert nothing.
- Dead code, leftover debug output, commented-out code, TODOs that hide a missing check."""

LANGUAGE_CHECKLISTS: dict[str, str] = {
    "Python": """\
- Mutable default arguments; late-binding closures in loops; `is` used for value comparison.
- Bare `except:` / `except Exception: pass`; exceptions re-raised without `from`.
- SQL built with f-strings or `%` instead of bound parameters; `text()` with user input.
- `subprocess` with `shell=True` or a joined string; `pickle`/`yaml.load`/`eval` on input.
- Blocking calls (requests, time.sleep, sync DB) inside `async def`; missing `await`.
- `requests`/`httpx` without `timeout=`; sessions/files not closed (no `with`).
- Naive vs aware datetimes mixed; float for money (use Decimal).
- ORM: lazy loads inside loops (N+1), commit inside a loop, session shared across threads.
- Type hints that lie (Optional returned where non-None is promised).""",
    "TypeScript": """\
- `any`, non-null `!` and `as` casts that hide a possibly undefined value; unchecked JSON.parse.
- Promises not awaited or not caught (floating promises); `forEach` with async callbacks.
- `==` instead of `===`; truthiness checks that treat 0 or "" as missing.
- React: missing/incorrect hook dependencies, state set during render, keys from array index on
  reorderable lists, effects without cleanup (timers, subscriptions, fetch abort).
- `dangerouslySetInnerHTML`, `innerHTML`, `eval`, building URLs or HTML from user input.
- Secrets or internal URLs in client bundles (`NEXT_PUBLIC_*`, env read in the browser).
- Server routes/actions: input validated (zod or similar), auth checked on the server.
- fetch without error handling for non-2xx; no timeout/abort on slow calls.""",
    "JavaScript": """\
- Promises not awaited or not caught; async callbacks in `forEach`; `==` instead of `===`.
- `innerHTML`, `eval`, `new Function`, template strings building HTML/SQL/shell from input.
- Prototype pollution via merging user objects; regexes vulnerable to catastrophic backtracking.
- Express/Koa: auth middleware order, missing input validation, errors leaking stack traces.
- Unbounded arrays/maps in module scope (memory leak in a long-running process).""",
    "Java": """\
- Resources not closed (use try-with-resources); `catch (Exception e) {}` or log-and-continue.
- String-concatenated JPQL/SQL/native queries; missing `@PreAuthorize`/ownership check on
  service methods reachable from controllers.
- `@Transactional` on private/self-invoked methods (no proxy, no transaction); checked
  exceptions not rolling back; long transactions around remote calls.
- JPA: N+1 from lazy collections in loops, `findAll()` without paging, entity returned
  directly from the API (over-exposure, lazy-init errors).
- `equals` without `hashCode`; mutable static state; non-thread-safe singletons (SimpleDateFormat,
  HashMap shared across threads).
- `Optional.get()` without check; NPE on unboxing; BigDecimal compared with `equals`.
- RestTemplate/WebClient/HttpClient without connect/read timeouts.""",
    "Kotlin": """\
- `!!` on values that can be null; platform types from Java treated as non-null.
- Coroutines: blocking calls on Dispatchers.Default/Main, GlobalScope, exceptions lost in
  `launch`, missing cancellation handling.
- Spring/JPA rules as for Java (transactions, N+1, timeouts, ownership checks).""",
    "Go": """\
- Errors ignored (`_ =`), not wrapped with context, or checked after using the value.
- Goroutines without a way to stop (leak), unbounded goroutine fan-out, missing WaitGroup.
- Data races: maps or slices shared across goroutines without a mutex.
- `context.Context` not passed down / not respected; HTTP clients without timeout
  (`http.DefaultClient`); response bodies not closed.
- `defer` inside loops; nil map writes; slices aliasing after `append`.
- SQL with `fmt.Sprintf`; `rows.Err()` not checked; rows not closed.""",
    "PHP": """\
- SQL built from strings instead of prepared statements / query builder bindings.
- Mass assignment (`$fillable`/`$guarded`), missing authorization (policies/gates) on actions.
- Output not escaped (`{!! !!}`, echo of input); `unserialize` on input; file uploads trusted
  by extension or client MIME type.
- Loose comparisons (`==`) on tokens/hashes; missing CSRF protection on state-changing forms.
- N+1 via lazy relations in loops (use eager loading); queries without limits.""",
    "C#": """\
- `async void`, `.Result`/`.Wait()` deadlocks, missing `ConfigureAwait` in libraries.
- `IDisposable` not disposed; `HttpClient` created per call (socket exhaustion).
- EF: N+1, client-side evaluation, missing `AsNoTracking` on read paths, raw SQL with input.
- Missing `[Authorize]`/resource checks on controller actions; model binding over-posting.""",
    "Rust": """\
- `unwrap()`/`expect()` on fallible input paths; panics in library code.
- `unsafe` blocks without a stated invariant; blocking calls inside async tasks.
- Unbounded channels/collections; locks held across `.await`.""",
    "Ruby": """\
- Strong parameters missing (mass assignment); authorization (Pundit/CanCan) skipped.
- SQL fragments with interpolation; `html_safe`/`raw` on input; `send`/`constantize` on input.
- N+1 queries (use `includes`); callbacks with side effects on save; jobs not idempotent.""",
    "Swift": """\
- Force unwraps / `try!` on data from outside; retain cycles in closures (missing `[weak self]`).
- UI work off the main thread; secrets in plist or code; ATS exceptions.""",
    "Dart": """\
- `!` on nullable values from outside; `setState` after dispose; unawaited futures.
- Secrets in the app bundle; HTTP without timeout; large lists without lazy builders.""",
    "Vue": """\
- `v-html` with user content; props mutated in the child; watchers without cleanup.
- Secrets or internal URLs in the client bundle; API errors not handled in the UI.""",
    "SQL": """\
- Migrations: data loss (drop/rename column with data, type narrowing), no backfill for a new
  NOT NULL column, long locks on big tables (index without CONCURRENTLY, table rewrites).
- A migration that does not match the model/code shipped with it; no way back (down migration).
- Queries: missing WHERE on UPDATE/DELETE, missing index for the new filter/join, SELECT *
  on wide tables, unbounded result sets.""",
}

# Changes that deserve their own list whatever the language.
PATH_CHECKLISTS: list[tuple[tuple[str, ...], str, str]] = [
    (
        ("*migration*", "*migrations/*", "*alembic/*", "*flyway*", "*liquibase*", "*.sql"),
        "Database migration",
        LANGUAGE_CHECKLISTS["SQL"],
    ),
    (
        (
            "Dockerfile*",
            "*docker-compose*",
            "*.github/workflows/*",
            "*.gitlab-ci*",
            "*k8s/*",
            "*helm/*",
            "*.tf",
        ),
        "Build / deploy configuration",
        """\
- Secrets in images, CI variables echoed, `latest` tags, containers running as root.
- Ports, resources and health checks; a deploy change that breaks rollback or zero-downtime.""",
    ),
    (
        ("*auth*", "*login*", "*session*", "*token*", "*permission*", "*security*", "*acl*"),
        "Authentication / authorization code",
        """\
- Constant-time comparison for secrets; tokens with expiry and audience checked; no secrets in
  URLs; password hashing with a slow KDF; session fixation and logout invalidation.
- Every role/permission branch tested, including the deny path.""",
    ),
]


def select_checklists(paths: Iterable[str], languages: Iterable[str]) -> list[str]:
    """Titles of the checklists a change needs, general first (for the review's basis)."""
    return [title for title, _ in _sections(list(paths), set(languages))]


def _sections(paths: list[str], languages: set[str]) -> list[tuple[str, str]]:
    sections = [("General", GENERAL)]
    for language in sorted(languages):
        if language in LANGUAGE_CHECKLISTS and language != "SQL":
            sections.append((language, LANGUAGE_CHECKLISTS[language]))
    lowered = [p.lower() for p in paths]
    names = [PurePosixPath(p).name for p in lowered]
    for patterns, title, text in PATH_CHECKLISTS:
        if any(
            fnmatch(p, pat) or fnmatch(n, pat)
            for p, n in zip(lowered, names, strict=True)
            for pat in patterns
        ):
            sections.append((title, text))
    return sections


def render_checklists(paths: Iterable[str], languages: Iterable[str]) -> str:
    """The checklist block of a review prompt, capped."""
    text = "\n\n".join(
        f"### {title}\n{body}" for title, body in _sections(list(paths), set(languages))
    )
    return text[:MAX_CHECKLIST_CHARS]
