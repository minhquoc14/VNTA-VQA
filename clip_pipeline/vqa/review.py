"""Reviewer outputs.

`trimmed/` is the finished-product folder: every APPROVED clip is materialised
there, as one file for the whole clip or as several when the reviewer split it.
REJECTED clips are removed from it. Masters and delivered files are never
touched, so a rejection is always recoverable.
"""

from __future__ import annotations

from pathlib import Path

from . import db, media


def trimmed_dir(clip) -> Path:
    """`work/<video_id>/trimmed/` — derived from the clip's own paths."""
    src = Path(clip["delivered_path"] or clip["master_path"])
    return src.parent.parent / "trimmed"


def _source(clip) -> Path:
    # Always cut from delivered: that is the blurred video the reviewer watched.
    return Path(clip["delivered_path"] or clip["master_path"])


def discard(clip) -> int:
    """Remove a clip's outputs from `trimmed/`. Returns how many files went.

    Only the finished product is deleted. The master, the delivered file and the
    downloaded source all stay, so a rejection can be revisited.
    """
    removed = 0
    for seg in db.trim_segments(clip):
        p = Path(seg["path"])
        if p.exists():
            p.unlink()
            removed += 1
    db.set_trim_segments(clip["clip_id"], [])
    return removed


def purge_video(video_id: str) -> tuple[int, int]:
    """Remove a video from the queue, outputs and all. Returns (rows, files).

    `db.remove_video` only knows about rows. A video stopped mid-encode can
    already own APPROVED files in `trimmed/`, because clips become reviewable
    as soon as they are encoded -- the reviewer does not wait for the run to
    finish. Those files have to go too, or nothing will ever reference them
    again.

    Paths are collected first but deleted only if the row actually went: the
    status check lives inside the DELETE, so a refusal must leave the disk
    exactly as it was.
    """
    paths = [Path(seg["path"]) for seg in db.video_trim_segments(video_id)]
    removed = db.remove_video(video_id)
    if not removed:
        return 0, 0
    gone = 0
    for p in paths:
        if p.exists():
            p.unlink()
            gone += 1
    return removed, gone


def materialize(clip, segments_s: list[tuple[float, float]], cfg: dict) -> list[dict]:
    """Write the approved output into `trimmed/`, one file per segment.

    `segments_s` is a list of (start, end) in seconds relative to the clip. An
    empty list means "the whole clip" and produces a single file.
    """
    cid = clip["clip_id"]
    src = _source(clip)
    if not src.exists():
        raise media.MediaError(f"source missing: {src}")

    dur = clip["duration_ms"] / 1000.0
    segs = list(segments_s) or [(0.0, dur)]

    # The UI rounds times to 0.1s, so an "end of clip" value can land a hair past
    # the real duration. Clamp inside the tolerance; only reject a segment that
    # is meaningfully outside, which means the reviewer mistyped.
    TOL = 0.2
    checked: list[tuple[float, float]] = []
    for i, (a, b) in enumerate(segs, 1):
        if b <= a:
            raise media.MediaError(f"segment {i}: end must be after start")
        if a < -TOL or b > dur + TOL:
            raise media.MediaError(
                f"segment {i}: {a:.1f}-{b:.1f}s falls outside the clip (0-{dur:.1f}s)")
        checked.append((max(0.0, a), min(dur, b)))
    segs = checked

    # Clear previous outputs first, or shrinking the segment count leaves orphans
    # behind in the finished-product folder.
    discard(clip)

    out_dir = trimmed_dir(clip)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []
    for i, (a, b) in enumerate(segs, 1):
        dst = out_dir / f"{cid}_t{i:02d}.mp4"
        whole = a <= 0.001 and b >= dur - 0.001
        if whole:
            media.copy_stream(src, dst)   # no re-encode when nothing is cut
        else:
            media.trim_clip(src, dst, a, b, cfg)
        written.append({"start_ms": int(round(a * 1000)),
                        "end_ms": int(round(b * 1000)), "path": str(dst)})

    db.set_trim_segments(cid, written)
    return written
