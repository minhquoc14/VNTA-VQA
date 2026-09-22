"""ffmpeg / ffprobe helpers.

Frames are sampled by piping raw grayscale from ffmpeg rather than seeking with
OpenCV — ffmpeg decodes once, crops in the filter graph, and hands back only the
pixels we asked for. Much faster than per-frame seeking on long videos.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# Windows: don't flash a console window for every ffmpeg call.
_CREATE_NO_WINDOW = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, creationflags=_CREATE_NO_WINDOW, **kw
    )


class MediaError(RuntimeError):
    pass


def probe(path: Path) -> dict:
    """Return {width, height, fps, duration_s, codec, nb_frames}."""
    cp = _run([
        FFPROBE, "-v", "error", "-select_streams", "v:0",
        "-show_streams", "-show_format", "-of", "json", str(path),
    ])
    if cp.returncode != 0:
        raise MediaError(f"ffprobe failed: {cp.stderr.decode(errors='replace')[:300]}")

    data = json.loads(cp.stdout or b"{}")
    streams = data.get("streams") or []
    if not streams:
        raise MediaError("no video stream")
    s = streams[0]

    num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
    try:
        fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        fps = 0.0

    duration = 0.0
    for src in (s.get("duration"), (data.get("format") or {}).get("duration")):
        try:
            duration = float(src)
            break
        except (TypeError, ValueError):
            continue

    return {
        "width": int(s.get("width") or 0),
        "height": int(s.get("height") or 0),
        "fps": fps,
        "duration_s": duration,
        "codec": s.get("codec_name") or "",
        "nb_frames": int(s.get("nb_frames") or 0),
    }


def sample_gray(path: Path, fps: float, width: int,
                crop: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """Sample grayscale frames at `fps`, scaled to `width` px wide.

    `crop` is (x, y, w, h) in source pixels, applied before scaling.
    Returns (n, h, w) uint8. Empty array if nothing decoded.
    """
    filters = []
    if crop:
        x, y, w, h = crop
        filters.append(f"crop={w}:{h}:{x}:{y}")
    filters.append(f"fps={fps}")
    filters.append(f"scale={width}:-2:flags=bilinear")
    filters.append("format=gray")

    # Ask ffmpeg what the post-filter size is, so we can reshape the raw bytes.
    meta = probe(path)
    if crop:
        src_w, src_h = crop[2], crop[3]
    else:
        src_w, src_h = meta["width"], meta["height"]
    if not src_w or not src_h:
        return np.empty((0, 0, 0), dtype=np.uint8)
    out_w = width
    out_h = max(2, int(round(src_h * width / src_w / 2)) * 2)

    cmd = [
        FFMPEG, "-v", "error", "-i", str(path),
        "-vf", ",".join(filters), "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    cp = _run(cmd)
    if cp.returncode != 0 and not cp.stdout:
        raise MediaError(f"ffmpeg sample failed: {cp.stderr.decode(errors='replace')[:300]}")

    frame_bytes = out_w * out_h
    n = len(cp.stdout) // frame_bytes
    if n == 0:
        return np.empty((0, out_h, out_w), dtype=np.uint8)
    buf = np.frombuffer(cp.stdout[: n * frame_bytes], dtype=np.uint8)
    return buf.reshape(n, out_h, out_w)


def sample_window_gray(path: Path, start_s: float, dur_s: float,
                       width: int = 240) -> tuple[np.ndarray, float]:
    """Decode a short window at full frame rate. Returns (frames, fps).

    Used only to refine boundaries that have no frame-accurate visual cut, so it
    runs on a handful of seconds rather than the whole video.
    """
    meta = probe(path)
    src_w, src_h = meta["width"], meta["height"]
    if not src_w or not src_h:
        return np.empty((0, 0, 0), dtype=np.uint8), 0.0
    out_w = width
    out_h = max(2, int(round(src_h * width / src_w / 2)) * 2)

    cp = _run([
        FFMPEG, "-v", "error", "-ss", f"{max(0.0, start_s):.3f}", "-i", str(path),
        "-t", f"{dur_s:.3f}", "-vf", f"scale={out_w}:-2:flags=bilinear,format=gray",
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ])
    frame_bytes = out_w * out_h
    n = len(cp.stdout) // frame_bytes if frame_bytes else 0
    if n == 0:
        return np.empty((0, out_h, out_w), dtype=np.uint8), meta["fps"]
    buf = np.frombuffer(cp.stdout[: n * frame_bytes], dtype=np.uint8)
    return buf.reshape(n, out_h, out_w), meta["fps"]


def cut_master(src: Path, dst: Path, start_s: float, dur_s: float) -> None:
    """Lossless stream copy. Falls back to a re-encode if the copy is unusable.

    -c copy cuts on keyframes, so the result can drift or come out empty when the
    boundary isn't keyframe-aligned. We verify the duration and re-encode if needed.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-v", "error", "-y", "-ss", f"{start_s:.3f}", "-i", str(src),
        "-t", f"{dur_s:.3f}", "-c", "copy", "-avoid_negative_ts", "1", str(dst),
    ]
    cp = _run(cmd)

    ok = cp.returncode == 0 and dst.exists() and dst.stat().st_size > 1024
    if ok:
        try:
            got = probe(dst)["duration_s"]
            # Accept a half-second of keyframe drift; anything worse gets re-encoded.
            ok = abs(got - dur_s) <= 0.5
        except MediaError:
            ok = False
    if ok:
        return

    cp = _run([
        FFMPEG, "-v", "error", "-y", "-ss", f"{start_s:.3f}", "-i", str(src),
        "-t", f"{dur_s:.3f}", "-c:v", "libx264", "-crf", "16", "-preset", "veryfast",
        "-c:a", "aac", str(dst),
    ])
    if cp.returncode != 0:
        raise MediaError(f"cut failed: {cp.stderr.decode(errors='replace')[:300]}")


