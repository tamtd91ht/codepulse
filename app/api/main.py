import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import Engine

from app import health
from app.db import get_engine, get_redis
from app.logging import configure_logging, request_id_var
from app.webhook.router import router as webhook_router

configure_logging()
# Replaced by the access line below, which is written while request_id is still set.
logging.getLogger("uvicorn.access").disabled = True
access_log = logging.getLogger("codepulse.access")

app = FastAPI(title="CodePulse", version="0.1.0")
app.include_router(webhook_router, prefix="/api/v1")


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
