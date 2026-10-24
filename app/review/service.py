"""Review one MR or one direct commit end to end (docs/specs/06, task 3.5).

fetch diff -> prepare -> chunks -> LLM -> post-process -> score -> store. Diffs are never stored;
findings keep a short excerpt.
"""

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

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
from app.review import comment
from app.review.checkout import CheckoutError, checkout
from app.review.deep import TOOLS, DeepOutcome, build_deep_prompt, merge, parse_deep
from app.review.discover import discover
from app.review.postprocess import Finding, postprocess
from app.review.prepare import PreparedDiff, chunk, prepare, redact
from app.review.profile import (
    MIN_CACHED_CONFIDENCE,
    build_profile,
    profile_is_fresh,
    render_profile,
)
from app.review.progress import NullProgress, ProgressSink
from app.review.prompt import ReviewParseError, ReviewTarget, build_prompt, review_chunk
from app.review.providers.base import AgenticProvider, LLMError, LLMProvider, ReviewResult
from app.review.rubric import RUBRIC_VERSION
from app.review.rules import build_rules
from app.review.schema import Intent, LLMFinding
from app.review.status import mark_fixed_findings, supersede_mr_reviews
from app.scoring.formula import ScoredFinding, review_score, weights_for
from app.scoring.verdict import assess

log = logging.getLogger(__name__)

FINISHED = ("done", "skipped", "superseded")
# A "running" review older than this was left by a crashed worker and may be taken over.
# Must exceed the worker job timeout (REVIEW_JOB_TIMEOUT), or a killed job's row is never freed.
STALE_RUNNING = timedelta(minutes=50)


