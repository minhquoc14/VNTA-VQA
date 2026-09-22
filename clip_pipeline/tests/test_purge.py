"""Removing a video must not leave its finished product on disk.

Clips are reviewable while their video is still encoding, so a video the user
stops can already own APPROVED files in `trimmed/`. Deleting only the database
rows would strand those .mp4s: nothing lists them, nothing exports them, and
nothing will ever clean them up.
"""

from __future__ import annotations

from vqa import db, review


def _approved_clip(tmp_path, video_id="stoppedvid1"):
    """A video stopped mid-encode whose first clip was already approved."""
    db.enqueue(video_id, f"https://youtu.be/{video_id}")
    db.set_status(video_id, db.STOPPED, stage="encode")
    clip_id = f"{video_id}_000000_009000"
    db.insert_clip({
        "clip_id": clip_id, "youtube_video_id": video_id, "seq": 1,
        "start_ms": 0, "end_ms": 9000, "duration_ms": 9000,
        "confidence": "MEDIUM", "flags": "[]",
        "master_path": str(tmp_path / "clips" / "c.mp4"),
        "delivered_path": str(tmp_path / "delivered" / "c.mp4"),
        "pipeline_version": "v", "config_hash": "h"})

    trimmed = tmp_path / "trimmed"
    trimmed.mkdir(parents=True)
    out = trimmed / f"{clip_id}_t01.mp4"
    out.write_bytes(b"approved output")
    db.set_trim_segments(clip_id, [{"start_ms": 1000, "end_ms": 6000,
                                    "path": str(out)}])
    db.set_decision(clip_id, db.APPROVED)
    return video_id, out


def test_purging_a_video_deletes_what_the_reviewer_already_produced(
        fresh_db, tmp_path):
    video_id, approved_file = _approved_clip(tmp_path)

    rows, files = review.purge_video(video_id)

    assert (rows, files) == (1, 1)
    assert not approved_file.exists()
    assert db.get_video(video_id) is None
    assert db.list_clips(video_id=video_id) == []


def test_purging_refuses_a_video_that_is_still_being_reviewed(
        fresh_db, tmp_path):
    """A refusal must not delete the files on its way out."""
    video_id, approved_file = _approved_clip(tmp_path)
    db.set_status(video_id, db.READY_FOR_REVIEW)

    assert review.purge_video(video_id) == (0, 0)
    assert approved_file.exists()
    assert db.get_video(video_id) is not None
