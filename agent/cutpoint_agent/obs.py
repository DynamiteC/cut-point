"""Structured logging for Cloud Run.

Cloud Logging parses a JSON object on stdout into a real log entry: `severity`
becomes the log level, `message` the summary, and every other key a queryable
field. A bare print() becomes an untyped text blob, so an operator cannot filter
by severity or correlate the steps of one run -- which is what we had.

Deliberately not a logging framework. One function, no handlers to configure,
no dependency, and it degrades to readable text when not running on Cloud Run.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from contextvars import ContextVar

# Set once per pipeline run so every line from one run can be selected together.
_run_id: ContextVar[str] = ContextVar("cutpoint_run_id", default="")

_ON_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))


def new_run_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _run_id.set(rid)
    return rid


def set_run_id(rid: str) -> None:
    _run_id.set(rid)


def log(severity: str, message: str, **fields: object) -> None:
    """Emit one structured record. severity is a Cloud Logging level:
    DEBUG, INFO, NOTICE, WARNING, ERROR, CRITICAL.
    """
    rid = _run_id.get()
    if rid:
        fields["run_id"] = rid
    if _ON_CLOUD_RUN:
        payload = {"severity": severity, "message": message, **fields}
        print(json.dumps(payload, default=str), file=sys.stdout, flush=True)
        return
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[{severity}] {message}{' ' + extra if extra else ''}", flush=True)


def info(message: str, **fields: object) -> None:
    log("INFO", message, **fields)


def warning(message: str, **fields: object) -> None:
    log("WARNING", message, **fields)


def error(message: str, **fields: object) -> None:
    log("ERROR", message, **fields)
