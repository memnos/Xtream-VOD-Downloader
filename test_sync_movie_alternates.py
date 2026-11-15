"""Tests for sync alternate probing of new movie versions."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

import requests

import discarded_movie_streams as dms
from strm_sync import (
    _sync_movie_version_group,
    settle_unavailable_movie,
    stream_url_explicitly_rejected,
)


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
        ), patch("strm_sync.probe_stream_media_info") as probe_mock, patch(
            "strm_sync.stream_url_explicitly_rejected"
        ) as rejected_mock:
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
        rejected_mock.assert_not_called()
        self.assertIn("99.mp4", open(strm_path).read())

    def test_daily_sync_does_not_open_existing_urls(self):
        strm_path = self._write_strm(
            "Heart of the Beast 1080p",
            "http://h/movie/u/p/519632.mkv",
        )
        with patch(
            "strm_sync._resolve_movie_paths", return_value=(strm_path, "")
        ), patch(
            "strm_sync.stream_url_explicitly_rejected"
        ) as rejected_mock, patch(
            "strm_sync.probe_stream_media_info"
        ) as probe_mock, patch(
            "strm_sync.local_download_exists_for_strm", return_value=False
        ), patch(
            "strm_sync.load_auto_download_config",
            return_value={"stream_proxy_enabled": False, "stream_proxy_download": False},
        ):
            result, _path = _sync_movie_version_group(
                self._versions("519632"),
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=True,
                verify_existing_urls=False,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=dms.load_discarded_streams(),
            )
        self.assertEqual(result, "skipped")
        rejected_mock.assert_not_called()
        probe_mock.assert_not_called()
        with open(strm_path, encoding="utf-8") as handle:
            self.assertIn("519632.mkv", handle.read())

    def _versions(self, first_id: str, second_id: str | None = None):
        versions = [
            {
                "stream_id": first_id,
                "name": "Heart of the Beast 1080p",
                "container_extension": "mkv",
            }
        ]
        if second_id:
            versions.append(
                {
                    "stream_id": second_id,
                    "name": "Heart of the Beast 720p",
                    "container_extension": "mkv",
                }
            )
        return versions

    def _write_strm(self, folder_name: str, url: str) -> str:
        folder = os.path.join(self.root, folder_name)
        os.makedirs(folder, exist_ok=True)
        strm_path = os.path.join(folder, f"{folder_name}.strm")
        with open(strm_path, "w", encoding="utf-8") as handle:
            handle.write(url + "\n")
        return strm_path

    def test_dead_current_url_is_replaced_by_working_version(self):
        strm_path = self._write_strm(
            "Heart of the Beast 1080p",
            "http://h/movie/u/p/519632.mkv",
        )
        store = dms.load_discarded_streams()

        def rejected(url, **_kwargs):
            return "/519632." in url

        def probe(url, timeout=45):
            if "/519632." in url:
                raise AssertionError(url)
            return {"duration": 5400.0}

        with patch(
            "strm_sync._resolve_movie_paths", return_value=(strm_path, "")
        ), patch(
            "strm_sync.stream_url_explicitly_rejected", side_effect=rejected
        ), patch(
            "strm_sync.probe_stream_media_info", side_effect=probe
        ), patch("strm_sync.local_download_exists_for_strm", return_value=False), patch(
            "strm_sync.load_auto_download_config",
            return_value={"stream_proxy_enabled": False, "stream_proxy_download": False},
        ):
            result, path = _sync_movie_version_group(
                self._versions("519632", "600001"),
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=True,
                verify_existing_urls=True,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=store,
            )
        self.assertEqual(result, "updated")
        self.assertEqual(path, strm_path)
        with open(strm_path, encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("600001.mkv", body)
        self.assertNotIn("519632.mkv", body)
        self.assertIn("519632", store.get("streams") or {})

    def test_healthy_same_url_is_not_probed_or_rewritten(self):
        strm_path = self._write_strm(
            "Heart of the Beast 1080p",
            "http://h/movie/u/p/519632.mkv",
        )
        with patch(
            "strm_sync._resolve_movie_paths", return_value=(strm_path, "")
        ), patch(
            "strm_sync.stream_url_explicitly_rejected", return_value=False
        ) as rejected_mock, patch(
            "strm_sync.probe_stream_media_info"
        ) as probe_mock, patch(
            "strm_sync.local_download_exists_for_strm", return_value=False
        ), patch(
            "strm_sync.load_auto_download_config",
            return_value={"stream_proxy_enabled": False, "stream_proxy_download": False},
        ):
            result, _path = _sync_movie_version_group(
                self._versions("519632"),
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=True,
                verify_existing_urls=True,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=dms.load_discarded_streams(),
            )
        self.assertEqual(result, "skipped")
        rejected_mock.assert_called_once()
        probe_mock.assert_not_called()
        with open(strm_path, encoding="utf-8") as handle:
            self.assertIn("519632.mkv", handle.read())

    def test_dead_url_with_no_working_version_stays_until_caller_removes_it(self):
        strm_path = self._write_strm(
            "Heart of the Beast 1080p",
            "http://h/movie/u/p/519632.mkv",
        )
        with patch(
            "strm_sync._resolve_movie_paths", return_value=(strm_path, "")
        ), patch(
            "strm_sync.stream_url_explicitly_rejected", return_value=True
        ), patch("strm_sync.probe_stream_media_info") as probe_mock, patch(
            "strm_sync.local_download_exists_for_strm", return_value=False
        ):
            result, path = _sync_movie_version_group(
                self._versions("519632"),
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=True,
                verify_existing_urls=True,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=dms.load_discarded_streams(),
            )
        self.assertEqual(result, "unavailable")
        self.assertEqual(path, strm_path)
        probe_mock.assert_not_called()
        self.assertTrue(os.path.isfile(strm_path))
        self.assertEqual(
            settle_unavailable_movie(strm_path, remove_missing=False), "kept"
        )
        self.assertTrue(os.path.isfile(strm_path))
        nfo_path = os.path.splitext(strm_path)[0] + ".nfo"
        with open(nfo_path, "w", encoding="utf-8") as handle:
            handle.write("<movie/>")
        self.assertEqual(
            settle_unavailable_movie(strm_path, remove_missing=True), "removed"
        )
        self.assertFalse(os.path.isfile(strm_path))
        self.assertFalse(os.path.isfile(nfo_path))

    def test_proxy_strm_follows_registry_stream_and_skips_registering_a_403(self):
        proxy_url = "http://tv.lan:8510/p/movie/abc"
        strm_path = self._write_strm("Heart of the Beast 1080p", proxy_url)
        registered = []

        def resolve(**kwargs):
            registered.append(kwargs["remote_url"])
            return proxy_url

        def rejected(url, **_kwargs):
            return "/519632." in url

        with patch(
            "strm_sync._resolve_movie_paths", return_value=(strm_path, "")
        ), patch(
            "strm_sync.get_movie_proxy_entry",
            return_value={"remote_url": "http://h/movie/u/p/519632.mkv"},
        ), patch(
            "strm_sync.stream_url_explicitly_rejected", side_effect=rejected
        ), patch(
            "strm_sync.probe_stream_media_info",
            return_value={"duration": 100.0},
        ), patch(
            "strm_sync.resolve_movie_play_url", side_effect=resolve
        ), patch(
            "strm_sync.load_auto_download_config",
            return_value={
                "stream_proxy_enabled": True,
                "stream_proxy_host": "tv.lan",
                "stream_proxy_port": 8510,
                "stream_proxy_download": False,
            },
        ), patch("strm_sync.local_download_exists_for_strm", return_value=False):
            result, path = _sync_movie_version_group(
                self._versions("519632", "600001"),
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=True,
                verify_existing_urls=True,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=dms.load_discarded_streams(),
            )
        self.assertEqual(result, "updated")
        self.assertEqual(path, strm_path)
        self.assertEqual(registered, ["http://h/movie/u/p/600001.mkv"])
        with open(strm_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), proxy_url)

    def test_proxy_strm_without_registry_is_unavailable_when_catalog_url_is_403(self):
        proxy_url = "http://tv.lan:8510/p/movie/abc"
        strm_path = self._write_strm("Heart of the Beast 1080p", proxy_url)
        with patch(
            "strm_sync._resolve_movie_paths", return_value=(strm_path, "")
        ), patch(
            "strm_sync.get_movie_proxy_entry", return_value=None
        ), patch(
            "strm_sync.stream_url_explicitly_rejected", return_value=True
        ), patch(
            "strm_sync.resolve_movie_play_url"
        ) as resolve_mock, patch(
            "strm_sync.load_auto_download_config",
            return_value={
                "stream_proxy_enabled": True,
                "stream_proxy_host": "tv.lan",
                "stream_proxy_port": 8510,
                "stream_proxy_download": False,
            },
        ), patch("strm_sync.local_download_exists_for_strm", return_value=False):
            result, path = _sync_movie_version_group(
                self._versions("519632"),
                "http://h",
                "u",
                "p",
                self.root,
                update_existing=True,
                verify_existing_urls=True,
                tmdb_client=None,
                config={},
                allow_4k=True,
                discarded_store=dms.load_discarded_streams(),
            )
        self.assertEqual(result, "unavailable")
        self.assertEqual(path, strm_path)
        resolve_mock.assert_not_called()
        self.assertTrue(os.path.isfile(strm_path))


class StreamRejectionTests(unittest.TestCase):
    def test_only_explicit_client_errors_count(self):
        seen = {}

        class Response:
            def __init__(self, code):
                self.status_code = code

            def close(self):
                seen["closed"] = True

        def fake_get(url, **kwargs):
            seen["headers"] = kwargs["headers"]
            seen["stream"] = kwargs["stream"]
            if url.endswith("/down.mkv"):
                raise requests.ConnectionError("panel down")
            code = int(url.rsplit("/", 1)[-1].split(".", 1)[0])
            return Response(code)

        with patch("strm_sync.requests.get", side_effect=fake_get):
            self.assertTrue(stream_url_explicitly_rejected("http://h/401.mkv"))
            self.assertTrue(stream_url_explicitly_rejected("http://h/403.mkv"))
            self.assertTrue(seen.get("closed"))
            self.assertTrue(stream_url_explicitly_rejected("http://h/404.mkv"))
            self.assertTrue(stream_url_explicitly_rejected("http://h/410.mkv"))
            self.assertFalse(stream_url_explicitly_rejected("http://h/200.mkv"))
            self.assertFalse(stream_url_explicitly_rejected("http://h/500.mkv"))
            self.assertFalse(stream_url_explicitly_rejected("http://h/down.mkv"))
        self.assertEqual(seen["headers"]["User-Agent"], "Xtream-VOD-Downloader/1.0")
        self.assertEqual(seen["headers"]["Accept"], "*/*")
        self.assertNotIn("Sec-Fetch-Mode", seen["headers"])
        self.assertTrue(seen["stream"])


if __name__ == "__main__":
    unittest.main()
