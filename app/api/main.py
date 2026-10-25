import hmac
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from redis import Redis
from sqlalchemy import Engine

from app import health
from app.api import auth
from app.api.routers import (
    activity,
    admin,
    claude_repo,
    commits,
    disputes,
    llm,
    metrics,
    notifications,
    overview,
    reviews,
    settings,
    telegram,
)
from app.collector.gitlab.client import GitLabError
from app.config import get_settings
from app.db import get_engine, get_redis
from app.logging import configure_logging, request_id_var
from app.monitoring import CodePulseCollector
from app.webhook.router import router as webhook_router


def _settings_ok() -> bool:
    try:
        get_settings()
    except Exception:  # missing env at import time (tooling); requests will fail loudly later
        return False
    return True


configure_logging()
# Replaced by the access line below, which is written while request_id is still set.
logging.getLogger("uvicorn.access").disabled = True
access_log = logging.getLogger("codepulse.access")

_HTTPS = _settings_ok() and get_settings().app_base_url.startswith("https://")

app = FastAPI(title="CodePulse", version="0.1.0")
app.include_router(webhook_router, prefix="/api/v1")
for api_router in (
    auth.router,
    admin.router,
    llm.router,
    reviews.router,
    activity.router,
    metrics.router,
    disputes.router,
    telegram.router,
    overview.router,
    notifications.router,
    commits.router,
    settings.router,
    claude_repo.router,
):
    app.include_router(api_router, prefix="/api/v1")
# docs/specs/11: CORS for the dashboard origin only; cookies need credentials.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().dashboard_url] if _settings_ok() else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)


@app.exception_handler(GitLabError)
def gitlab_error(request: Request, exc: GitLabError) -> JSONResponse:
    return JSONResponse({"detail": f"GitLab: {exc}"}, status_code=502)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        access_log.info(
            "%s %s %s",
            request.method,
            request.url.path,
            response.status_code,
            extra={
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    # docs/specs/11: HTTPS only (TLS ends at the reverse proxy) and basic browser hardening.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if _HTTPS:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/healthz")
def healthz(
    engine: Annotated[Engine, Depends(get_engine)],
    redis: Annotated[Redis, Depends(get_redis)],
) -> JSONResponse:
    checks: dict[str, Any] = {
        "database": health.check_database(engine),
        "redis": health.check_redis(redis),
    }
    checks["workers"] = len(health.live_workers(redis)) if checks["redis"] else 0
    # Workers are reported, not required: the API can serve reads while the queue is down.
    ok = checks["database"] and checks["redis"]
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503
    )


_registry = CollectorRegistry()
_registry.register(
    CodePulseCollector(get_engine, get_redis, lambda: get_settings().monthly_llm_budget_usd)
)


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request) -> Response:
    token = get_settings().metrics_token.get_secret_value()
    if token and not hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {token}"
    ):
        return Response(status_code=401)
    return Response(generate_latest(_registry), media_type=CONTENT_TYPE_LATEST)
