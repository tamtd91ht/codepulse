"""Review one MR or one direct commit end to end (docs/specs/06, task 3.5).

fetch diff -> prepare -> chunks -> LLM -> post-process -> score -> store. Diffs are never stored;
findings keep a short excerpt.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.collector.gitlab.client import GitLabClient, GitLabError
from app.models import Account, Commit, MergeRequest, Project, Review, ReviewFinding
from app.review import comment
from app.review.postprocess import postprocess
from app.review.prepare import chunk, prepare
from app.review.prompt import ReviewParseError, ReviewTarget, build_prompt, review_chunk
from app.review.providers.base import LLMError, LLMProvider, ReviewResult
from app.review.rubric import RUBRIC_VERSION
from app.review.schema import LLMFinding
from app.review.status import supersede_mr_reviews
from app.scoring.formula import ScoredFinding, review_score

log = logging.getLogger(__name__)

FINISHED = ("done", "skipped", "superseded")
# A "running" review older than this was left by a crashed worker and may be taken over.
STALE_RUNNING = timedelta(minutes=30)


@dataclass
class ReviewSettings:
    ignore_paths: list[str]
    sensitive_paths: list[str]
    max_review_loc: int
    max_tokens_per_chunk: int
    min_confidence: float
    monthly_budget_usd: float = 0.0  # 0 = unlimited


class ReviewService:
    def __init__(
        self,
        session: Session,
        client: GitLabClient,
        provider: LLMProvider,
        settings: ReviewSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._session = session
        self._client = client
        self._provider = provider
        self._settings = settings
        self._clock = clock

    # -- entry points --------------------------------------------------------------------------

    def review_mr(self, project: Project, mr: MergeRequest, head_sha: str) -> Review | None:
        if not project.review_enabled:
            return None
        review = self._claim(
            "mr", head_sha, mr_id=mr.id, author=self._user_of(mr.author_account_id)
        )
        if review is None:
            return None
        description = ""
        try:
            info = self._client.merge_request(project.external_id, mr.iid)
            description = info.get("description") or ""
            entries = self._client.mr_diffs(project.external_id, mr.iid)
        except GitLabError as exc:
            return self._fail(review, f"gitlab: {exc}")
        target = ReviewTarget(project.path_with_namespace, mr.title or "", description)
        result = self._run(review, project, target, entries, head_sha)
        if result is not None and result.status == "done":
            supersede_mr_reviews(self._session, project, mr.iid, head_sha)
            if project.post_mr_comment:
                findings = list(
                    self._session.scalars(
                        select(ReviewFinding).where(ReviewFinding.review_id == result.id)
                    )
                )
                body = comment.render(result, findings)
                comment.post_or_update(self._client, project.external_id, mr.iid, body)
        return result

    def review_commit(self, project: Project, commit: Commit) -> Review | None:
        if not project.review_enabled:
            return None
        review = self._claim(
            "commit",
            commit.sha,
            commit_sha=commit.sha,
            author=self._user_of(commit.author_account_id),
        )
        if review is None:
            return None
        try:
            entries = self._client.commit_diff(project.external_id, commit.sha)
        except GitLabError as exc:
            return self._fail(review, f"gitlab: {exc}")
        title = (commit.message or "").strip().splitlines()[0] if commit.message else commit.sha
        target = ReviewTarget(project.path_with_namespace, title, kind="Commit")
        return self._run(review, project, target, entries, commit.sha)

    # -- steps ---------------------------------------------------------------------------------

    def _user_of(self, account_id: int | None) -> int | None:
        if account_id is None:
            return None
        account = self._session.get(Account, account_id)
        return account.user_id if account else None

    def _claim(
        self, target_type: str, sha: str, author: int | None, **target: Any
    ) -> Review | None:
        """Get-or-create the review row; a finished or running review of the same sha is final."""
        values = {
            "target_type": target_type,
            "mr_id": target.get("mr_id"),
            "commit_sha": target.get("commit_sha"),
            "reviewed_sha": sha,
            "rubric_version": RUBRIC_VERSION,
            "author_user_id": author,
            "status": "queued",
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
            .with_for_update()
        ).one()
        stale = (
            review.status == "running"
            and review.started_at is not None
            and review.started_at < self._clock() - STALE_RUNNING
        )
        if review.status in FINISHED or (review.status == "running" and not stale):
            log.info(
                "review already handled", extra={"review_id": review.id, "status": review.status}
            )
            return None
        review.status, review.error = "running", None
        review.started_at = self._clock()
        self._session.flush()
        return review

    def _finish(self, review: Review, status: str, error: str | None = None) -> Review:
        review.status, review.error = status, error
        review.finished_at = self._clock()
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
        calls: list[ReviewResult] = []
        raw_findings: list[LLMFinding] = []
        summaries: list[str] = []
        try:
            for piece in chunks:
                result = review_chunk(self._provider, build_prompt(target, piece))
                calls += result.calls
                raw_findings += result.review.findings
                summaries.append(result.review.summary.strip())
        except (LLMError, ReviewParseError) as exc:
            self._record_usage(review, calls)
            return self._fail(review, f"llm: {exc}")
        self._record_usage(review, calls)

        processed = postprocess(
            raw_findings, {f.path: f for f in prepared.files}, settings.min_confidence
        )
        for f in processed.findings:
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
                )
            )
        review.score = Decimal(
            str(
                review_score(
                    [ScoredFinding(f.severity, f.confidence) for f in processed.findings],
                    prepared.loc,
                )
            )
        )
        review.summary = "\n\n".join(s for s in summaries if s)[:4000]
        log.info(
            "review postprocessed",
            extra={
                "review_id": review.id,
                "kept": len(processed.findings),
                "dropped": dict(processed.dropped),
            },
        )
        return self._finish(review, "done")

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
