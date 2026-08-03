"""Tests for discarded movie streams store and sync skip logic."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import discarded_movie_streams as dms
from core import dedupe_catalog_by_quality


class DiscardedStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmpdir.name, "discarded.json")

        def _save(path, payload):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

        def _load(path, default):
            if not os.path.isfile(path):
                return default
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)

        self._patchers = [
            patch.object(dms, "DISCARDED_MOVIE_STREAMS_FILE", self.path),
            patch.object(dms, "_save_json_file", _save),
            patch.object(dms, "load_json_file", _load),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        self._tmpdir.cleanup()

    def test_mark_and_block_same_fingerprint(self):
        dms.mark_movie_stream_discarded(
            stream_id="100",
            ext="mp4",
            size=111,
            name="Foo",
            reason="probe_failed",
        )
        item = {"stream_id": "100", "container_extension": "mp4", "size": 111}
        self.assertTrue(dms.is_movie_stream_discarded(item))

    def test_changed_size_unblocks(self):
        dms.mark_movie_stream_discarded(
            stream_id="100",
            ext="mp4",
            size=111,
            name="Foo",
            reason="probe_failed",
        )
        changed = {"stream_id": "100", "container_extension": "mp4", "size": 999}
        self.assertFalse(dms.is_movie_stream_discarded(changed))
        self.assertFalse(dms.is_movie_stream_discarded(changed))

    def test_dedupe_skips_discarded_picks_next(self):
        dms.mark_movie_stream_discarded(
            stream_id="1",
            ext="mp4",
            size=0,
            name="Film 1080p",
            reason="probe_failed",
        )
        items = [
            {"stream_id": "1", "name": "Film (2020) 1080p", "container_extension": "mp4"},
            {"stream_id": "2", "name": "Film (2020) 720p", "container_extension": "mp4"},
        ]
        store = dms.load_discarded_streams()
        result, total = dedupe_catalog_by_quality(
            items,
            allow_4k=True,
            skip_item=dms.discarded_skip_predicate(store),
        )
        self.assertEqual(total, 2)
        self.assertEqual(len(result), 1)
        self.assertEqual(str(result[0]["stream_id"]), "2")

    def test_all_discarded_yields_empty(self):
        dms.mark_movie_stream_discarded(stream_id="1", ext="mp4", size=0, name="A")
        dms.mark_movie_stream_discarded(stream_id="2", ext="mp4", size=0, name="A")
        items = [
            {"stream_id": "1", "name": "Film (2020)", "container_extension": "mp4"},
            {"stream_id": "2", "name": "Film (2020) 720p", "container_extension": "mp4"},
        ]
        store = dms.load_discarded_streams()
        result, _ = dedupe_catalog_by_quality(
            items,
            allow_4k=True,
            skip_item=dms.discarded_skip_predicate(store),
        )
        self.assertEqual(result, [])

    def test_set_replaced_by(self):
        dms.mark_movie_stream_discarded(stream_id="9", ext="mkv", size=5, name="X")
        self.assertTrue(dms.set_discarded_replaced_by("9", "10"))
        entry = dms.load_discarded_streams()["streams"]["9"]
        self.assertEqual(entry["replaced_by"], "10")


if __name__ == "__main__":
    unittest.main()
