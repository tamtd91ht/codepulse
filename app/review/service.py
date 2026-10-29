"""Review one MR or one direct commit end to end (docs/specs/06, task 3.5; docs/specs/19 §4).

fetch diff -> prepare -> checkout once -> [1] agentic pass (or [1'] the no-tool pass as
fallback / below REVIEW_AGENT_MIN_LOC) -> post-process -> [2] independent verification ->
score, verdict -> store. Diffs are never stored; findings keep a short excerpt.

Transactions (docs/specs/19 §4.6): with `short_transactions` the service commits three times,
after the claim (the FOR UPDATE lock on the review row is released at once), after the
preparation (nothing stays open while the model works, minutes long) and after the save; the
MR comment is posted after that last commit. Without it, the default, the caller owns one
transaction around the whole review, which is what the integration tests do with their
rolled-back session.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.collector.gitlab.client import ArchiveTooLarge, GitLabClient, GitLabError
from app.models import (
    Account,
    BusinessProject,
    Commit,
    MergeRequest,
    Project,
    Review,
    ReviewFinding,
    User,
)
from app.models.review import CHANGE_TIME_UNKNOWN
from app.review import claude_kit, comment, knowledge
from app.review.agent import (
    agent_findings,
    build_agent_prompt,
    context_reader,
    review_chunk_in_repo,
)
from app.review.checklists import select_checklists
from app.review.checkout import CheckoutError, checkout
from app.review.claude_repo import (
    include_globs,
    load_repo_claude,
    load_shared_config,
    pack_instructions,
)
from app.review.live import LiveTracker, describe
from app.review.postprocess import Finding, postprocess
from app.review.prepare import ContextFetcher, PreparedDiff, chunk, prepare, redact
from app.review.profile import render_profile
from app.review.progress import NullProgress, ProgressSink
from app.review.prompt import (
    ReviewParseError,
    ReviewTarget,
    build_prompt,
    languages,
    review_chunk,
    target_header,
)
from app.review.providers.base import AgenticProvider, LLMError, LLMProvider, ReviewResult
from app.review.rubric import RUBRIC_LENS, RUBRIC_VERSION
from app.review.rules import collect_rules
from app.review.schema import AgentFinding, Intent, LLMFinding
from app.review.signals import detect_signals
from app.review.status import mark_fixed_findings, supersede_mr_reviews
from app.review.verify import skipped_status, verify
from app.scoring.formula import ScoredFinding, review_score, weights_for
from app.scoring.verdict import assess

log = logging.getLogger(__name__)

FINISHED = ("done", "skipped", "superseded")


@dataclass(frozen=True)
class _Target:
    """What a run was asked for, to name it in the live view when it does not happen."""

    kind: str  # mr | commit
    ref: str
    title: str
    sha: str
    mr_id: int | None = None
    commit_sha: str | None = None


def _off(project: Project) -> str:
    # An untracked repo is refused by the API and left out of the live view: only "off" shows.
    return "repo đã tắt review"


# Runs a person asked for (dashboard, Telegram, re-run): when one does not happen, the live view
# says so. Webhook and backfill duplicates are routine and stay silent.
ASKED_TRIGGERS = frozenset({"dashboard", "telegram", "rerun"})
VERIFY_TIMEOUT_S = 120.0  # docs/specs/19 §4.3: one verification call
# A "running" review older than this was left by a killed worker and may be taken over. It must
# exceed the RQ job timeout (app.workers.review_jobs.review_job_timeout: 3 x REVIEW_TIMEOUT_S +
# VERIFY_MAX_FINDINGS x 120 s + 10 min, 58 min with the defaults) by 5 min; the worker derives
# `ReviewSettings.stale_running` from its settings, this is the default-settings value.
STALE_RUNNING = timedelta(minutes=63)
# The PROFILE block of a prompt when the compiled knowledge carries the domain already.
KNOWLEDGE_DOMAIN_NOTE = "(see the DOMAIN section of PROJECT KNOWLEDGE below)"


# The skip reason of a diff over MAX_REVIEW_LOC; a lead's rerun may go past it (rerun API).
TOO_LARGE = "MR quá lớn"


@dataclass
class ReviewSettings:
    ignore_paths: list[str]
    sensitive_paths: list[str]
    max_review_loc: int
    max_tokens_per_chunk: int
    min_confidence: float
    monthly_budget_usd: float = 0.0  # 0 = unlimited
    # A lead's rerun past "MR quá lớn": the size check is skipped for that one run.
    ignore_loc_limit: bool = False
    # .claude files quoted to the model: per file, first pass total, deep pass total.
    claude_file_chars: int = claude_kit.MAX_FILE_CHARS
    fast_guidance_chars: int = 120_000
    deep_instructions_chars: int = 300_000
    scoring_weights: Mapping[str, float] | None = None
    # docs/specs/19 §4.2, §8: [1] the agentic main pass (needs an AgenticProvider).
    agent_review: bool = True
    agent_min_loc: int = 20  # below: [1'] the no-tool pass, then [2]
    agent_max_turns: int = 40
    agent_timeout_s: float = 480.0
    agent_max_archive_bytes: int = 300 * 1024 * 1024
    chunk_tokens: int = 60_000  # [1] chunks only above this; [1'] uses max_tokens_per_chunk
    # docs/specs/19 §4.3: [2] independent verification.
    verify: bool = True
    verify_max_findings: int = 12
    verify_max_turns: int = 15
    verify_timeout_s: float = VERIFY_TIMEOUT_S
    verify_min_confidence_unverified: float = 0.7
    knowledge_tokens: int = 12_000  # docs/specs/19 §3.1: RK budget in the prompt
    stale_running: timedelta = STALE_RUNNING  # see STALE_RUNNING


@dataclass
class PassOutcome:
    """What the main pass ([1] agentic or [1'] no-tool) produced, before verification."""

    mode: str  # agent | fast
    findings: list[Finding] = field(default_factory=list)
    summary: str = ""
    intent: dict[str, Any] | None = None
    applied_rules: list[dict[str, Any]] = field(default_factory=list)
    project_fit: list[str] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    chunks: int = 0
    turns: int = 0
    cost_usd: float = 0.0
    failed: str | None = None  # the no-tool pass failed too: the review fails with this error


def _paths(entries: list[dict[str, Any]]) -> set[str]:
    return {str(e.get("new_path") or e.get("old_path") or "") for e in entries} - {""}


def merge_intents(intents: list[Intent]) -> dict[str, Any] | None:
    """One intent block per review: the first chunk's summary, the union of concerns; the
    verdicts (matches_intent, altitude) are the worst any chunk reported."""
    if not intents:
        return None
    concerns: list[str] = []
    for intent in intents:
        for concern in intent.concerns:
            if concern not in concerns:
                concerns.append(concern)
    matches = [i.matches_intent for i in intents if i.matches_intent is not None]
    altitudes = [i.altitude for i in intents if i.altitude is not None]
    altitude = next(
        (a for a in ("under", "over") if a in altitudes), altitudes[0] if altitudes else None
    )
    return {
        "summary": redact(next((i.summary for i in intents if i.summary), ""))[:1500],
        "matches_intent": (all(matches) if matches else None),
        "altitude": altitude,
        "concerns": [redact(c) for c in concerns[:5]],
    }


class ReviewService:
    def __init__(
        self,
        session: Session,
        client: GitLabClient,
        provider: LLMProvider,
        settings: ReviewSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        agent: AgenticProvider | None = None,
        progress: ProgressSink | None = None,
        short_transactions: bool = False,
    ) -> None:
        """`short_transactions` (docs/specs/19 §4.6, the workers): the service commits after
        the claim, the preparation and the save, so no lock or transaction outlives a step
        while the model works. Off by default: the caller then owns one transaction around
        the review (the integration tests pass a session whose work is rolled back)."""
        self._session = session
        self._progress: ProgressSink = progress or NullProgress()
        self._unclaimed: str | None = None  # why the last _claim took nothing (None: running)
        self._pending_basis: dict[int, dict[str, Any]] = {}
        self._client = client
        self._provider = provider
        self._settings = settings
        self._clock = clock
        self._agent = agent  # tools in a checkout ([1], [2]); None = no-tool reviews only
        self._short = short_transactions

    # -- entry points --------------------------------------------------------------------------

    def review_mr(
        self,
        project: Project,
        mr: MergeRequest,
        head_sha: str,
        *,
        trigger: str = "webhook",
        requested_by: int | None = None,
        force: bool = False,
        expected_round: int | None = None,
    ) -> Review | None:
        target = _Target("mr", str(mr.iid), mr.title or "", head_sha, mr_id=mr.id)
        if not (project.review_enabled and project.tracked):
            self._not_run(project, trigger, requested_by, _off(project), target)
            return None
        if force and (mr.state != "opened" or mr.head_sha != head_sha):
            # Checked again at run time: the MR may have been merged or pushed since the
            # request, and an old head is history (docs/specs/17 §Lịch sử).
            log.info("forced review dropped: mr not open at this head", extra={"mr_id": mr.id})
            reason = "MR không còn mở ở head này (đã merge, đóng hoặc có push mới)"
            self._not_run(project, trigger, requested_by, reason, target)
            return None
        review = self._claim(
            "mr",
            head_sha,
            mr_id=mr.id,
            changed_at=mr.created_at_src,
            author=self._user_of(mr.author_account_id),
            trigger=trigger,
            requested_by=requested_by,
            force=force,
            expected_round=expected_round,
        )
        if review is None:
            self._commit_step("claim")  # nothing claimed: release the row (and the sync's rows)
            if self._unclaimed:
                self._not_run(project, trigger, requested_by, self._unclaimed, target)
            return None
        self._begin(review, project, "mr", str(mr.iid), mr.title or "", head_sha)
        self._commit_step("claim")
        return self._tracked(review, lambda: self._mr_claimed(review, project, mr, head_sha))

    def _mr_claimed(
        self, review: Review, project: Project, mr: MergeRequest, head_sha: str
    ) -> Review:
        self._step(review.id, "diff")
        description = ""
        try:
            info = self._client.merge_request(project.external_id, mr.iid)
            description = info.get("description") or ""
            entries = self._client.mr_diffs(project.external_id, mr.iid)
        except GitLabError as exc:
            self._fail(review, f"gitlab: {exc}")
            self._commit_step("save")
            return review
        target = ReviewTarget(
            project.path_with_namespace,
            mr.title or "",
            description,
            conventions=self._rules(review, project, _paths(entries)),
        )
        result = self._run(review, project, target, entries, head_sha)
        post = result.status == "done" and bool(project.post_mr_comment)
        findings: list[ReviewFinding] = []
        if result.status == "done":
            supersede_mr_reviews(self._session, project, mr.iid, head_sha)
            # A re-judgment of the same head is not a fix: only a new push can fix findings.
            if review.round == 1:
                mark_fixed_findings(self._session, mr.id, result.id)
            if post:
                findings = list(
                    self._session.scalars(
                        select(ReviewFinding).where(ReviewFinding.review_id == result.id)
                    )
                )
        self._commit_step("save")
        if post:
            # After the save: the comment points at a review that is already there.
            body = comment.render(result, findings)
            comment.post_or_update(self._client, project.external_id, mr.iid, body)
        return result

    def review_commit(
        self,
        project: Project,
        commit: Commit,
        *,
        trigger: str = "webhook",
        requested_by: int | None = None,
        force: bool = False,
        expected_round: int | None = None,
    ) -> Review | None:
        first_line = (commit.message or "").strip().splitlines()[:1]
        title = first_line[0] if first_line else commit.sha
        target = _Target("commit", commit.sha, title, commit.sha, commit_sha=commit.sha)
        if not (project.review_enabled and project.tracked):
            self._not_run(project, trigger, requested_by, _off(project), target)
            return None
        review = self._claim(
            "commit",
            commit.sha,
            commit_sha=commit.sha,
            changed_at=commit.authored_at,
            author=self._user_of(commit.author_account_id),
            trigger=trigger,
            requested_by=requested_by,
            force=force,
            expected_round=expected_round,
        )
        if review is None:
            self._commit_step("claim")
            if self._unclaimed:
                reason = self._unclaimed
                self._not_run(project, trigger, requested_by, reason, target)
            return None
        self._begin(review, project, "commit", commit.sha, title, commit.sha)
        self._commit_step("claim")
        return self._tracked(review, lambda: self._commit_claimed(review, project, commit, title))

    def _commit_claimed(
        self, review: Review, project: Project, commit: Commit, title: str
    ) -> Review:
        self._step(review.id, "diff")
        try:
            entries = self._client.commit_diff(project.external_id, commit.sha)
        except GitLabError as exc:
            self._fail(review, f"gitlab: {exc}")
            self._commit_step("save")
            return review
        target = ReviewTarget(
            project.path_with_namespace,
            title,
            kind="Commit",
            conventions=self._rules(review, project, _paths(entries)),
        )
        result = self._run(review, project, target, entries, commit.sha)
        self._commit_step("save")
        return result

    # -- steps ---------------------------------------------------------------------------------

    def _commit_step(self, step: str) -> None:
        """docs/specs/19 §4.6: end one of the three short transactions (claim, prepare, save).
        Without `short_transactions` the caller owns the single transaction: no-op."""
        if not self._short:
            return
        self._session.commit()
        log.debug("review transaction committed", extra={"step": step})

    def _tracked(self, review: Review, work: Callable[[], Review]) -> Review:
        """Run a claimed review and tell the live view how it ended (None: it crashed)."""
        try:
            result = work()
        except BaseException as exc:
            self._progress.end(review.id, None)
            self._crashed(review, exc)
            raise
        self._progress.end(review.id, result.status, result.error)
        return result

    def _crashed(self, review: Review, exc: BaseException) -> None:
        """With short transactions the claim is committed already, so an unexpected error (or
        RQ's job timeout, a BaseException) would leave the row `running` until
        `stale_running` frees it: mark it failed in a short transaction of its own, so it can
        be rerun at once. A worker killed outright still relies on the takeover. In
        single-transaction mode the caller's rollback undoes the claim: nothing to do."""
        if not self._short:
            return
        try:
            self._session.rollback()
            self._fail(review, f"crash: {exc.__class__.__name__}: {str(exc)[:200]}")
            self._session.commit()
        except Exception:
            log.exception("crashed review not marked failed", extra={"review_id": review.id})

    def _step(self, review_id: int, stage: str, detail: str = "", **extra: Any) -> None:
        """A stage of a running review: a log line (so `docker compose logs worker` shows a
        long review is alive) and the live view on the dashboard."""
        log.info("review stage", extra={"review_id": review_id, "stage": stage, "detail": detail})
        self._progress.stage(review_id, stage, detail, **extra)

    def _live(self, review_id: int, tracker: LiveTracker, event: dict[str, Any]) -> None:
        """One stream-json event of the agentic pass: into the live view, and the latest action
        into the running review's stage line."""
        entries = tracker.entries(event)
        if not entries:
            return
        self._progress.event(review_id, entries)
        last = entries[-1]
        self._progress.stage(
            review_id,
            "agent",
            f"lượt {tracker.turns}/{self._settings.agent_max_turns} · {describe(last)}",
            turns=tracker.turns,
            files_read=len(tracker.reads),
        )

    def _begin(
        self, review: Review, project: Project, kind: str, ref: str, title: str, sha: str
    ) -> None:
        """Announce a claimed review to the live progress view (target and people only)."""
        author = self._session.get(User, review.author_user_id) if review.author_user_id else None
        requester = (
            self._session.get(User, review.requested_by_user_id)
            if review.requested_by_user_id
            else None
        )
        log.info(
            "review started",
            extra={
                "review_id": review.id,
                "project_id": project.id,
                "kind": kind,
                "ref": ref[:12],
                "trigger": review.trigger,
                "round": review.round,
            },
        )
        self._progress.start(
            review.id,
            {
                "kind": kind,
                "project_id": project.id,
                "project": project.path_with_namespace,
                "ref": ref,
                "sha": sha,
                "title": title[:120],
                "author_user_id": review.author_user_id,
                "author": (author.full_name if author else None),
                "requested_by": (requester.full_name if requester else None),
                "trigger": review.trigger,
                "round": review.round,
                "started_at": self._clock().isoformat(),
            },
        )

    def _not_run(
        self,
        project: Project,
        trigger: str,
        requested_by: int | None,
        reason: str,
        target: "_Target",
    ) -> None:
        """A run someone asked for that does not happen: shown in the live view as skipped, with
        why, under the latest review of the target, so the page that asked does not wait for
        nothing. A review running elsewhere already shows there and is left alone."""
        if trigger not in ASKED_TRIGGERS:
            return
        review = self._session.scalars(
            select(Review)
            .where(
                Review.target_type == target.kind,
                Review.mr_id.is_not_distinct_from(target.mr_id),
                Review.commit_sha.is_not_distinct_from(target.commit_sha),
                Review.reviewed_sha == target.sha,
            )
            .order_by(Review.id.desc())
            .limit(1)
        ).first()
        if review is None or review.status == "running":
            return  # no row to name it by (accepted), or the live view shows that run
        log.info("asked review not run", extra={"review_id": review.id, "reason": reason})
        author = self._session.get(User, review.author_user_id) if review.author_user_id else None
        requester = self._session.get(User, requested_by) if requested_by else None
        self._progress.note_ended(
            review.id,
            {
                "kind": target.kind,
                "project_id": project.id,
                "project": project.path_with_namespace,
                "ref": target.ref,
                "sha": target.sha,
                "title": target.title[:120],
                "author_user_id": review.author_user_id,
                "author": (author.full_name if author else None),
                "requested_by": (requester.full_name if requester else None),
                "trigger": trigger,
                "round": review.round,
                "started_at": self._clock().isoformat(),
            },
            "skipped",
            reason,
        )

    def _business(self, project: Project) -> BusinessProject | None:
        if project.business_project_id is None:
            return None
        return self._session.get(BusinessProject, project.business_project_id)

    def _rules(self, review: Review, project: Project, changed: set[str]) -> str:
        """Team rules for the prompt (docs/specs/17): dự án + repo text, plus files read from the
        repo's default branch, never from the change under review (its author could rewrite
        them in the same commit); a rules file touched by the diff is left out."""
        business = self._business(project)
        ref = project.default_branch or "HEAD"

        def fetch(path: str) -> str | None:
            try:
                return self._client.file_raw(project.external_id, path, ref)
            except GitLabError:
                return None

        text, sources = collect_rules(business, project, fetch, skip=changed)
        # Kept aside: it becomes the review's basis only if the LLM pass actually starts
        # (a skipped or budget-blocked review was not checked against anything).
        self._pending_basis[review.id] = {
            "rubric": {"version": RUBRIC_VERSION, "lens": list(RUBRIC_LENS)},
            "rules": sources,
            "rules_ref": ref,
        }
        return text

    def _user_of(self, account_id: int | None) -> int | None:
        if account_id is None:
            return None
        account = self._session.get(Account, account_id)
        return account.user_id if account else None

    def _claim(
        self,
        target_type: str,
        sha: str,
        author: int | None,
        trigger: str = "webhook",
        requested_by: int | None = None,
        force: bool = False,
        expected_round: int | None = None,
        **target: Any,
    ) -> Review | None:
        """Get-or-create the review row; a finished or running review of the same sha is final
        unless `force`: then round N+1 starts, and round N is marked superseded once N+1 is
        done (docs/specs/17 §Lịch sử). History is never deleted. `expected_round` is the round
        the requester saw: a duplicate or concurrent forced job finds a newer one and stops.
        When it returns None, `_unclaimed` says why (None: running elsewhere)."""
        self._unclaimed = None
        if requested_by is not None and self._session.get(User, requested_by) is None:
            requested_by = None  # the requester left between enqueue and run
        values = {
            "target_type": target_type,
            "mr_id": target.get("mr_id"),
            "commit_sha": target.get("commit_sha"),
            "reviewed_sha": sha,
            "changed_at": target.get("changed_at") or CHANGE_TIME_UNKNOWN,
            "rubric_version": RUBRIC_VERSION,
            "author_user_id": author,
            "status": "queued",
            "trigger": trigger,
            "requested_by_user_id": requested_by,
        }
        self._session.execute(
            insert(Review).values(**values).on_conflict_do_nothing(constraint="uq_reviews_target")
        )
        review = self._session.scalars(
            select(Review)
            .where(
                Review.target_type == target_type,
                Review.mr_id.is_not_distinct_from(values["mr_id"]),
                Review.commit_sha.is_not_distinct_from(values["commit_sha"]),
                Review.reviewed_sha == sha,
                Review.rubric_version == RUBRIC_VERSION,
            )
            .order_by(Review.round.desc())
            .with_for_update()
        ).first()
        assert review is not None  # noqa: S101 - the insert above guarantees a row
        stale = (
            review.status == "running"
            and review.started_at is not None
            and review.started_at < self._clock() - self._settings.stale_running
        )
        if force and review.status == "done":
            if review.superseded_by is not None or (
                expected_round is not None and review.round != expected_round
            ):
                # Another forced run took this round already (FOR UPDATE re-reads the row but
                # cannot see the newer one): one paid rerun per request.
                log.info("forced review already taken", extra={"review_id": review.id})
                self._unclaimed = "đã có lần review mới hơn cho thay đổi này"
                return None
            previous = review
            review = Review(**{**values, "round": previous.round + 1})
            self._session.add(review)
            self._session.flush()
            # A pointer now; previous stays "done" (and counted) until the new round succeeds.
            previous.superseded_by = review.id
            log.info(
                "review round started",
                extra={"review_id": review.id, "round": review.round, "previous": previous.id},
            )
        elif review.status in FINISHED or (review.status == "running" and not stale):
            log.info(
                "review already handled", extra={"review_id": review.id, "status": review.status}
            )
            self._unclaimed = (
                None  # running elsewhere: the live view shows that run
                if review.status == "running"
                else f"đã có kết quả ({review.status}, lần {review.round}); "
                "dùng ↻ Review lại để chạy lần mới"
            )
            return None
        review.status, review.error = "running", None
        review.basis = None  # a failed/skipped row taken over: its old basis is not this run's
        review.deep, review.mode = None, "fast"
        review.prompt_tokens, review.completion_tokens, review.cost_usd = 0, 0, Decimal(0)
        review.started_at = self._clock()
        # A failed/skipped/stale row taken over records what asked for this run.
        review.trigger, review.requested_by_user_id = trigger, requested_by
        self._session.flush()
        return review

    def _finish(self, review: Review, status: str, error: str | None = None) -> Review:
        review.status, review.error = status, error
        review.finished_at = self._clock()
        # `deep` and `basis` are filled in place while the review runs (and the profile step
        # flushes in between): tell the session they changed, or the last keys are lost.
        for column in ("deep", "basis"):
            if getattr(review, column) is not None:
                flag_modified(review, column)
        if status == "done" and review.round > 1:
            # The new round replaces the one it was forced from, only now that it has a result.
            self._session.execute(
                update(Review)
                .where(Review.superseded_by == review.id, Review.status == "done")
                .values(status="superseded")
            )
        self._session.flush()
        log.info(
            "review finished", extra={"review_id": review.id, "status": status, "error": error}
        )
        return review

    def _fail(self, review: Review, error: str) -> Review:
        return self._finish(review, "failed", error[:1000])

    def _run(
        self,
        review: Review,
        project: Project,
        target: ReviewTarget,
        entries: list[dict[str, Any]],
        sha: str,
    ) -> Review:
        settings = self._settings
        if project.allow_external_llm is False:
            # Phase 0: no local provider in v1, so these projects are not reviewed.
            return self._finish(review, "skipped", "project does not allow external LLM")

        prepared = prepare(entries, settings.ignore_paths, settings.sensitive_paths)
        review.loc_reviewed = prepared.loc
        if not prepared.files:
            return self._finish(review, "skipped", "no reviewable files")
        over_limit = prepared.loc > settings.max_review_loc
        if over_limit and not settings.ignore_loc_limit:
            return self._finish(review, "skipped", f"{TOO_LARGE}: {prepared.loc} LOC")
        if not self._within_budget():
            # "failed", not "skipped": the review can be re-run once the budget allows it.
            return self._fail(review, "monthly LLM budget reached")

        # What the project is and how its team reviews: the compiled review knowledge
        # (docs/specs/19; its DOMAIN is the profile) or, while a project has none, the cached
        # profile and the .claude files this change needs (docs/specs/17/18, kept as the
        # fallback of lộ trình step 1).
        target.knowledge, knowledge_info, has_domain = self._knowledge_block(project, prepared)
        target.profile, profile_source = self._profile_block(project, has_domain)
        guidance: list[dict[str, Any]] = []
        if not target.knowledge:
            target.guidance, guidance = self._guidance(review, project, target, prepared, sha)
        review.basis = self._pending_basis.pop(review.id, None)
        if review.basis is not None and over_limit:
            # Reviewed past MAX_REVIEW_LOC on a lead's request: say so where the run is described.
            review.basis["loc_limit"] = {"loc": prepared.loc, "max": settings.max_review_loc}
        if review.basis is not None:
            review.basis["checklists"] = select_checklists(
                [f.path for f in prepared.files], self._languages(prepared)
            )
            review.basis["fast_guidance"] = guidance[:40]
            review.basis["fast_profile"] = bool(target.profile)
            review.basis["profile_source"] = profile_source
            if profile_source == "cached" and project.profile is not None:
                review.basis["profile"] = {
                    "sha": (project.profile_sha or "")[:12],
                    "built_at": project.profile_at.isoformat() if project.profile_at else None,
                    "confidence": project.profile.get("confidence"),
                }
            if knowledge_info:
                review.basis["knowledge"] = knowledge_info

        review.deep = {}
        agent_on = self._agent is not None and settings.agent_review and project.deep_review
        agent_pass = agent_on and prepared.loc >= settings.agent_min_loc
        if agent_on and not agent_pass:
            review.deep["skipped"] = f"diff dưới {settings.agent_min_loc} LOC"
        # Everything the passes need from the database is loaded: end the transaction so none
        # stays open while the model works (docs/specs/19 §4.6).
        self._commit_step("prepare")
        outcome: PassOutcome | None = None
        if self._agent is not None and (agent_pass or self._verify_on()):
            # One checkout for the whole review: the agentic pass, the ±30 lines of context and
            # the verification all read from it (docs/specs/19 §4.1).
            self._step(review.id, "checkout", "tải mã nguồn")
            try:
                with checkout(
                    self._client,
                    project.external_id,
                    sha,
                    max_archive_bytes=settings.agent_max_archive_bytes,
                    sensitive_paths=settings.sensitive_paths,
                ) as (root, stats):
                    review.deep["files_seen"] = stats.files
                    outcome = self._passes(review, project, target, prepared, sha, root, agent_pass)
            except (ArchiveTooLarge, CheckoutError) as exc:
                review.deep["error"] = "checkout"
                review.deep["detail"] = str(exc)[:200]
                log.warning("checkout failed; no-tool review", extra={"review_id": review.id})
        if outcome is None:
            outcome = self._passes(review, project, target, prepared, sha, None, False)
        if outcome.failed is not None:
            return self._fail(review, outcome.failed)
        return self._store(review, prepared, outcome)

    @staticmethod
    def _languages(prepared: PreparedDiff) -> set[str]:
        return {lang for piece in chunk(prepared, 10**9) for lang in languages(piece).split(", ")}

    def _profile_block(self, project: Project, has_domain: bool) -> tuple[str, str]:
        """The PROFILE block of the prompt and where it came from (`knowledge`, `cached`,
        `none`). The knowledge's DOMAIN section is the profile of a review; the cached
        `project.profile` is the fallback while a repo has no knowledge or its knowledge has
        no domain. The review path never builds a profile: `refresh_profile_job` (the Admin
        button, from the default branch) is its only writer, because the reviewed sha is the
        author's and must not become the premise it is judged by."""
        if has_domain:
            return KNOWLEDGE_DOMAIN_NOTE, "knowledge"
        if project.profile:
            return render_profile(project.profile), "cached"
        return "", "none"

    def _knowledge_block(
        self, project: Project, prepared: PreparedDiff
    ) -> tuple[str, dict[str, Any], bool]:
        """The compiled review knowledge for the prompt (docs/specs/19 §3), the basis entry
        describing it, and whether it carries a DOMAIN section (then it is the profile too).
        Empty when the repo has no RK yet: then the .claude guidance path of docs/specs/17/18
        is used (the review job builds the first RK before the review)."""
        ref = project.default_branch or "HEAD"
        try:
            claude_sha = knowledge.claude_tree_sha(self._client, project.external_id, ref)
        except Exception:  # staleness is informational; never block a review on it
            claude_sha = None
        loaded = knowledge.load_for_review(
            self._session,
            project,
            claude_sha,
            None,  # shared sha: unknown here, so the shared part never counts as stale
            max_tokens=self._settings.knowledge_tokens,
        )
        if loaded is None:
            return "", {}, False
        signals = detect_signals(prepared.files)
        diff_text = "\n".join(f.render() for f in prepared.files)[:200_000]
        checks = knowledge.select_checks(
            loaded.knowledge, [f.path for f in prepared.files], signals, diff_text
        )
        text = knowledge.render_for_review(loaded.knowledge, checks)
        info = knowledge.basis_for(loaded.knowledge, checks, loaded.stale, loaded.approved)
        info["signals"] = sorted(signals)
        if loaded.orphans:
            info["orphan_edits"] = loaded.orphans[:20]
        return text, info, bool(text) and not loaded.knowledge.domain.empty

    def _api_context(self, project: Project, sha: str) -> ContextFetcher:
        """Without a checkout the ±30 lines of context come from GitLab, one file at a time."""

        def fetch(path: str) -> str | None:
            try:
                return self._client.file_raw(project.external_id, path, sha)
            except GitLabError:
                return None  # context is a bonus; the diff alone is reviewable

        return fetch

    def _passes(
        self,
        review: Review,
        project: Project,
        target: ReviewTarget,
        prepared: PreparedDiff,
        sha: str,
        root: Path | None,
        agent_pass: bool,
    ) -> PassOutcome:
        """[1] with the repository open when asked and possible, else (or on its failure) [1']
        the no-tool pass; then [2] the verification when there is a checkout to verify in."""
        settings = self._settings
        fetch_context = (
            context_reader(root) if root is not None else self._api_context(project, sha)
        )
        outcome: PassOutcome | None = None
        assert review.deep is not None  # noqa: S101 - _run() set it
        if agent_pass and root is not None and self._agent is not None:
            try:
                outcome = self._agent_pass(
                    review, project, target, prepared, sha, root, fetch_context
                )
            except (LLMError, ReviewParseError, GitLabError) as exc:
                review.deep["fallback"] = {
                    "from": "agentic",
                    "error": f"{exc.__class__.__name__}: {str(exc)[:200]}",
                }
                log.warning(
                    "agentic pass failed; no-tool pass instead",
                    extra={"review_id": review.id, "error": exc.__class__.__name__},
                )
            except Exception as exc:  # the no-tool pass always survives the agentic one
                review.deep["fallback"] = {"from": "agentic", "error": exc.__class__.__name__}
                log.exception(
                    "agentic pass crashed; no-tool pass instead", extra={"review_id": review.id}
                )
        if outcome is None:
            outcome = self._fast_pass(review, target, prepared, fetch_context)
            if outcome.failed is not None:
                return outcome
        review.mode = outcome.mode
        if root is not None and self._agent is not None and self._verify_on():
            self._step(review.id, "verify", "kiểm chứng finding")
            verified, report = verify(
                outcome.findings,
                agent=self._agent,
                root=root,
                diff_files={f.path: f for f in prepared.files},
                project=target.project,
                target=target_header(target),
                knowledge=target.knowledge,
                max_findings=settings.verify_max_findings,
                max_turns=settings.verify_max_turns,
                timeout_s=settings.verify_timeout_s,
                on_result=lambda result: self._add_usage(review, result),
                on_batch=lambda n, total: self._step(review.id, "verify", f"lời gọi {n}/{total}"),
            )
            outcome.findings = verified
            review.deep["verification"] = report.as_dict()
        else:
            # Not a failure of [2]: it was not configured to run, so the findings stand as
            # reported (`skipped`, pre-v4 semantics) instead of waiting for a person.
            outcome.findings = skipped_status(outcome.findings)
            review.deep["verification"] = {"skipped": self._verify_skip_reason()}
        return outcome

    def _verify_on(self) -> bool:
        return self._settings.verify and self._settings.verify_max_findings > 0

    def _verify_skip_reason(self) -> str:
        """Why [2] did not run, when it did not (the checkout is the only run-time reason)."""
        settings = self._settings
        if not settings.verify:
            return "tắt"
        if self._agent is None:
            return "không có provider có tool"
        if settings.verify_max_findings <= 0:
            return "VERIFY_MAX_FINDINGS=0"
        return "không có bản sao mã nguồn"

    def _agent_pass(
        self,
        review: Review,
        project: Project,
        target: ReviewTarget,
        prepared: PreparedDiff,
        sha: str,
        root: Path,
        fetch_context: ContextFetcher,
    ) -> PassOutcome:
        """[1] docs/specs/19 §4.2: the lean prompt over the whole diff (chunked only above
        REVIEW_CHUNK_TOKENS), Read/Grep/Glob in the checkout. Raises on CLI, timeout or an
        answer that does not validate after one repair; the caller falls back to [1']."""
        settings, agent = self._settings, self._agent
        assert agent is not None  # noqa: S101 - checked by the caller
        pieces = chunk(prepared, settings.chunk_tokens, fetch_context)
        self._step(review.id, "agent", f"{len(pieces)} phần, agent đang đọc code", loc=prepared.loc)
        outcome = PassOutcome("agent", chunks=len(pieces))
        raw: list[AgentFinding] = []
        summaries: list[str] = []
        intents: list[Intent] = []
        for number, piece in enumerate(pieces, 1):
            if len(pieces) > 1:
                self._step(review.id, "agent", f"phần {number}/{len(pieces)}")
            tracker = LiveTracker(str(root))
            result = review_chunk_in_repo(
                agent,
                self._provider,
                build_agent_prompt(target, piece),
                str(root),
                max_turns=settings.agent_max_turns,
                timeout_s=settings.agent_timeout_s,
                on_event=lambda event, t=tracker: self._live(review.id, t, event),
            )
            for call in result.calls:
                self._add_usage(review, call)
                outcome.cost_usd += call.cost_usd
            review.model = result.calls[0].model
            outcome.turns += int((result.calls[0].raw or {}).get("num_turns") or tracker.turns)
            raw += result.review.findings
            summaries.append(result.review.summary.strip())
            if result.review.intent is not None:
                intents.append(result.review.intent)
            outcome.applied_rules += [r.model_dump() for r in result.review.applied_rules]
            outcome.project_fit += [
                n for n in result.review.project_fit if n not in outcome.project_fit
            ]
        processed = agent_findings(
            raw, {f.path: f for f in prepared.files}, root, settings.min_confidence
        )
        outcome.findings = processed.findings
        outcome.dropped = dict(processed.dropped)
        outcome.summary = "\n\n".join(s for s in summaries if s)
        outcome.intent = merge_intents(intents)
        outcome.project_fit = outcome.project_fit[:6]
        return outcome

    def _fast_pass(
        self,
        review: Review,
        target: ReviewTarget,
        prepared: PreparedDiff,
        fetch_context: ContextFetcher,
    ) -> PassOutcome:
        """[1'] the no-tool pass of docs/specs/06: the same prompt (minus the repository), the
        diff in chunks of MAX_TOKENS_PER_CHUNK. Never raises: `failed` carries the error."""
        settings = self._settings
        pieces = chunk(prepared, settings.max_tokens_per_chunk, fetch_context)
        self._step(
            review.id, "llm", f"{len(pieces)} phần", loc=prepared.loc, files=len(prepared.files)
        )
        outcome = PassOutcome("fast", chunks=len(pieces))
        calls: list[ReviewResult] = []
        raw: list[LLMFinding] = []
        summaries: list[str] = []
        intents: list[Intent] = []
        try:
            for number, piece in enumerate(pieces, 1):
                if len(pieces) > 1:
                    self._step(review.id, "llm", f"phần {number}/{len(pieces)}")
                result = review_chunk(self._provider, build_prompt(target, piece))
                calls += result.calls
                raw += result.review.findings
                summaries.append(result.review.summary.strip())
                if result.review.intent is not None:
                    intents.append(result.review.intent)
        except (LLMError, ReviewParseError) as exc:
            self._record_usage(review, calls)
            outcome.failed = f"llm: {exc}"
            return outcome
        self._record_usage(review, calls)
        outcome.cost_usd = sum(c.cost_usd for c in calls)
        processed = postprocess(raw, {f.path: f for f in prepared.files}, settings.min_confidence)
        outcome.findings = processed.findings
        outcome.dropped = dict(processed.dropped)
        outcome.summary = "\n\n".join(s for s in summaries if s)
        outcome.intent = merge_intents(intents)
        return outcome

    def _store(self, review: Review, prepared: PreparedDiff, outcome: PassOutcome) -> Review:
        settings = self._settings
        self._step(review.id, "save")
        final = outcome.findings
        for f in final:
            self._session.add(
                ReviewFinding(
                    review_id=review.id,
                    source="ai",
                    severity=f.severity,
                    category=f.category,
                    file_path=f.file_path,
                    line_start=f.line_start,
                    line_end=f.line_end,
                    title=f.title,
                    message=f.message,
                    suggestion=f.suggestion,
                    code_excerpt=f.code_excerpt,
                    confidence=Decimal(str(f.confidence)),
                    fingerprint=f.fingerprint,
                    rule_id=f.rule_id,
                    status=f.status,
                    verified=f.verified,
                    verification=f.verification,
                )
            )
        review.verdict, review.risk_score = assess(
            (f.severity, f.status, f.verified) for f in final
        )
        review.intent = outcome.intent
        review.score = Decimal(
            str(
                review_score(
                    [ScoredFinding(f.severity, f.confidence, f.status, f.verified) for f in final],
                    prepared.loc,
                    weights_for(RUBRIC_VERSION, settings.scoring_weights),
                    RUBRIC_VERSION,
                    settings.verify_min_confidence_unverified,
                )
            )
        )
        review.summary = redact(outcome.summary)[:4000]
        review.deep = {
            **(review.deep or {}),
            "mode": outcome.mode,
            "chunks": outcome.chunks,
            "turns": outcome.turns,
            "cost_usd": round(outcome.cost_usd, 4),
            "dropped": outcome.dropped,
            "applied_rules": outcome.applied_rules[:100],
            "project_fit": outcome.project_fit,
        }
        if review.basis is not None:
            # docs/specs/19 §4.6: which rules the agent said apply, for the basis card.
            review.basis["applied_rules"] = [
                {"rule_id": r["rule_id"], "applies": r["applies"], "where": r["where"]}
                for r in outcome.applied_rules[:60]
            ]
        log.info(
            "review postprocessed",
            extra={
                "review_id": review.id,
                "kept": len(final),
                "dropped": outcome.dropped,
                "mode": review.mode,
                "verdict": review.verdict,
            },
        )
        return self._finish(review, "done")

    def _guidance(
        self,
        review: Review,
        project: Project,
        target: ReviewTarget,
        prepared: PreparedDiff,
        sha: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Fallback while a project has no compiled knowledge (docs/specs/19 lộ trình step 1):
        the .claude files for the prompt, from the dự án's shared repo and the reviewed repo's
        own `.claude`, chosen by what the change touches (review procedure first, then
        CLAUDE.md, rules and skills that match), and a record per file for the basis. Empty
        when neither exists or can be read."""
        business = self._business(project)
        shared_files: list[claude_kit.KitFile] = []
        if business is not None and business.claude_repo_path:
            self._step(review.id, "llm", f"đọc repo .claude {business.claude_repo_path}")
            shared = load_shared_config(
                self._client,
                business,
                project.connection_id,
                sensitive_paths=self._settings.sensitive_paths,
                file_chars=self._settings.claude_file_chars,
            )
            shared_files = shared.files if shared else []
        in_diff = {f.path for f in prepared.files} | set(prepared.excluded)
        repo_files, _ = load_repo_claude(
            self._client,
            project,
            sha,
            changed=in_diff,
            sensitive_paths=self._settings.sensitive_paths,
            file_chars=self._settings.claude_file_chars,
        )
        if not shared_files and not repo_files:
            return "", []
        # Of two equal files select() keeps the first: the repo's own copy.
        chosen = claude_kit.select(
            [*repo_files, *shared_files],
            prepared.files,
            target.title,
            target.description,
            include_globs(business.claude_repo_include if business else None),
        )
        picked = [*chosen.procedure, *chosen.ranked]
        shared_name = business.claude_repo_path if business else ""
        packed = pack_instructions(
            [
                (
                    "shared",
                    f"repo {shared_name}",
                    "",
                    [f.doc() for f in picked if f.source == "shared"],
                ),
                (
                    "repo",
                    f"của repo {project.path_with_namespace}",
                    "",
                    [f.doc() for f in picked if f.source == "repo"],
                ),
            ],
            self._settings.fast_guidance_chars,
        )
        why = {f.path: f.why for f in picked}
        for record in packed.records:
            record["why"] = why.get(record["path"], [])
        return packed.text, packed.records

    def _add_usage(self, review: Review, call: ReviewResult) -> None:
        review.prompt_tokens = (review.prompt_tokens or 0) + call.prompt_tokens
        review.completion_tokens = (review.completion_tokens or 0) + call.completion_tokens
        review.cost_usd = Decimal(str(round(float(review.cost_usd or 0) + call.cost_usd, 4)))

    def month_spend_usd(self) -> float:
        """Reviews plus knowledge builds this month (docs/specs/19 §6: one budget)."""
        from app.workers.knowledge_jobs import month_spend_usd  # lazy: the worker imports us

        return month_spend_usd(self._session, self._clock())

    def _within_budget(self) -> bool:
        """docs/specs/06: pause over MONTHLY_LLM_BUDGET_USD; 13: alert from 80%."""
        budget = self._settings.monthly_budget_usd
        if budget <= 0:
            return True
        spent = self.month_spend_usd()
        if spent >= budget:
            log.error(
                "monthly LLM budget reached, reviews paused",
                extra={"spent_usd": spent, "budget_usd": budget},
            )
            return False
        if spent >= 0.8 * budget:
            log.warning(
                "monthly LLM budget above 80%", extra={"spent_usd": spent, "budget_usd": budget}
            )
        return True

    def _record_usage(self, review: Review, calls: list[ReviewResult]) -> None:
        if not calls:
            return
        review.model = calls[-1].model
        for call in calls:
            self._add_usage(review, call)
