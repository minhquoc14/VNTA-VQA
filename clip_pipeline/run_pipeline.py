"""Pipeline runner. Spawned as a subprocess by the Streamlit app.

Streamlit reruns its script on every widget interaction, so the pipeline cannot
live inside it. This writes state to SQLite and appends to a log file; the app
polls both.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("logs")


def main() -> int:
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"run_{stamp}.log"
    latest = LOG_DIR / "latest.log"

    handle = log_path.open("a", encoding="utf-8", buffering=1)
    try:
        latest.write_text(str(log_path), encoding="utf-8")
    except OSError:
        pass

    def log(msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
        handle.write(line + "\n")
        print(line, flush=True)

    try:
        from vqa import config, db, pipeline
        db.init()
        cfg = config.load()
    except Exception as exc:
        log(f"RUNNER STARTUP FAILED: {exc}")
        log(traceback.format_exc()[:2000])
        handle.close()
        return 1

    log(f"run start | config {config.config_hash(cfg)} | "
        f"review={'on' if cfg.get('review_enabled') else 'OFF (headless)'} | "
        f"blur={'on' if cfg.get('blur_enabled') else 'off'}")

    processed = 0
    while True:
        row = db.next_queued()
        if row is None:
            break
        video_id = row["youtube_video_id"]
        try:
            pipeline.process_video(video_id, row["canonical_url"], cfg, log)
        except Exception as exc:
            log(f"[{video_id}] UNEXPECTED ERROR: {exc}")
            log(traceback.format_exc()[:2000])
            db.set_status(video_id, db.FAILED, stage="unknown",
                          error_code="FAILED", error_detail=str(exc)[:500])
        processed += 1

    log(f"run finished - {processed} video(s) processed")
    handle.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
