"""G10b approval gate — the trigger policy, the checkpointer factory, TTL and the per-thread lock.

The design is eval/g10b_design.md; the pre-registration is eval/g10b_PREDICTION.md. Rows are 1-based.

The trigger is a POLICY ON THE QUESTION'S WORDING, NOT A MEASUREMENT OF ANSWER QUALITY. It pauses /ask/agent before
generation when either:
- exposure_limit_named: the question names an occupational exposure limit (the pattern below). It misses wordings it
  does not name: "immediately dangerous to life or health" spelled out (guardrail hard negative 25) and "exposure
  ceiling" (frozen row 21);
- scoped_fallback: the router scoped the question to one document, but the filtered retrieval failed or came back
  empty, so the evidence came from the whole corpus instead.

Durability is option A: a SqliteSaver on the file named by REVIEW_DB_PATH. Without it there is no checkpointer, and a
question the trigger fires on is refused (fail closed) rather than answered unreviewed. On Render's free plan the file
does not survive a redeploy, restart or spin-down; a resume then returns "expired or lost", never an answer.
"""

import logging
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

EXPOSURE_LIMIT_PATTERN = re.compile(r"\b(exposure limits?|IDLH|PELs?|RELs?|TLVs?|STELs?)\b", re.IGNORECASE)
FALLBACK_NOTE = "retrieve[direct-fallback]"  # the trace note source_scoped_retrieve_node writes when it falls back
DEFAULT_TTL_S = 24 * 60 * 60  # eval/replay_safety_design.md §3

# Statuses of the `review` channel (R5's state machine). Resolved statuses are terminal.
PENDING, APPROVED, AMENDED, REJECTED, UNAVAILABLE = "pending", "approved", "amended", "rejected", "unavailable"
RESOLVED = (APPROVED, AMENDED, REJECTED)


def trigger_reason(question: str, trace_notes: list[str]) -> str | None:
    """The disjunct that fires, or None. Deterministic: the question text and the path record, nothing judged."""
    if EXPOSURE_LIMIT_PATTERN.search(question or ""):
        return "exposure_limit_named"
    if any(note.startswith(FALLBACK_NOTE) for note in trace_notes):
        return "scoped_fallback"
    return None


def ttl_seconds() -> int:
    return int(os.environ.get("REVIEW_TTL_S", DEFAULT_TTL_S))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    """Absolute UTC ISO-8601, e.g. 2026-10-09T07:00:00Z (refinement B)."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def expired(record: dict, moment: datetime | None = None) -> bool:
    expires_at = record.get("expires_at")
    if not expires_at:
        return False
    deadline = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (moment or now_utc()) >= deadline


def pending_record(reason: str, thread_id: str, moment: datetime | None = None) -> dict:
    start = moment or now_utc()
    return {"status": PENDING, "reason": reason, "thread_id": thread_id, "created_at": iso(start),
            "expires_at": iso(start + timedelta(seconds=ttl_seconds()))}


@lru_cache(maxsize=4)
def _saver_for(path: str):
    from langgraph.checkpoint.sqlite import SqliteSaver

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI runs sync handlers on a thread pool; SqliteSaver serializes access with its lock.
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


def checkpointer():
    """The process's checkpointer: a SqliteSaver on REVIEW_DB_PATH, or None (the gate then fails closed)."""
    path = os.environ.get("REVIEW_DB_PATH", "").strip()
    return _saver_for(path) if path else None


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def thread_lock(thread_id: str) -> threading.Lock:
    """Serializes resumes of one thread inside this process. Sufficient for the single uvicorn worker the Dockerfile
    runs; several workers would need a database-level guard (eval/KNOWN_LIMITATIONS.md, G10b)."""
    with _locks_guard:
        return _locks.setdefault(thread_id, threading.Lock())
