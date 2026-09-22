"""The Run tab must not hold the rest of the app hostage while it polls.

The reviewer works in a different tab, but Streamlit executes every tab's body
in one script run -- so a poll loop at app scope re-created the `st.video`
element every two seconds and made reviewing during a run impossible.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "app.py"


class FakeProc:
    """Stands in for the run_pipeline.py subprocess."""

    def __init__(self, alive: bool = True):
        self._alive = alive
        self.waited = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    def wait(self, timeout=None):
        self.waited = True
        self._alive = False
        return 0


@pytest.fixture
def app(fresh_db):
    return AppTest.from_file(str(APP), default_timeout=30)


def test_a_run_in_progress_does_not_stall_the_whole_app(app):
    app.session_state["proc"] = FakeProc(alive=True)

    start = time.monotonic()
    app.run()
    elapsed = time.monotonic() - start

    # The old code slept 2s at app scope before the Review tab was even
    # reached, then reran, forever. Anything near 2s means it is back.
    assert elapsed < 1.0, f"script run took {elapsed:.2f}s while busy"
    assert not app.exception


def test_settings_unlock_once_the_run_has_finished(app):
    app.session_state["proc"] = FakeProc(alive=False)

    app.run()

    assert not app.checkbox(key="review_enabled").disabled


def test_settings_stay_locked_while_the_run_is_alive(app):
    app.session_state["proc"] = FakeProc(alive=True)

    app.run()

    assert app.checkbox(key="review_enabled").disabled


def test_a_clip_is_reviewable_while_its_video_is_still_processing(app, fresh_db):
    """Clips land in the DB one at a time during encoding, so the reviewer
    should not have to wait for the whole queue to drain."""
    db = fresh_db
    db.enqueue("stillgoing", "https://youtu.be/stillgoing")
    db.set_status("stillgoing", db.PROCESSING, stage="encode")
    db.insert_clip({
        "clip_id": "stillgoing_000000_020000", "youtube_video_id": "stillgoing",
        "seq": 1, "start_ms": 0, "end_ms": 20000, "duration_ms": 20000,
        "confidence": "MEDIUM", "flags": "[]",
        "master_path": "work/stillgoing/clips/c.mp4",
        "delivered_path": "work/stillgoing/delivered/c.mp4",
        "pipeline_version": "test", "config_hash": "test",
    })
    app.session_state["proc"] = FakeProc(alive=True)

    app.run()
    playhead = app.slider(key="play_stillgoing_000000_020000")
    playhead.set_value(7.5).run()

    assert app.slider(key="play_stillgoing_000000_020000").value == 7.5
    assert not app.exception


def test_the_log_keeps_updating_as_the_run_writes_to_it(app, tmp_path,
                                                        monkeypatch):
    """The whole point of polling. A keyed widget would pin the first read
    forever, since Streamlit ignores `value=` once the key exists."""
    logs = tmp_path / "logs"          # app.py reads logs/latest.log from the cwd
    logs.mkdir()
    log = logs / "run.log"
    log.write_text("first line\n", encoding="utf-8")
    (logs / "latest.log").write_text(str(log), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    app.session_state["proc"] = FakeProc(alive=True)
    app.run()
    before = _log_text(app)

    log.write_text("first line\nsecond line\n", encoding="utf-8")
    app.run()

    assert "second line" not in before
    assert "second line" in _log_text(app)


def _log_text(at) -> str:
    return " ".join([e.value for e in at.code] + [e.value for e in at.text_area])


def test_stop_marks_the_in_flight_video_instead_of_stranding_it(app, fresh_db):
    """Stop used to only kill the process. The row it was working on stayed at
    PROCESSING forever: invisible to next_queued, to both retries, and to
    removal."""
    db = fresh_db
    db.enqueue("stoppedvid1", "https://youtu.be/stoppedvid1")
    db.set_status("stoppedvid1", db.PROCESSING, stage="encode")
    db.enqueue("waitingvid1", "https://youtu.be/waitingvid1")
    proc = FakeProc(alive=True)
    app.session_state["proc"] = proc

    app.run()
    app.button(key="stop_go").click().run()

    assert not app.exception, [e.value for e in app.exception]
    assert db.get_video("stoppedvid1")["status"] == db.STOPPED
    assert proc.waited, "must wait for the runner to die before writing status"
    # A video that never started is still simply queued.
    assert db.get_video("waitingvid1")["status"] == db.QUEUED