def fixed_regions(cfg: dict, width: int, height: int) -> list[dict]:
    """Config bands (frame fractions) -> source-pixel boxes.

    These are applied to every video regardless of what calibration finds:
    the burned-in clock, the compilation counter and the source-camera name are
    intermittent, so per-pixel temporal variance cannot detect them.
    """
    out: list[dict] = []
    for b in cfg.get("fixed_blur", []):
        if not b.get("enabled", True):
            continue
        x = max(0, min(width - 2, int(float(b["x"]) * width)))
        y = max(0, min(height - 2, int(float(b["y"]) * height)))
        w = max(2, min(width - x, int(float(b["w"]) * width)))
        h = max(2, min(height - y, int(float(b["h"]) * height)))
        # ffmpeg's crop needs even dimensions on yuv420p.
        out.append({"x": x - x % 2, "y": y - y % 2,
                    "w": w - w % 2, "h": h - h % 2,
                    "role": "fixed", "name": b.get("name", "fixed")})
    return out


def blur_filter(regions: list[dict], cfg: dict) -> str | None:
    """Build the overlay-removal filter_complex, or None if disabled/empty.

    blur + color_kill -> crop, gblur, hue, eq, overlay
    blur only         -> crop, gblur, overlay
    """
    if not cfg.get("blur_enabled") or not regions:
        return None

    sigma = float(cfg.get("blur_sigma", 20))
    parts, last = [], "0:v"
    for i, r in enumerate(regions):
        x, y, w, h = int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])
        # gblur takes a real sigma. boxblur's radius is capped by the chroma
        # plane size, which fails outright on small overlay regions.
        chain = [f"crop={w}:{h}:{x}:{y}", f"gblur=sigma={sigma}:steps=3"]
        if cfg.get("color_kill_enabled"):
            chain.append(f"hue=s={float(cfg.get('desaturate', 0.15))}")
            chain.append(f"eq=brightness=-{float(cfg.get('darken', 0.10))}")

        # Explicit split: each stage needs the same frame twice (as the overlay
        # base and as the crop source). Reusing one label for both silently drops
        # the overlay for all but the last region.
        base, cut, out = f"b{i}", f"c{i}", f"v{i}"
        parts.append(f"[{last}]split=2[{base}][{cut}]")
        parts.append(f"[{cut}]{','.join(chain)}[r{i}]")
        parts.append(f"[{base}][r{i}]overlay={x}:{y}[{out}]")
        last = out
    return ";".join(parts) + f";[{last}]null[vout]"


