"""Overlay calibration — find static overlay regions.

Per-pixel temporal variance: a burned-in overlay barely changes while the scene
behind it moves. Real scene content moves, so this cannot mask a license plate,
road sign or shop signage — which a learned text detector absolutely would.
CPU-only, deterministic, cached per channel.
"""

from __future__ import annotations

import cv2
import numpy as np

from .. import media

SAMPLE_W = 480          # analysis width; boxes are scaled back to source pixels
TARGET_FRAMES = 300
VAR_MAX = 18.0          # absolute "static" floor (0-255 gray variance)
# Real channel branding is alpha-blended, not opaque: the scene shows through,
# so no pixel is ever truly static (measured floor on real footage: var 258,
# vs 18 for a synthetic opaque overlay). The signal is *relative* - overlay
# pixels are still ~4x steadier than scene pixels. Take the steadiest VAR_PCT%
# of the frame, with VAR_MAX as a floor so opaque overlays behave as before.
VAR_PCT = 1.0
DETAIL_MIN = 22.0       # overlays have text/logo contrast; flat sky does not
MARGIN = 0.25           # keep boxes whose centre sits in the outer 25%
AREA_MIN = 0.0005
AREA_MAX = 0.08


def _candidate_boxes(frames: np.ndarray) -> list[dict]:
    var = frames.astype(np.float32).var(axis=0)
    mean = frames.mean(axis=0).astype(np.uint8)
    h, w = var.shape

    cutoff = max(VAR_MAX, float(np.percentile(var, VAR_PCT)))
    static = (var < cutoff).astype(np.uint8) * 255
    static = cv2.morphologyEx(static, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    static = cv2.morphologyEx(static, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    n, _, stats, centroids = cv2.connectedComponentsWithStats(static, connectivity=8)
    frame_area = float(h * w)
    boxes: list[dict] = []

    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if not (AREA_MIN <= area / frame_area <= AREA_MAX):
            continue

        cx, cy = centroids[i]
        in_margin = (
            cx < w * MARGIN or cx > w * (1 - MARGIN)
            or cy < h * MARGIN or cy > h * (1 - MARGIN)
        )
        if not in_margin:
            continue

        patch = mean[y:y + bh, x:x + bw]
        if patch.size == 0 or float(patch.std()) < DETAIL_MIN:
            continue  # flat static region (sky, road) — not an overlay

        boxes.append({"x": int(x), "y": int(y), "w": int(bw), "h": int(bh),
                      "area": int(area), "detail": float(patch.std())})

    boxes.sort(key=lambda b: b["area"], reverse=True)
    return boxes[:6]


def calibrate(video_path, duration_s: float) -> list[dict]:
    """Return static overlay regions in SOURCE pixel coordinates."""
    fps = max(0.05, min(2.0, TARGET_FRAMES / max(duration_s, 1.0)))
    frames = media.sample_gray(video_path, fps=fps, width=SAMPLE_W)
    if frames.shape[0] < 8:
        return []

    boxes = _candidate_boxes(frames)
    if not boxes:
        return []

    meta = media.probe(video_path)
    scale = meta["width"] / float(frames.shape[2]) if frames.shape[2] else 1.0
    pad = 2
    return [{
        "x": max(0, int(b["x"] * scale) - pad),
        "y": max(0, int(b["y"] * scale) - pad),
        "w": min(meta["width"], int(b["w"] * scale) + 2 * pad),
        "h": min(meta["height"], int(b["h"] * scale) + 2 * pad),
        "role": "overlay",
    } for b in boxes]
