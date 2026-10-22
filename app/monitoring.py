"""Prometheus metrics (docs/specs/13 "Vận hành"): queue length, review latency, LLM error rate,
cost per day. Read from Postgres and Redis at scrape time, so every API replica reports the
same numbers and nothing has to be kept in process memory."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from statistics import quantiles

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from redis import Redis
from rq import Queue
from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.orm import Session

from app import health
from app.models import Review

QUEUES = ("default",)
WINDOW = timedelta(hours=1)


class CodePulseCollector(Collector):
    def __init__(
        self,
        engine: Callable[[], Engine | Connection],
        redis: Callable[[], Redis],
        budget_usd: Callable[[], float],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._engine = engine
        self._redis = redis
        self._budget = budget_usd
        self._clock = clock

    def collect(self) -> Iterator[GaugeMetricFamily]:
        now = self._clock()
        redis = self._redis()
        queue = GaugeMetricFamily(
            "codepulse_queue_jobs", "Jobs waiting in the RQ queue", labels=["queue"]
        )
        try:
            for name in QUEUES:
                queue.add_metric([name], len(Queue(name, connection=redis)))
            workers = len(health.live_workers(redis, now))
        except Exception:  # Redis down: report what we can, the up metric shows the rest
            workers = 0
        yield queue
        yield GaugeMetricFamily(
            "codepulse_workers_alive", "Workers with a fresh heartbeat", value=workers
        )

        with Session(self._engine()) as session:
            since = now - WINDOW
            rows = session.execute(
                select(Review.status, Review.error, Review.created_at, Review.finished_at).where(
                    Review.finished_at >= since
                )
            ).all()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            spent_month, spent_day = session.execute(
                select(
                    func.coalesce(func.sum(Review.cost_usd), 0),
                    func.coalesce(
                        func.sum(Review.cost_usd).filter(Review.started_at >= day_start), 0
                    ),
                ).where(Review.started_at >= month_start)
            ).one()

        latencies = sorted(
            (finished - created).total_seconds()
            for status, _, created, finished in rows
            if status == "done" and created is not None and finished is not None
        )
        latency = GaugeMetricFamily(
            "codepulse_review_latency_seconds",
            "Queue-to-done time of reviews finished in the last hour",
            labels=["quantile"],
        )
        if latencies:
            p50, p95 = (
                (latencies[0], latencies[0])
                if len(latencies) == 1
                else (quantiles(latencies, n=100)[49], quantiles(latencies, n=100)[94])
            )
            latency.add_metric(["0.5"], p50)
            latency.add_metric(["0.95"], p95)
        yield latency

        llm_failed = sum(
            1
            for status, error, *_ in rows
            if status == "failed" and (error or "").startswith("llm:")
        )
        llm_calls = llm_failed + sum(1 for status, *_ in rows if status == "done")
        yield GaugeMetricFamily(
            "codepulse_llm_error_ratio",
            "Share of reviews in the last hour that failed in the LLM step",
            value=llm_failed / llm_calls if llm_calls else 0.0,
        )
        yield GaugeMetricFamily(
            "codepulse_llm_cost_usd_today", "LLM spend today (UTC)", value=float(spent_day or 0)
        )
        yield GaugeMetricFamily(
            "codepulse_llm_cost_usd_month", "LLM spend this month", value=float(spent_month or 0)
        )
        yield GaugeMetricFamily(
            "codepulse_llm_budget_usd", "MONTHLY_LLM_BUDGET_USD", value=self._budget()
        )
