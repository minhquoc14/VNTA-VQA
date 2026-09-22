"""Pipeline configuration.

Config lives in config.json, written by the Streamlit sidebar. Nothing else edits it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CONFIG_PATH = Path("config.json")
PIPELINE_VERSION = "v0.1.0"

DEFAULTS = {
    # Toggles
    "review_enabled": True,
    "blur_enabled": True,
    "color_kill_enabled": True,
    # Overlay removal
    "blur_sigma": 20,
    # Fixed blur bands, applied to EVERY video on top of whatever calibration
    # finds. Coordinates are fractions of the frame, so one setting works at any
    # resolution. Defaults cover the burned-in clock, the compilation counter
    # and the source-camera name on Camera Giao thong footage.
    "fixed_blur": [
        {"name": "bottom_left",  "enabled": True, "x": 0.0,  "y": 0.88, "w": 0.30, "h": 0.12},
        {"name": "top_left",     "enabled": True, "x": 0.0,  "y": 0.0,  "w": 0.30, "h": 0.08},
        {"name": "bottom_right", "enabled": True, "x": 0.68, "y": 0.84, "w": 0.32, "h": 0.11},
        {"name": "middle_bottom", "enabled": True, "x": 0.30, "y": 0.78, "w": 0.40, "h": 0.22},
    ],
    "desaturate": 0.15,
    "darken": 0.10,
    # Detection
    "content_threshold": 27.0,
    # Clip policy
    "min_duration_s": 5.0,
    "max_duration_s": 30.0,
    "safety_trim_frames": 2,
    # Encoding
    "encoder": "h264_nvenc",
    "nvenc_cq": 23,   # 19 was ~6x the master size; 23 is visually equivalent
    "libx264_crf": 18,
    # Review. Mark cut builds "impact +/- pad" and shrinks from there; see
    # GUIDE.md 2.4. Reviewer-side only, but it lives here so the sidebar value
    # persists across sessions rather than dying with the browser tab.
    "trim_pad_s": 5.0,
    # Paths
    "work_dir": "work",
    "output_dir": "export",
    # Download
    "max_height": 1080,
    # YouTube bot-gates datacenter/flagged IPs. Either path works; the file is
    # checked first. Both stay out of git (see .gitignore).
    "cookies_file": "",          # path to a Netscape cookies.txt
    "cookies_browser": "",       # "", "chrome", "edge", "firefox", "brave"
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            # A broken config file should not stop the pipeline; defaults are fine.
            pass
    return cfg


def save(cfg: dict) -> None:
    merged = dict(DEFAULTS)
    merged.update(cfg)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")


def config_hash(cfg: dict) -> str:
    """Stable short hash of the config — stamped on every clip for provenance."""
    blob = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


def work_dir(cfg: dict, video_id: str) -> Path:
    return Path(cfg["work_dir"]) / video_id
