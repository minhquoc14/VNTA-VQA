"""YouTube URL canonicalization.

The video ID is the deduplication key — never the raw URL string, since
watch?v=ABC, youtu.be/ABC and watch?v=ABC&t=30 are all the same video.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

_PATH_PREFIXES = ("/shorts/", "/embed/", "/v/", "/live/")


def video_id(url: str) -> str | None:
    """Extract the 11-char YouTube video ID, or None if this isn't one."""
    url = (url or "").strip()
    if not url:
        return None

    # Bare ID pasted directly
    if ID_RE.match(url):
        return url

    if "//" not in url:
        url = "https://" + url

    try:
        p = urlparse(url)
    except ValueError:
        return None

    host = (p.hostname or "").lower().removeprefix("www.")

    if host in ("youtu.be",):
        candidate = p.path.lstrip("/").split("/")[0]
        return candidate if ID_RE.match(candidate) else None

    if host not in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        return None

    if p.path == "/watch":
        vals = parse_qs(p.query).get("v", [])
        return vals[0] if vals and ID_RE.match(vals[0]) else None

    for prefix in _PATH_PREFIXES:
        if p.path.startswith(prefix):
            candidate = p.path[len(prefix):].split("/")[0]
            return candidate if ID_RE.match(candidate) else None

    return None


def canonical(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def parse_lines(text: str) -> tuple[list[tuple[str, str]], list[tuple[int, str]]]:
    """Parse pasted text or an uploaded .txt.

    Returns (ok, bad) where ok is [(video_id, canonical_url)] and bad is
    [(line_number, raw_line)]. One typo must never abandon the rest of the file.
    """
    ok: list[tuple[str, str]] = []
    bad: list[tuple[int, str]] = []
    seen: set[str] = set()

    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        vid = video_id(line)
        if not vid:
            bad.append((i, line[:80]))
            continue
        if vid in seen:
            continue
        seen.add(vid)
        ok.append((vid, canonical(vid)))

    return ok, bad
