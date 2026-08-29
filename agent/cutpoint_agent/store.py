"""Durable state for reports, jobs and watch fingerprints.

Cloud Run gives each instance its own ephemeral filesystem, so a report written
by the instance that ran the pipeline is invisible to the instance that later
serves GET /report/{id}. Anything that must outlive a single request goes
through here.

Backend is chosen by CUTPOINT_STORE:
  "local"     (default) -- data/{reports,jobs,watch}/, keeps tests and `make demo`
                           working with no cloud access
  "firestore"           -- Firestore collections, used on Cloud Run

The default is deliberately local and keyed off its own variable rather than off
GOOGLE_CLOUD_PROJECT, which is set in .env for Vertex and would otherwise drag
the whole test suite onto Firestore.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
REPORTS_DIR = REPO_ROOT / "data" / "reports"
JOBS_DIR = REPO_ROOT / "data" / "jobs"
WATCH_DIR = REPO_ROOT / "data" / "watch"

REPORTS_COLLECTION = "cutpoint_reports"
JOBS_COLLECTION = "cutpoint_jobs"
WATCH_COLLECTION = "cutpoint_watch"

_ID_MAX_LEN = 64


def using_firestore() -> bool:
    return os.environ.get("CUTPOINT_STORE", "local").lower() == "firestore"


def _client():
    from google.cloud import firestore

    return firestore.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"])


def _check_id(doc_id: str) -> str:
    """Ids reach both filesystem paths and Firestore document paths. The API
    validates at its boundary; this is the second gate for callers that do not
    come through FastAPI, such as the Pub/Sub handler and the watcher.
    """
    if not doc_id or len(doc_id) > _ID_MAX_LEN:
        raise ValueError(f"invalid id: {doc_id!r}")
    if not all(c.isalnum() or c in "_-" for c in doc_id):
        raise ValueError(f"invalid id: {doc_id!r}")
    return doc_id


def _local_path(directory: Path, doc_id: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{_check_id(doc_id)}.json"


def _put(collection: str, directory: Path, doc_id: str, payload: dict[str, Any]) -> None:
    _check_id(doc_id)
    if using_firestore():
        _client().collection(collection).document(doc_id).set(payload)
        return
    _local_path(directory, doc_id).write_text(json.dumps(payload, indent=2, default=str))


def _get(collection: str, directory: Path, doc_id: str) -> dict[str, Any] | None:
    _check_id(doc_id)
    if using_firestore():
        snap = _client().collection(collection).document(doc_id).get()
        return snap.to_dict() if snap.exists else None
    path = _local_path(directory, doc_id)
    return json.loads(path.read_text()) if path.exists() else None


def save_report(trailer_id: str, notes: dict[str, Any]) -> None:
    _put(REPORTS_COLLECTION, REPORTS_DIR, trailer_id, notes)


def load_report(trailer_id: str) -> dict[str, Any] | None:
    return _get(REPORTS_COLLECTION, REPORTS_DIR, trailer_id)


def save_job(job_id: str, job: dict[str, Any]) -> None:
    _put(JOBS_COLLECTION, JOBS_DIR, job_id, job)


def load_job(job_id: str) -> dict[str, Any] | None:
    return _get(JOBS_COLLECTION, JOBS_DIR, job_id)


def save_watch_error(payload: dict[str, Any]) -> None:
    """Record a failed scan in the watch collection, not the jobs collection.

    Jobs are readable unauthenticated (the UI polls status), and this record has
    a fixed, guessable id. Writing raw exception text there published internal
    infrastructure details to anyone who requested /jobs/_last_error.
    """
    _put(WATCH_COLLECTION, WATCH_DIR, "_last_error", payload)


def get_fingerprint(trailer_id: str) -> str | None:
    doc = _get(WATCH_COLLECTION, WATCH_DIR, trailer_id)
    return doc.get("fingerprint") if doc else None


def set_fingerprint(trailer_id: str, fingerprint: str) -> None:
    _put(WATCH_COLLECTION, WATCH_DIR, trailer_id, {"fingerprint": fingerprint})