@dataclass
class ReviewSettings:
    ignore_paths: list[str]
    sensitive_paths: list[str]
    max_review_loc: int
    max_tokens_per_chunk: int
    min_confidence: float
    monthly_budget_usd: float = 0.0  # 0 = unlimited
    scoring_weights: Mapping[str, float] | None = None
    # docs/specs/18
    deep_review: bool = False
    deep_max_turns: int = 40
    deep_timeout_s: float = 600.0
    deep_max_archive_bytes: int = 300 * 1024 * 1024
    deep_min_loc: int = 0
    profile_ttl: timedelta = timedelta(days=7)


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
        "summary": next((i.summary for i in intents if i.summary), "")[:1500],
        "matches_intent": (all(matches) if matches else None),
        "altitude": altitude,
        "concerns": concerns[:5],
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
    ) -> None:
        self._session = session
        self._progress: ProgressSink = progress or NullProgress()
        self._client = client
        self._provider = provider
        self._settings = settings
        self._clock = clock
        self._agent = agent  # deep review provider; None = fast reviews only

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
        if not (project.review_enabled and project.tracked):
            return None
        if force and (mr.state != "opened" or mr.head_sha != head_sha):
            # Checked again at run time: the MR may have been merged or pushed since the
            # request, and an old head is history (docs/specs/17 §Lịch sử).
            log.info("forced review dropped: mr not open at this head", extra={"mr_id": mr.id})
            return None
        review = self._claim(
            "mr",
            head_sha,
            mr_id=mr.id,
            author=self._user_of(mr.author_account_id),
            trigger=trigger,
            requested_by=requested_by,
            force=force,
            expected_round=expected_round,
        )
        if review is None:
            return None
        self._begin(review, project, "mr", str(mr.iid), mr.title or "", head_sha)
        return self._tracked(review, lambda: self._mr_claimed(review, project, mr, head_sha))

    def _mr_claimed(
        self, review: Review, project: Project, mr: MergeRequest, head_sha: str
    ) -> Review:
        self._progress.stage(review.id, "diff")
        description = ""
        try:
            info = self._client.merge_request(project.external_id, mr.iid)
            description = info.get("description") or ""
            entries = self._client.mr_diffs(project.external_id, mr.iid)
        except GitLabError as exc:
            return self._fail(review, f"gitlab: {exc}")
        target = ReviewTarget(
            project.path_with_namespace,
            mr.title or "",
            description,
            conventions=self._rules(project, _paths(entries)),
        )
        result = self._run(review, project, target, entries, head_sha)
        if result is not None and result.status == "done":
            supersede_mr_reviews(self._session, project, mr.iid, head_sha)
            # A re-judgment of the same head is not a fix: only a new push can fix findings.
            if review.round == 1:
                mark_fixed_findings(self._session, mr.id, result.id)
            if project.post_mr_comment:
                findings = list(
                    self._session.scalars(
                        select(ReviewFinding).where(ReviewFinding.review_id == result.id)
                    )
                )
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
        if not (project.review_enabled and project.tracked):
            return None
        review = self._claim(
            "commit",
            commit.sha,
            commit_sha=commit.sha,
            author=self._user_of(commit.author_account_id),
            trigger=trigger,
            requested_by=requested_by,
            force=force,
            expected_round=expected_round,
        )
        if review is None:
            return None
        first_line = (commit.message or "").strip().splitlines()[:1]
        title = first_line[0] if first_line else commit.sha
        self._begin(review, project, "commit", commit.sha, title, commit.sha)
        return self._tracked(review, lambda: self._commit_claimed(review, project, commit, title))

    def _commit_claimed(
        self, review: Review, project: Project, commit: Commit, title: str
    ) -> Review:
        self._progress.stage(review.id, "diff")
        try:
            entries = self._client.commit_diff(project.external_id, commit.sha)
        except GitLabError as exc:
            return self._fail(review, f"gitlab: {exc}")
        target = ReviewTarget(
            project.path_with_namespace,
            title,
            kind="Commit",
            conventions=self._rules(project, _paths(entries)),
        )
        return self._run(review, project, target, entries, commit.sha)

    # -- steps ---------------------------------------------------------------------------------

    def _tracked(self, review: Review, work: Callable[[], Review]) -> Review:
        """Run a claimed review and tell the live view how it ended (None: it crashed)."""
        try:
            result = work()
        except BaseException:
            self._progress.end(review.id, None)
            raise
        self._progress.end(review.id, result.status)
        return result

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

    def _rules(self, project: Project, changed: set[str]) -> str:
        """Team rules for the prompt (docs/specs/17): dự án + repo text, plus files read from the
        repo's default branch, never from the change under review (its author could rewrite
        them in the same commit); a rules file touched by the diff is left out."""
        business = (
            self._session.get(BusinessProject, project.business_project_id)
            if project.business_project_id is not None
            else None
        )
        ref = project.default_branch or "HEAD"

        def fetch(path: str) -> str | None:
            try:
                return self._client.file_raw(project.external_id, path, ref)
            except GitLabError:
                return None

        return build_rules(business, project, fetch, skip=changed)

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
        the requester saw: a duplicate or concurrent forced job finds a newer one and stops."""
        if requested_by is not None and self._session.get(User, requested_by) is None:
            requested_by = None  # the requester left between enqueue and run
        values = {
            "target_type": target_type,
            "mr_id": target.get("mr_id"),
            "commit_sha": target.get("commit_sha"),
            "reviewed_sha": sha,
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
            and review.started_at < self._clock() - STALE_RUNNING
        )
        if force and review.status == "done":
            if review.superseded_by is not None or (
                expected_round is not None and review.round != expected_round
            ):
                # Another forced run took this round already (FOR UPDATE re-reads the row but
                # cannot see the newer one): one paid rerun per request.
                log.info("forced review already taken", extra={"review_id": review.id})
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
            return None
        review.status, review.error = "running", None
        review.started_at = self._clock()
        # A failed/skipped/stale row taken over records what asked for this run.
        review.trigger, review.requested_by_user_id = trigger, requested_by
        self._session.flush()
        return review

    def _finish(self, review: Review, status: str, error: str | None = None) -> Review:
        review.status, review.error = status, error
        review.finished_at = self._clock()
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
        if prepared.loc > settings.max_review_loc:
            return self._finish(review, "skipped", f"MR quá lớn: {prepared.loc} LOC")
        if not self._within_budget():
            # "failed", not "skipped": the review can be re-run once the budget allows it.
            return self._fail(review, "monthly LLM budget reached")

        def fetch_context(path: str) -> str | None:
            try:
                return self._client.file_raw(project.external_id, path, sha)
            except GitLabError:
                return None  # context is a bonus; the diff alone is reviewable

        chunks = chunk(prepared, settings.max_tokens_per_chunk, fetch_context)
        self._progress.stage(
            review.id, "llm", f"{len(chunks)} phần", loc=prepared.loc, files=len(prepared.files)
        )
        calls: list[ReviewResult] = []
        raw_findings: list[LLMFinding] = []
        summaries: list[str] = []
        intents: list[Intent] = []
        try:
            for number, piece in enumerate(chunks, 1):
                if len(chunks) > 1:
                    self._progress.stage(review.id, "llm", f"phần {number}/{len(chunks)}")
                result = review_chunk(self._provider, build_prompt(target, piece))
                calls += result.calls
                raw_findings += result.review.findings
                summaries.append(result.review.summary.strip())
                if result.review.intent is not None:
                    intents.append(result.review.intent)
        except (LLMError, ReviewParseError) as exc:
            self._record_usage(review, calls)
            return self._fail(review, f"llm: {exc}")
        self._record_usage(review, calls)

        processed = postprocess(
            raw_findings, {f.path: f for f in prepared.files}, settings.min_confidence
        )
        outcome = self._deep(review, project, target, prepared, processed.findings, sha)
        self._progress.stage(review.id, "save")
        final: list[Finding] = outcome.findings if outcome else processed.findings
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
                )
            )
        review.verdict, review.risk_score = assess((f.severity, "open") for f in final)
        review.intent = (
            outcome.intent.model_dump() if outcome and outcome.intent else merge_intents(intents)
        )
        review.score = Decimal(
            str(
                review_score(
                    [ScoredFinding(f.severity, f.confidence) for f in final],
                    prepared.loc,
                    weights_for(RUBRIC_VERSION, settings.scoring_weights),
                )
            )
        )
        summary = "\n\n".join(s for s in summaries if s)
        if outcome and outcome.summary:
            summary = outcome.summary
        review.summary = redact(summary)[:4000]
        log.info(
            "review postprocessed",
            extra={
                "review_id": review.id,
                "kept": len(final),
                "dropped": dict(processed.dropped),
                "mode": review.mode,
            },
        )
        return self._finish(review, "done")

    # -- deep review (docs/specs/18) -----------------------------------------------------------

    def _deep(
        self,
        review: Review,
        project: Project,
        target: ReviewTarget,
        prepared: PreparedDiff,
        candidates: list[Finding],
        sha: str,
    ) -> DeepOutcome | None:
        """Second pass with the repository open. Never raises: on any trouble the first pass
        stands and `review.deep` says why."""
        settings = self._settings
        review.mode = "fast"
        agent = self._agent
        if agent is None or not settings.deep_review or not project.deep_review:
            return None
        if prepared.loc < settings.deep_min_loc:
            review.deep = {"skipped": f"diff dưới {settings.deep_min_loc} LOC"}
            return None
        if not self._within_budget():
            review.deep = {"skipped": "hết ngân sách LLM tháng"}
            return None
        self._progress.stage(review.id, "deep", "tải mã nguồn")
        try:
            with checkout(
                self._client,
                project.external_id,
                sha,
                max_archive_bytes=settings.deep_max_archive_bytes,
                sensitive_paths=settings.sensitive_paths,
            ) as (root, stats):
                docs = discover(root)
                profile = self._profile(project, docs, sha, review)
                self._progress.stage(review.id, "deep", "agent đang đọc code", files=stats.files)
                prompt = build_deep_prompt(
                    target,
                    render_profile(profile) if profile else "",
                    "\n\n".join(f"### {d.path}\n{d.text}" for d in docs.instructions),
                    target.conventions,
                    prepared,
                    candidates,
                )
                result = agent.review_in_repo(
                    prompt,
                    str(root),
                    tools=TOOLS,
                    max_turns=settings.deep_max_turns,
                    timeout_s=settings.deep_timeout_s,
                )
                self._add_usage(review, result)
                deep = parse_deep(result.output)
                outcome = merge(
                    candidates,
                    deep,
                    root,
                    {f.path: f for f in prepared.files},
                    settings.min_confidence,
                )
        except (ArchiveTooLarge, CheckoutError) as exc:
            review.deep = {"error": "checkout", "detail": str(exc)[:200]}
            log.warning("deep review skipped: checkout", extra={"review_id": review.id})
            return None
        except (GitLabError, LLMError, ValueError) as exc:
            review.deep = {"error": exc.__class__.__name__, "detail": str(exc)[:200]}
            log.warning(
                "deep review failed; first pass kept",
                extra={"review_id": review.id, "error": exc.__class__.__name__},
            )
            return None
        except Exception as exc:  # the spec promises the fast pass always survives
            review.deep = {"error": exc.__class__.__name__}
            log.exception("deep review crashed; first pass kept", extra={"review_id": review.id})
            return None
        review.mode = "deep"
        review.deep = {
            **outcome.report(),
            "files_seen": stats.files,
            "turns": (result.raw or {}).get("num_turns"),
            "cost_usd": round(result.cost_usd, 4),
            "instruction_sources": docs.instruction_paths[:20],
        }
        return outcome

    def _profile(
        self, project: Project, docs: Any, sha: str, review: Review
    ) -> dict[str, Any] | None:
        """The cached project profile, rebuilt when stale (one plain LLM call, billed to the
        review that needed it so the monthly budget sees it)."""
        if profile_is_fresh(project, self._settings.profile_ttl, self._clock()):
            return project.profile
        self._progress.stage(review.id, "profile", "dựng hồ sơ dự án")
        try:
            profile, usage = build_profile(self._provider, docs, project.path_with_namespace)
        except (LLMError, ValueError) as exc:
            log.warning("project profile not built", extra={"error": exc.__class__.__name__})
            return project.profile
        self._add_usage(review, usage)
        if float(profile.get("confidence") or 0) < MIN_CACHED_CONFIDENCE:
            # Thin documents: use it for this review, but ask again next time.
            return profile
        project.profile, project.profile_sha, project.profile_at = profile, sha, self._clock()
        project.profile["cost_usd"] = round(usage.cost_usd, 4)
        self._session.flush()
        return project.profile

    def _add_usage(self, review: Review, call: ReviewResult) -> None:
        review.prompt_tokens = (review.prompt_tokens or 0) + call.prompt_tokens
        review.completion_tokens = (review.completion_tokens or 0) + call.completion_tokens
        review.cost_usd = Decimal(str(round(float(review.cost_usd or 0) + call.cost_usd, 4)))

    def month_spend_usd(self) -> float:
        now = self._clock()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = self._session.scalar(
            select(func.coalesce(func.sum(Review.cost_usd), 0)).where(
                Review.started_at >= month_start
            )
        )
        return float(spent or 0)

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
        review.prompt_tokens = sum(c.prompt_tokens for c in calls)
        review.completion_tokens = sum(c.completion_tokens for c in calls)
        review.cost_usd = Decimal(str(round(sum(c.cost_usd for c in calls), 4)))
