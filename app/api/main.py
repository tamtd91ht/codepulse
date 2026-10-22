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

configure_logging()

app = FastAPI(title="CodePulse", version="0.1.0")


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
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