def encode_delivered(src: Path, dst: Path, regions: list[dict], cfg: dict) -> None:
    """Derive the delivered clip from a master. No blur -> plain copy."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    fc = blur_filter(regions, cfg)

    if fc is None:
        cp = _run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-c", "copy", str(dst)])
        if cp.returncode != 0:
            raise MediaError(f"copy failed: {cp.stderr.decode(errors='replace')[:300]}")
        return

    if cfg.get("encoder") == "h264_nvenc":
        # NVENC has no CRF; -cq is the equivalent.
        venc = ["-c:v", "h264_nvenc", "-rc", "vbr",
                "-cq", str(int(cfg.get("nvenc_cq", 19))), "-preset", "p5"]
    else:
        venc = ["-c:v", "libx264", "-crf", str(int(cfg.get("libx264_crf", 18))),
                "-preset", "medium"]

    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(src),
           "-filter_complex", fc, "-map", "[vout]", *venc, str(dst)]
    cp = _run(cmd)

    if cp.returncode != 0 and cfg.get("encoder") == "h264_nvenc":
        # NVENC can fail on a busy or unsupported GPU — fall back to CPU rather
        # than losing the clip.
        cmd = [FFMPEG, "-v", "error", "-y", "-i", str(src),
               "-filter_complex", fc, "-map", "[vout]",
               "-c:v", "libx264", "-crf", str(int(cfg.get("libx264_crf", 18))),
               "-preset", "veryfast", str(dst)]
        cp = _run(cmd)

    if cp.returncode != 0:
        raise MediaError(f"encode failed: {cp.stderr.decode(errors='replace')[:300]}")


def trim_clip(src: Path, dst: Path, start_s: float, end_s: float, cfg: dict) -> None:
    """Write src[start_s:end_s] to dst as a new file. Never touches src.

    Re-encodes rather than stream-copying: a reviewer's in/out points are almost
    never keyframe-aligned, and -c copy would silently snap them.
    """
    dur = end_s - start_s
    if dur <= 0:
        raise MediaError("end time must be after start time")
    dst.parent.mkdir(parents=True, exist_ok=True)

    if cfg.get("encoder") == "h264_nvenc":
        venc = ["-c:v", "h264_nvenc", "-rc", "vbr",
                "-cq", str(int(cfg.get("nvenc_cq", 23))), "-preset", "p5"]
    else:
        venc = ["-c:v", "libx264", "-crf", str(int(cfg.get("libx264_crf", 18))),
                "-preset", "veryfast"]

    base = [FFMPEG, "-v", "error", "-y", "-ss", f"{start_s:.3f}", "-i", str(src),
            "-t", f"{dur:.3f}"]
    cp = _run([*base, *venc, "-c:a", "aac", str(dst)])
    if cp.returncode != 0 and cfg.get("encoder") == "h264_nvenc":
        cp = _run([*base, "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                   "-c:a", "aac", str(dst)])
    if cp.returncode != 0:
        raise MediaError(f"trim failed: {cp.stderr.decode(errors='replace')[:300]}")


def parse_time(text: str) -> float:
    """Accept `HH:MM:SS`, `MM:SS`, `SS.s` or a bare number of seconds."""
    t = str(text).strip()
    if not t:
        raise MediaError("empty time")
    if ":" in t:
        parts = t.split(":")
        if len(parts) > 3:
            raise MediaError(f"bad time: {text}")
        total = 0.0
        for part in parts:
            total = total * 60 + float(part or 0)
        return total
    return float(t)


def fmt_time(seconds: float) -> str:
    """Seconds -> HH:MM:SS.s, the same shape parse_time accepts."""
    s = max(0.0, float(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{sec:04.1f}"


def copy_stream(src: Path, dst: Path) -> None:
    """Container copy, no re-encode. Used when a segment is the whole clip."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cp = _run([FFMPEG, "-v", "error", "-y", "-i", str(src), "-c", "copy", str(dst)])
    if cp.returncode != 0:
        raise MediaError(f"copy failed: {cp.stderr.decode(errors='replace')[:300]}")
