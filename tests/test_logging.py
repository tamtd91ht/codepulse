import json
import logging

from app.logging import JsonFormatter, job_id_var, request_id_var


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.makeLogRecord({"name": "t", "levelname": "INFO", "msg": msg})
    record.__dict__.update(extra)
    return record


def test_plain_record_is_json_without_ids() -> None:
    entry = json.loads(JsonFormatter().format(_record("hello %s")))
    assert entry["message"] == "hello %s"
    assert entry["level"] == "INFO"
    assert "request_id" not in entry
    assert "job_id" not in entry


def test_ids_and_extra_fields_are_included() -> None:
    req, job = request_id_var.set("req-1"), job_id_var.set("job-9")
    try:
        entry = json.loads(JsonFormatter().format(_record("synced", project_id=42)))
    finally:
        request_id_var.reset(req)
        job_id_var.reset(job)
    assert entry["request_id"] == "req-1"
    assert entry["job_id"] == "job-9"
    assert entry["project_id"] == 42


def test_exception_is_serialised() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        import sys

        record = _record("failed")
        record.exc_info = sys.exc_info()
    entry = json.loads(JsonFormatter().format(record))
    assert "RuntimeError: boom" in entry["exc_info"]
