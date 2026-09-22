"""SQLite state. Four tables, WAL mode (Streamlit reads while the subprocess writes)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("pipeline.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    channel_id      TEXT PRIMARY KEY,
    name            TEXT,
    overlay_regions TEXT,
    calibrated_at   TEXT
);

CREATE TABLE IF NOT EXISTS videos (
    youtube_video_id TEXT PRIMARY KEY,
    canonical_url    TEXT NOT NULL,
    title            TEXT,
    channel_id       TEXT,
    channel_name     TEXT,
    duration_s       REAL,
    status           TEXT NOT NULL,
    stage            TEXT,
    error_code       TEXT,
    error_detail     TEXT,
    retry_count      INTEGER DEFAULT 0,
    config_snapshot  TEXT,
    n_clips          INTEGER DEFAULT 0,
    n_boundaries     INTEGER DEFAULT 0,
    created_at       TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS clips (
    clip_id          TEXT PRIMARY KEY,
    youtube_video_id TEXT NOT NULL,
    seq              INTEGER,
    start_ms         INTEGER NOT NULL,
    end_ms           INTEGER NOT NULL,
    duration_ms      INTEGER NOT NULL,
    confidence       TEXT NOT NULL,
    flags            TEXT,
    master_path      TEXT,
    delivered_path   TEXT,
    pipeline_version TEXT,
    config_hash      TEXT,
    trim_segments    TEXT,
    created_at       TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    clip_id     TEXT PRIMARY KEY,
    decision    TEXT NOT NULL,
    reviewed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_clips_video ON clips(youtube_video_id);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
"""

# Video states (architecture.md §9)
QUEUED = "QUEUED"
DOWNLOADING = "DOWNLOADING"
PROCESSING = "PROCESSING"
READY_FOR_REVIEW = "READY_FOR_REVIEW"
DONE = "DONE"
DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
FAILED = "FAILED"
# Pressing Stop kills the runner mid-video. Without a state of its own that row
# sits at DOWNLOADING or PROCESSING forever: `next_queued()` will not pick it up,
# neither retry button sees it, and removal refuses it.
STOPPED = "STOPPED"

RETRYABLE = (DOWNLOAD_FAILED, FAILED, STOPPED)

# Review priority: longer clips carry more to look at, so they go first.
PRIORITY_SECONDS = 15
TOP, LOW_PRIORITY = "TOP", "LOW"


def priority(duration_ms: int) -> str:
    """> 15s is TOP priority; <= 15s is LOW."""
    return TOP if duration_ms > PRIORITY_SECONDS * 1000 else LOW_PRIORITY

# Clip decisions
UNREVIEWED = "UNREVIEWED"
APPROVED = "APPROVED"
REJECTED = "REJECTED"
FLAGGED = "FLAGGED"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with tx() as conn:
        conn.executescript(SCHEMA)
        # Additive migration for databases created before trimming existed.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(clips)")}
        if "trim_segments" not in have:
            conn.execute("ALTER TABLE clips ADD COLUMN trim_segments TEXT")
            have.add("trim_segments")
        # Fold a pre-existing single trim into the segment list.
        if "trimmed_path" in have:
            for r in conn.execute(
                    "SELECT clip_id, trimmed_path, trim_start_ms, trim_end_ms"
                    " FROM clips WHERE trimmed_path IS NOT NULL"
                    " AND trim_segments IS NULL"):
                conn.execute(
                    "UPDATE clips SET trim_segments=? WHERE clip_id=?",
                    (json.dumps([{"start_ms": r["trim_start_ms"],
                                  "end_ms": r["trim_end_ms"],
                                  "path": r["trimmed_path"]}]), r["clip_id"]))


# ── videos ────────────────────────────────────────────────────────────────


def enqueue(video_id: str, url: str) -> bool:
    """Insert a video as QUEUED. Returns False if it already exists."""
    with tx() as conn:
        cur = conn.execute(
            "SELECT 1 FROM videos WHERE youtube_video_id = ?", (video_id,)
        )
        if cur.fetchone():
            return False
        conn.execute(
            "INSERT INTO videos (youtube_video_id, canonical_url, status,"
            " created_at, updated_at) VALUES (?,?,?,?,?)",
            (video_id, url, QUEUED, now(), now()),
        )
    return True


