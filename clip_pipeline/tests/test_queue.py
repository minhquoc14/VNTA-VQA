"""Queue management: adding URLs, and taking them back out again."""

from __future__ import annotations


def test_queued_video_can_be_removed(fresh_db):
    db = fresh_db
    db.enqueue("vid1", "https://youtu.be/vid1")

    assert db.remove_video("vid1") == 1
    assert db.get_video("vid1") is None


def test_reviewed_video_is_not_removable(fresh_db):
    """A processed video owns clips, and `clips` has no FK back to `videos`."""
    db = fresh_db
    db.enqueue("vid1", "https://youtu.be/vid1")
    db.set_status("vid1", db.READY_FOR_REVIEW)

    assert db.remove_video("vid1") == 0
    assert db.get_video("vid1") is not None


def test_failed_download_is_removable_then_re_addable(fresh_db):
    """The whole point: drop a broken URL and paste it again to retry."""
    db = fresh_db
    db.enqueue("vid1", "https://youtu.be/vid1")
    db.set_status("vid1", db.DOWNLOAD_FAILED, error_code="DOWNLOAD_FAILED")

    assert db.enqueue("vid1", "https://youtu.be/vid1") is False   # blocked before
    assert db.remove_video("vid1") == 1
    assert db.enqueue("vid1", "https://youtu.be/vid1") is True    # free after
    assert db.get_video("vid1")["status"] == db.QUEUED


def test_in_flight_video_is_not_removable(fresh_db):
    """The runner is another process; it may claim a row between render and click."""
    db = fresh_db
    db.enqueue("vid1", "https://youtu.be/vid1")
    for status in (db.DOWNLOADING, db.PROCESSING, db.DONE):
        db.set_status("vid1", status)
        assert db.remove_video("vid1") == 0, status


def test_removing_twice_is_harmless(fresh_db):
    db = fresh_db
    db.enqueue("vid1", "https://youtu.be/vid1")

    assert db.remove_video("vid1") == 1
    assert db.remove_video("vid1") == 0


def test_enqueue_many_names_the_duplicates_and_their_state(fresh_db):
    """"duplicate: 2" tells the reviewer nothing; which ones, and how they went,
    is what decides whether anything needs doing."""
    db = fresh_db
    db.enqueue("old_ok", "https://youtu.be/old_ok")
    db.set_status("old_ok", db.READY_FOR_REVIEW)
    db.enqueue("old_bad", "https://youtu.be/old_bad")
    db.set_status("old_bad", db.DOWNLOAD_FAILED)

    added, dupes = db.enqueue_many([
        ("new", "https://youtu.be/new"),
        ("old_ok", "https://youtu.be/old_ok"),
        ("old_bad", "https://youtu.be/old_bad"),
    ])

    assert added == ["new"]
    assert dupes == [("old_ok", db.READY_FOR_REVIEW),
                     ("old_bad", db.DOWNLOAD_FAILED)]


def test_a_stopped_video_can_be_removed_and_can_be_retried(fresh_db):
    """Stop used to strand a row: no retry saw it, no remove touched it."""
    db = fresh_db
    db.enqueue("stoppedvid1", "https://youtu.be/stoppedvid1")
    db.set_status("stoppedvid1", db.STOPPED, stage="encode")

    assert db.retry_status(db.STOPPED) == 1
    assert db.get_video("stoppedvid1")["status"] == db.QUEUED

    db.set_status("stoppedvid1", db.STOPPED)
    assert db.remove_video("stoppedvid1") == 1


def _half_encoded(db, video_id="stoppedvid1"):
    """A video Stop caught mid-encode: some clips already in the DB."""
    db.enqueue(video_id, f"https://youtu.be/{video_id}")
    db.set_status(video_id, db.STOPPED, stage="encode")
    for i in range(2):
        db.insert_clip({
            "clip_id": f"{video_id}_{i}", "youtube_video_id": video_id, "seq": i,
            "start_ms": 0, "end_ms": 9000, "duration_ms": 9000,
            "confidence": "MEDIUM", "flags": "[]", "master_path": "m",
            "delivered_path": "d", "pipeline_version": "v", "config_hash": "h"})
    return video_id


def test_removing_a_stopped_video_takes_its_half_encoded_clips_with_it(fresh_db):
    """`clips` has no FK to `videos`; leftovers would haunt the review queue."""
    db = fresh_db
    vid = _half_encoded(db)
    assert len(db.list_clips(video_id=vid)) == 2

    assert db.remove_video(vid) == 1

    assert db.list_clips(video_id=vid) == []
    assert db.clip_counts()["by_decision"] == {}
