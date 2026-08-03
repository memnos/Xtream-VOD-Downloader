"""Tests for sync alternate probing of new movie versions."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import discarded_movie_streams as dms
from strm_sync import _sync_movie_version_group


class SyncAlternateProbeTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = self._tmpdir.name
        self.discard_path = os.path.join(self.root, "discarded.json")
        def _save(path, payload):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(__import__("json").dumps(payload))

        def _load(path, default):
            if not os.path.isfile(path):
                return default
            with open(path, encoding="utf-8") as handle:
                return __import__("json").load(handle)

        self._patchers = [
            patch.object(dms, "DISCARDED_MOVIE_STREAMS_FILE", self.discard_path),
            patch.object(dms, "_save_json_file", _save),
            patch.object(dms, "load_json_file", _load),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        self._tmpdir.cleanup()

    def test_new_version_probed_and_created_after_discarded(self):
        dms.mark_movie_stream_discarded(
            stream_id="1", ext="mp4", size=0, name="Film 1080p", reason="probe_failed"
        )
        store = dms.load_discarded_streams()
        versions = [
            {
                "stream_id": "1",
                "name": "Film (2020) 1080p",
                "container_extension": "mp4",
            },
            {
                "stream_id": "2",
                "name": "Film (2020) 720p",
                "container_extension": "mp4",
            },
        ]
        strm_path = os.path.join(self.root, "Film (2020)", "Film (2020).strm")

        def resolve(item, *_a, **_k):
            return strm_path, ""

        with patch("strm_sync._resolve_movie_paths", side_effect=resolve), patch(
            "strm_sync.probe_stream_media_info",
            return_value={"duration": 100.0},
        ), patch("strm_sync.local_download_exists_for_strm", return_value=False):
            result, path = _sync_movie_version_group(
                versions,
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=False,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=store,
            )
        self.assertEqual(result, "created")
        self.assertEqual(path, strm_path)
        self.assertTrue(os.path.isfile(strm_path))
        self.assertIn("2.mp4", open(strm_path).read())

    def test_new_version_probe_fail_tries_next(self):
        versions = [
            {
                "stream_id": "10",
                "name": "Film (2021) BluRay 1080p",
                "container_extension": "mp4",
            },
            {
                "stream_id": "11",
                "name": "Film (2021) HDTV 720p",
                "container_extension": "mp4",
            },
        ]
        strm_path = os.path.join(self.root, "Film (2021)", "Film (2021).strm")
        store = dms.load_discarded_streams()

        def resolve(item, *_a, **_k):
            return strm_path, ""

        def probe(url, timeout=45):
            if "/10." in url:
                return None
            return {"duration": 90.0}

        with patch("strm_sync._resolve_movie_paths", side_effect=resolve), patch(
            "strm_sync.probe_stream_media_info", side_effect=probe
        ), patch("strm_sync.local_download_exists_for_strm", return_value=False):
            result, path = _sync_movie_version_group(
                versions,
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=False,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=store,
            )
        self.assertEqual(result, "created")
        with open(strm_path, encoding="utf-8") as handle:
            self.assertIn("11.mp4", handle.read())
        self.assertIn("10", store.get("streams") or {})
        self.assertTrue(
            dms.is_movie_stream_discarded(
                {"stream_id": "10", "container_extension": "mp4", "size": 0},
                store=store,
            )
        )

    def test_existing_strm_not_replaced_without_update(self):
        versions = [
            {
                "stream_id": "20",
                "name": "Film (2022)",
                "container_extension": "mp4",
            },
        ]
        folder = os.path.join(self.root, "Film (2022)")
        os.makedirs(folder)
        strm_path = os.path.join(folder, "Film (2022).strm")
        with open(strm_path, "w") as handle:
            handle.write("http://h/movie/u/p/99.mp4\n")

        with patch(
            "strm_sync._resolve_movie_paths", return_value=(strm_path, "")
        ), patch("strm_sync.probe_stream_media_info") as probe_mock:
            result, path = _sync_movie_version_group(
                versions,
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=False,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=dms.load_discarded_streams(),
            )
        self.assertEqual(result, "skipped")
        probe_mock.assert_not_called()
        self.assertIn("99.mp4", open(strm_path).read())


if __name__ == "__main__":
    unittest.main()
