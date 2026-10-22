import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import Engine

from app import health
from app.api import auth
from app.api.routers import admin, metrics, reviews
from app.collector.gitlab.client import GitLabError
from app.config import get_settings
from app.db import get_engine, get_redis
from app.logging import configure_logging, request_id_var
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

app = FastAPI(title="CodePulse", version="0.1.0")
app.include_router(webhook_router, prefix="/api/v1")
for api_router in (auth.router, admin.router, reviews.router, metrics.router):
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
