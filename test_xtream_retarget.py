"""Tests for Xtream host/credential retarget without a full library sync."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from unittest import mock

import stream_proxy
import xtream_retarget
from core import MOVIE_STRM_URL_SIDECAR, read_strm_url, write_strm


class XtreamRetargetUrlTest(unittest.TestCase):
    def test_retarget_movie_and_series_keep_stream_id(self):
        movie = xtream_retarget.retarget_xtream_media_url(
            "http://old.example:8080/movie/u/p/519632.mkv",
            "http://new.example:24000",
            "NEWUSER",
            "NEWPASS",
        )
        self.assertEqual(
            movie, "http://new.example:24000/movie/NEWUSER/NEWPASS/519632.mkv"
        )
        series = xtream_retarget.retarget_xtream_media_url(
            "http://old.example:8080/series/u/p/42.mp4",
            "http://new.example:24000",
            "NEWUSER",
            "NEWPASS",
        )
        self.assertEqual(
            series, "http://new.example:24000/series/NEWUSER/NEWPASS/42.mp4"
        )

    def test_retarget_skips_proxy_and_unchanged(self):
        self.assertIsNone(
            xtream_retarget.retarget_xtream_media_url(
                "http://tv.lan:8510/p/movie/abc",
                "http://new.example:24000",
                "u",
                "p",
            )
        )
        same = "http://h/movie/u/p/1.mkv"
        self.assertIsNone(
            xtream_retarget.retarget_xtream_media_url(same, "http://h", "u", "p")
        )

    def test_only_from_host_filter(self):
        url = "http://keep.example/movie/u/p/9.mkv"
        self.assertIsNone(
            xtream_retarget.retarget_xtream_media_url(
                url,
                "http://new.example",
                "a",
                "b",
                only_from_host="http://other.example",
            )
        )
        self.assertEqual(
            xtream_retarget.retarget_xtream_media_url(
                url,
                "http://new.example",
                "a",
                "b",
                only_from_host="keep.example",
            ),
            "http://new.example/movie/a/b/9.mkv",
        )


class XtreamRetargetLibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.movies = os.path.join(self.tmp.name, "movies")
        self.series = os.path.join(self.tmp.name, "series")
        self.downloads = os.path.join(self.tmp.name, "downloads")
        os.makedirs(self.movies)
        os.makedirs(self.series)
        os.makedirs(self.downloads)
        self.registry = os.path.join(self.tmp.name, "proxy_registry.json")
        self.creds = os.path.join(self.tmp.name, "creds.json")
        self._reg_env = mock.patch.dict(
            os.environ,
            {
                "PROXY_REGISTRY_FILE": self.registry,
                "CREDENTIALS_FILE": self.creds,
                "DATA_DIR": self.tmp.name,
            },
        )
        self._reg_env.start()
        # Reload module-level paths that read env at import time.
        stream_proxy.PROXY_REGISTRY_FILE = self.registry
        stream_proxy._REGISTRY_CACHE = None
        stream_proxy._REGISTRY_DIRTY = 0
        import core

        core.CREDENTIALS_FILE = self.creds
        core.DATA_DIR = self.tmp.name

    def tearDown(self):
        stream_proxy._REGISTRY_CACHE = None
        stream_proxy._REGISTRY_DIRTY = 0
        self._reg_env.stop()
        self.tmp.cleanup()

    def _write(self, path: str, url: str) -> str:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_strm(path, url)
        return path

    def test_library_retarget_preserves_mtime_and_proxy_text(self):
        direct = self._write(
            os.path.join(self.movies, "Film (2020)", "Film (2020).strm"),
            "http://old.example/movie/oldu/oldp/100.mkv",
        )
        proxy_path = self._write(
            os.path.join(self.movies, "Proxy Film", "Proxy Film.strm"),
            "http://tv.lan:8510/p/movie/proxyfilm",
        )
        ep = self._write(
            os.path.join(
                self.series,
                "Show (2020)",
                "Season 01",
                "Show (2020) - S01E01.strm",
            ),
            "http://old.example/series/oldu/oldp/55.mp4",
        )
        # Age the direct file so a rewrite without preserve_mtime would bump mtime.
        old_mtime = time.time() - 3600
        os.utime(direct, (old_mtime, old_mtime))
        before = os.stat(direct).st_mtime

        sidecar_dir = os.path.join(self.downloads, "Film (2020)")
        os.makedirs(sidecar_dir)
        with open(
            os.path.join(sidecar_dir, MOVIE_STRM_URL_SIDECAR),
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                {"url": "http://old.example/movie/oldu/oldp/100.mkv", "strm_path": direct},
                handle,
            )

        stream_proxy.register_movie_proxy(
            folder_name="Proxy Film",
            remote_url="http://old.example/movie/oldu/oldp/200.mkv",
            local_path=os.path.join(self.downloads, "Proxy Film", "x.mkv"),
            strm_path=proxy_path,
            write_sidecar=False,
            save=True,
        )
        stream_proxy.flush_proxy_registry()

        with mock.patch.object(
            xtream_retarget,
            "verify_xtream_credentials",
            return_value=(True, "Active"),
        ):
            result = xtream_retarget.retarget_xtream_library(
                "http://new.example:24000",
                "NEW",
                "PASS",
                movies_root=self.movies,
                series_root=self.series,
                downloads_movies=self.downloads,
                verify=True,
                save_creds=True,
                preserve_mtime=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["strm_updated"], 2)
        self.assertEqual(result["strm_proxy"], 1)
        self.assertEqual(result["registry_movies"], 1)
        self.assertEqual(result["sidecar_updated"], 1)
        self.assertEqual(
            read_strm_url(direct),
            "http://new.example:24000/movie/NEW/PASS/100.mkv",
        )
        self.assertEqual(
            read_strm_url(proxy_path),
            "http://tv.lan:8510/p/movie/proxyfilm",
        )
        self.assertEqual(
            read_strm_url(ep),
            "http://new.example:24000/series/NEW/PASS/55.mp4",
        )
        self.assertAlmostEqual(os.stat(direct).st_mtime, before, delta=1.5)
        entry = stream_proxy.get_movie_proxy_entry(
            stream_proxy.movie_proxy_key("Proxy Film")
        )
        self.assertEqual(
            entry["remote_url"],
            "http://new.example:24000/movie/NEW/PASS/200.mkv",
        )
        with open(self.creds, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(saved["host"], "http://new.example:24000")
        self.assertEqual(saved["user"], "NEW")

    def test_auth_failure_aborts_without_rewrites(self):
        path = self._write(
            os.path.join(self.movies, "A", "A.strm"),
            "http://old.example/movie/u/p/1.mkv",
        )
        with mock.patch.object(
            xtream_retarget,
            "verify_xtream_credentials",
            return_value=(False, "Disabled"),
        ):
            result = xtream_retarget.retarget_xtream_library(
                "http://new.example",
                "u",
                "p",
                movies_root=self.movies,
                series_root=self.series,
                downloads_movies=self.downloads,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(read_strm_url(path), "http://old.example/movie/u/p/1.mkv")
        self.assertFalse(os.path.isfile(self.creds))


class XtreamRetargetLiveAuthTest(unittest.TestCase):
    """Optional live check when XTREAM_HOST / USER / PASSWORD are set."""

    def test_env_credentials_authenticate(self):
        host = os.environ.get("XTREAM_HOST", "").strip()
        user = os.environ.get("XTREAM_USER", "").strip()
        password = os.environ.get("XTREAM_PASSWORD", "").strip()
        if not (host and user and password):
            self.skipTest("XTREAM_HOST/USER/PASSWORD not set")
        ok, detail = xtream_retarget.verify_xtream_credentials(
            host, user, password, timeout=15
        )
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
