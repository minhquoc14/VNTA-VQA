"""Shared fixtures. Every test gets its own pipeline.db in a tmp dir."""

from __future__ import annotations

import pytest

from vqa import db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point vqa.db at an empty database for the duration of one test."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pipeline.db")
    db.init()
    return db
