"""Boundary policy and the clip filter.

Segmentation is PySceneDetect only. The counter-fusion path was removed: on real
Camera Giao thong footage the burned-in counter is transient (visible for a few
seconds per segment), so it is not a signal a static-region detector can recover,
and a mis-detected counter would make the two "independent" signals correlated -
inflating confidence on what is really one signal wearing two hats.

One signal means one confidence level. Review order is driven by clip duration
(see db.list_clips), not by confidence.
"""

from __future__ import annotations

import numpy as np

HIGH, MEDIUM, LOW = "HIGH", "MEDIUM", "LOW"


def boundaries_from_cuts(cuts: list[float], duration_s: float) -> list[dict]:
    """Visual cuts -> boundaries. Single signal, so everything is MEDIUM."""
    out: list[dict] = []
    for c in sorted(cuts):
        if not (0.0 < c < duration_s):
            continue
        if out and abs(c - out[-1]["t"]) < 0.4:
            continue
        out.append({"t": c, "confidence": MEDIUM, "source": "content"})
    return out


def to_clips(boundaries: list[dict], duration_s: float, fps: float,
             cfg: dict) -> list[dict]:
    """Turn boundaries into clip segments and apply the clip filter."""
    trim = int(cfg.get("safety_trim_frames", 2)) / max(fps, 1.0)
    min_d = float(cfg["min_duration_s"])
    max_d = float(cfg["max_duration_s"])

    edges = [0.0] + [b["t"] for b in boundaries] + [duration_s]
    confs = [HIGH] + [b["confidence"] for b in boundaries]

    clips: list[dict] = []
    seq = 0
    for i in range(len(edges) - 1):
        start = edges[i] + (trim if i > 0 else 0.0)
        end = edges[i + 1] - trim
        dur = end - start
        if dur <= 0:
            continue

        flags: list[str] = []
        if dur < min_d:
            continue  # too short for meaningful VQA — dropped
        if dur > max_d:
            flags.append("TOO_LONG")  # reviewer decides; never auto-split

        # A clip is only as trustworthy as its weakest edge.
        left = confs[i] if i < len(confs) else MEDIUM
        right = confs[i + 1] if i + 1 < len(confs) else HIGH
        order = {LOW: 0, MEDIUM: 1, HIGH: 2}
        confidence = min((left, right), key=lambda c: order[c])
        if confidence == LOW:
            flags.append("LOW_CONFIDENCE")

        seq += 1
        clips.append({
            "seq": seq,
            "start_s": round(start, 3),
            "end_s": round(end, 3),
            "duration_s": round(dur, 3),
            "confidence": confidence,
            "flags": flags,
        })
    return clips


# A real traffic clip can be genuinely quiet (empty street at night), so the
# frozen-frame floor is deliberately low: catch title cards, not calm scenes.
MOTION_FLOOR = 0.25
BLACK_LEVEL = 12.0


def blank_reason(frames: np.ndarray) -> str | None:
    """Return why a clip should be dropped, or None to keep it.

    Catches intro cards, outros and bumpers - not quiet traffic.
    """
    if frames.shape[0] < 2:
        return None
    if float(frames.mean()) < BLACK_LEVEL:
        return "black"
    motion = float(np.mean([
        np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16)))
        for a, b in zip(frames, frames[1:])
    ]))
    if motion < MOTION_FLOOR:
        return f"static(motion={motion:.2f})"
    return None