def enqueue_many(pairs) -> tuple[list[str], list[tuple[str, str]]]:
    """Queue a batch of (video_id, url). Returns (added ids, duplicates).

    Each duplicate comes back with the status it already has, because "3
    duplicates" leaves the user unable to tell a finished video from one whose
    download failed and needs removing.
    """
    added: list[str] = []
    dupes: list[tuple[str, str]] = []
    for video_id, url in pairs:
        if enqueue(video_id, url):
            added.append(video_id)
        else:
            row = get_video(video_id)
            dupes.append((video_id, row["status"] if row else "?"))
    return added, dupes


REMOVABLE = (QUEUED, DOWNLOAD_FAILED, FAILED, STOPPED)


def remove_video(video_id: str) -> int:
    """Take a URL back out of the queue. Returns 1 if it went, 0 if refused.

    Only rows the pipeline has finished with are removable. A STOPPED video may
    already own half-encoded clips, so its `clips` and `reviews` rows go with
    it -- `clips` has no foreign key to `videos`, and leftovers would be handed
    to the reviewer by `list_clips()` forever.

    The status test is inside the DELETE rather than in the caller: the runner
    is a separate process and can move a row from QUEUED to DOWNLOADING between
    the two. Nothing outside the database is touched here; files the reviewer
    already produced are `review.purge_video`'s job.
    """
    placeholders = ",".join("?" * len(REMOVABLE))
    with tx() as conn:
        cur = conn.execute(
            f"DELETE FROM videos WHERE youtube_video_id=? AND status IN"
            f" ({placeholders})",
            (video_id, *REMOVABLE),
        )
        if not cur.rowcount:
            return 0
        conn.execute(
            "DELETE FROM reviews WHERE clip_id IN"
            " (SELECT clip_id FROM clips WHERE youtube_video_id=?)", (video_id,))
        conn.execute("DELETE FROM clips WHERE youtube_video_id=?", (video_id,))
        return cur.rowcount


def set_status(video_id: str, status: str, *, stage: str | None = None,
               error_code: str | None = None, error_detail: str | None = None) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE videos SET status=?, stage=?, error_code=?, error_detail=?,"
            " updated_at=? WHERE youtube_video_id=?",
            (status, stage, error_code, error_detail, now(), video_id),
        )


