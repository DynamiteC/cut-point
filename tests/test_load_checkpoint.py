"""A checkpoint that survives a regenerated events file makes the loader skip
the head of the new file and report success. That is silent data loss.
"""

from __future__ import annotations

import json

import pytest

from ingest import load as load_mod


@pytest.fixture
def events_file(tmp_path, monkeypatch):
    monkeypatch.setattr(load_mod, "DATA_DIR", tmp_path)
    (tmp_path / "events").mkdir()
    path = tmp_path / "events" / "demo_001.ndjson"
    path.write_text('{"a": 1}\n{"a": 2}\n{"a": 3}\n')
    return path


def test_checkpoint_is_honoured_when_the_file_is_unchanged(events_file) -> None:
    # Arrange
    load_mod.save_checkpoint("demo_001", 2, events_file)

    # Act / Assert
    assert load_mod.load_checkpoint("demo_001", events_file) == 2


def test_checkpoint_is_discarded_when_the_events_file_was_regenerated(events_file) -> None:
    # Arrange: a checkpoint from the previous generation of this file
    load_mod.save_checkpoint("demo_001", 2, events_file)

    # Act: regenerate with different content
    events_file.write_text('{"a": 9}\n{"a": 8}\n{"a": 7}\n{"a": 6}\n')

    # Assert: skipping 2 lines here would silently drop the new file's head
    assert load_mod.load_checkpoint("demo_001", events_file) == 0
    assert not load_mod.checkpoint_path("demo_001").exists()


def test_a_legacy_checkpoint_without_source_identity_is_discarded(events_file) -> None:
    # Arrange: the old on-disk format, which carried no file identity at all
    load_mod.checkpoint_path("demo_001").write_text(json.dumps({"rows_loaded": 2}))

    # Assert
    assert load_mod.load_checkpoint("demo_001", events_file) == 0
