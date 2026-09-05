"""SQLite: run state + cost ledger. Single file, zero ops, doubles as the audit trail."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'started',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    approved_script_at TEXT,
    approved_video_at TEXT,
    final_path TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    node TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Empty in Phase 1 (no paid calls yet) but wired up now so Phase 2's
-- HeyGen spend has an audit trail from day one instead of being bolted on.
CREATE TABLE IF NOT EXISTS costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    usd REAL NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which B-roll/photo clip (identified by a hash of its actual source URL,
-- not the search query -- see broll.py) got used in which run, so a clip
-- seen in a recent run can be skipped in favor of a fresh one instead of
-- showing the same footage on every video that happens to search a
-- similar phrase. See config.BROLL_RECENCY_RUNS.
CREATE TABLE IF NOT EXISTS broll_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def create_run(run_id: str, topic: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO runs (id, topic) VALUES (?, ?)", (run_id, topic))


def log_event(run_id: str, node: str, status: str, detail: str = "") -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO run_events (run_id, node, status, detail) VALUES (?, ?, ?, ?)",
            (run_id, node, status, detail),
        )


def mark_status(run_id: str, status: str, **fields) -> None:
    with connect() as conn:
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values())
        conn.execute(f"UPDATE runs SET status = ?{',' if cols else ''} {cols} WHERE id = ?",
                     [status, *vals, run_id])


def record_broll_usage(clip_id: str, run_id: str) -> None:
    """Called every time a clip is actually used in a beat -- whether it
    was just downloaded or already sitting in the cache from a previous
    run -- so recency tracking reflects real usage, not just downloads."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO broll_usage (clip_id, run_id) VALUES (?, ?)",
            (clip_id, run_id),
        )


def recent_broll_clip_ids(limit_runs: int) -> set[str]:
    """Every clip_id used in any of the most recent `limit_runs` DISTINCT
    runs (not just the most recent N rows -- a single run can use a dozen
    clips, and we want a window measured in videos, not beats)."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT clip_id FROM broll_usage WHERE run_id IN (
                SELECT run_id FROM (
                    SELECT run_id, MAX(at) AS last_at FROM broll_usage
                    GROUP BY run_id ORDER BY last_at DESC LIMIT ?
                )
            )
            """,
            (limit_runs,),
        ).fetchall()
        return {row["clip_id"] for row in rows}


def get_pending_gate_run() -> sqlite3.Row | None:
    """
    Most recent run still parked at a human gate (status like
    'awaiting_<gate>') -- used by telegram_bot.py to rehydrate its
    in-memory session after a restart. A restart wipes that in-memory
    state, but the LangGraph checkpoint (and this status marker) survive
    in the sqlite files regardless of process lifetime, so a run waiting
    on you doesn't just silently vanish if the bot process dies or gets
    restarted while it's mid-flight -- confirmed the hard way once
    already, see BACKLOG.md.
    """
    with connect() as conn:
        return conn.execute(
            "SELECT id, status FROM runs WHERE status LIKE 'awaiting_%' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()


def total_spent_this_month() -> float:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(usd), 0) AS total FROM costs "
            "WHERE strftime('%Y-%m', at) = strftime('%Y-%m', 'now')"
        ).fetchone()
        return float(row["total"])
