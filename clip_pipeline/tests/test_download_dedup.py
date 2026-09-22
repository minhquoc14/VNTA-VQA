"""Regression locks: a video already on disk is never fetched again.

These do not drive new code. They pin behaviour the pipeline already has, so a
later change to the download stage cannot quietly start re-fetching gigabytes.
"""

from __future__ import annotations

import json

import pytest
import yt_dlp

from vqa.stages import download


@pytest.fixture
def no_network(monkeypatch):
    """Any real fetch attempt becomes a loud failure. Returns the call log."""
    calls: list[str] = []

    class Tripwire:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download=True):
            calls.append(url)
            raise AssertionError(f"yt-dlp was called for {url}")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Tripwire)
    return calls


def _downloaded(work, *, size=4096, meta=True):
    work.mkdir(parents=True, exist_ok=True)
    (work / "source.mp4").write_bytes(b"\0" * size)
    if meta:
        (work / "metadata.json").write_text(json.dumps(
            {"title": "t", "channel_id": "c", "channel_name": "n",
             "duration_s": 600.0, "webpage_url": "u"}), encoding="utf-8")


def test_a_downloaded_video_is_never_fetched_again(tmp_path, no_network):
    work = tmp_path / "vid1"
    _downloaded(work)

    meta = download.download("https://youtu.be/vid1", work, 1080, {})

    assert no_network == []
    assert meta["channel_id"] == "c"


def test_an_unusable_file_is_fetched_again(tmp_path, no_network):
    """Resume must not mean 'trust anything on disk'."""
    work = tmp_path / "vid1"
    _downloaded(work, size=500)          # truncated: yt-dlp died mid-download

    with pytest.raises(AssertionError):
        download.download("https://youtu.be/vid1", work, 1080, {})


def test_a_duplicate_url_never_reaches_the_download_stage(fresh_db):
    """The first line of defence: it never gets queued a second time."""
    db = fresh_db
    assert db.enqueue("vid1", "https://youtu.be/vid1") is True
    assert db.enqueue("vid1", "https://youtu.be/vid1?t=42s") is False


def test_a_finished_video_is_never_picked_up_again(fresh_db):
    """The second: the runner only ever pulls QUEUED rows."""
    db = fresh_db
    db.enqueue("vid1", "https://youtu.be/vid1")
    db.set_status("vid1", db.READY_FOR_REVIEW)

    assert db.next_queued() is None