def update_video(video_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as conn:
        conn.execute(
            f"UPDATE videos SET {cols}, updated_at=? WHERE youtube_video_id=?",
            (*fields.values(), now(), video_id),
        )


def get_video(video_id: str) -> sqlite3.Row | None:
    with tx() as conn:
        return conn.execute(
            "SELECT * FROM videos WHERE youtube_video_id=?", (video_id,)
        ).fetchone()


def list_videos(status: str | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM videos"
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    q += " ORDER BY created_at"
    with tx() as conn:
        return conn.execute(q, args).fetchall()


def next_queued() -> sqlite3.Row | None:
    with tx() as conn:
        return conn.execute(
            "SELECT * FROM videos WHERE status=? ORDER BY created_at LIMIT 1", (QUEUED,)
        ).fetchone()


def mark_stopped() -> int:
    """Whatever the killed runner was working on becomes STOPPED. Returns rows.

    Only in-flight rows are touched. Videos it had already finished keep their
    own result, and ones it never reached stay QUEUED.

    Call this *after* the process has actually exited, not just after
    `terminate()`: the runner writes status too, and a dying one can overwrite
    STOPPED with the stage it was in.
    """
    with tx() as conn:
        cur = conn.execute(
            "UPDATE videos SET status=?, updated_at=? WHERE status IN (?,?)",
            (STOPPED, now(), DOWNLOADING, PROCESSING),
        )
        return cur.rowcount


def retry_status(status: str) -> int:
    """Reset a whole failure class back to QUEUED."""
    with tx() as conn:
        cur = conn.execute(
            "UPDATE videos SET status=?, error_code=NULL, error_detail=NULL,"
            " retry_count=retry_count+1, updated_at=? WHERE status=?",
            (QUEUED, now(), status),
        )
        return cur.rowcount


# ── channels ──────────────────────────────────────────────────────────────


def get_channel(channel_id: str) -> sqlite3.Row | None:
    with tx() as conn:
        return conn.execute(
            "SELECT * FROM channels WHERE channel_id=?", (channel_id,)
        ).fetchone()


def save_channel(channel_id: str, name: str, regions: list) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO channels (channel_id, name, overlay_regions,"
            " calibrated_at) VALUES (?,?,?,?)"
            " ON CONFLICT(channel_id) DO UPDATE SET name=excluded.name,"
            " overlay_regions=excluded.overlay_regions,"
            " calibrated_at=excluded.calibrated_at",
            (channel_id, name, json.dumps(regions), now()),
        )


# ── clips ─────────────────────────────────────────────────────────────────


def insert_clip(clip: dict) -> None:
    with tx() as conn:
        # UPSERT, not INSERT OR REPLACE: a replace drops the whole row, wiping
        # the reviewer's trim columns on every re-run.
        conn.execute(
            "INSERT INTO clips (clip_id, youtube_video_id, seq, start_ms,"
            " end_ms, duration_ms, confidence, flags, master_path, delivered_path,"
            " pipeline_version, config_hash, created_at)"
            " VALUES (:clip_id,:youtube_video_id,:seq,:start_ms,:end_ms,:duration_ms,"
            ":confidence,:flags,:master_path,:delivered_path,:pipeline_version,"
            ":config_hash,:created_at)"
            " ON CONFLICT(clip_id) DO UPDATE SET"
            " youtube_video_id=excluded.youtube_video_id, seq=excluded.seq,"
            " start_ms=excluded.start_ms, end_ms=excluded.end_ms,"
            " duration_ms=excluded.duration_ms, confidence=excluded.confidence,"
            " flags=excluded.flags, master_path=excluded.master_path,"
            " delivered_path=excluded.delivered_path,"
            " pipeline_version=excluded.pipeline_version,"
            " config_hash=excluded.config_hash",
            {**clip, "created_at": now()},
        )
        conn.execute(
            "INSERT OR IGNORE INTO reviews (clip_id, decision) VALUES (?,?)",
            (clip["clip_id"], UNREVIEWED),
        )


def list_clips(video_id: str | None = None, decision: str | None = None,
               confidence: str | None = None) -> list[sqlite3.Row]:
    q = ("SELECT c.*, r.decision, r.reviewed_at FROM clips c"
         " JOIN reviews r ON r.clip_id = c.clip_id WHERE 1=1")
    args: list = []
    if video_id:
        q += " AND c.youtube_video_id=?"
        args.append(video_id)
    if decision:
        q += " AND r.decision=?"
        args.append(decision)
    if confidence:
        q += " AND c.confidence=?"
        args.append(confidence)
    # TOP priority first, then longest within a band. Priority comes from the
    # ORIGINAL duration and never changes when a reviewer trims a clip.
    q += (f" ORDER BY CASE WHEN c.duration_ms > {PRIORITY_SECONDS * 1000}"
          " THEN 0 ELSE 1 END, c.duration_ms DESC,"
          " c.youtube_video_id, c.start_ms")
    with tx() as conn:
        return conn.execute(q, args).fetchall()


def get_clip(clip_id: str) -> sqlite3.Row | None:
    with tx() as conn:
        return conn.execute(
            "SELECT * FROM clips WHERE clip_id=?", (clip_id,)).fetchone()


def set_decision(clip_id: str, decision: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO reviews (clip_id, decision, reviewed_at) VALUES (?,?,?)"
            " ON CONFLICT(clip_id) DO UPDATE SET decision=excluded.decision,"
            " reviewed_at=excluded.reviewed_at",
            (clip_id, decision, now()),
        )


def clip_counts() -> dict:
    with tx() as conn:
        rows = conn.execute(
            "SELECT r.decision, COUNT(*) n FROM reviews r GROUP BY r.decision"
        ).fetchall()
        conf = conn.execute(
            "SELECT confidence, COUNT(*) n FROM clips GROUP BY confidence"
        ).fetchall()
    return {
        "by_decision": {r["decision"]: r["n"] for r in rows},
        "by_confidence": {r["confidence"]: r["n"] for r in conf},
    }


def set_trim_segments(clip_id: str, segments: list[dict]) -> None:
    """Record the materialised output segments. Masters are never touched."""
    with tx() as conn:
        conn.execute("UPDATE clips SET trim_segments=? WHERE clip_id=?",
                     (json.dumps(segments) if segments else None, clip_id))


def video_trim_segments(video_id: str) -> list[dict]:
    """Every materialised segment belonging to one video, flattened.

    Reads `clips` directly rather than going through `list_clips()`, which
    inner-joins `reviews` and would silently skip a clip whose review row never
    got written.
    """
    with tx() as conn:
        rows = conn.execute(
            "SELECT trim_segments FROM clips WHERE youtube_video_id=?"
            " AND trim_segments IS NOT NULL", (video_id,)).fetchall()
    return [seg for r in rows for seg in json.loads(r["trim_segments"])]


def trim_segments(row) -> list[dict]:
    """Parse a clip row's segment list. [] when the clip was never materialised."""
    raw = row["trim_segments"] if row is not None else None
    return json.loads(raw) if raw else []
