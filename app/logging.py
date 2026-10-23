"""JSON logs; every line carries request_id / job_id when one is set (docs/specs/AGENTS.md)."""

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}
# Telegram bot tokens travel in the URL path (https://api.telegram.org/bot<token>/...); no log
# line may carry one, whatever logger wrote it (docs/specs/11, AGENTS.md).
_BOT_TOKEN = re.compile(r"bot\d+:[A-Za-z0-9_-]{20,}")


def redact(text: str) -> str:
    return _BOT_TOKEN.sub("bot***", text)


class RedactSecrets(logging.Filter):
    """Rewrites known secret shapes in the message and its arguments before formatting."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()
                }
            else:
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        if request_id := request_id_var.get():
            entry["request_id"] = request_id
        if job_id := job_id_var.get():
            entry["job_id"] = job_id
        entry.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactSecrets())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "rq.worker"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    # httpx logs every request URL at INFO; a Telegram URL contains the bot token.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
