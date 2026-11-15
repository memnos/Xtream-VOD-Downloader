import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import core
from core import clear_folder_match_cache

import library_strm_watch
from library_strm_watch import (
    cleanup_superseded_strm,
    relative_library_dir,
    silo_scan_path,
    tick_library_strm_watch,
)


class LibraryStrmWatchTest(unittest.TestCase):
    def tearDown(self):
        clear_folder_match_cache()

    def test_silo_scan_path_uses_configured_library_root(self):
        libraries = [
            {"id": 1, "type": "movies", "enabled": True, "paths": ["/mnt/union/Movies"]},
            {"id": 2, "type": "series", "enabled": True, "paths": ["/mnt/union/Serie_Tv"]},
        ]
        self.assertEqual(
            silo_scan_path("movies", "Heart (2026) [tmdbid-1]", libraries),
            "/mnt/union/Movies/Heart (2026) [tmdbid-1]",
        )
        self.assertEqual(
            silo_scan_path("series", "Show (2022)/Season 01", libraries),
            "/mnt/union/Serie_Tv/Show (2022)/Season 01",
        )
        self.assertIsNone(silo_scan_path("movies", "", libraries))

    def test_settled_mediarium_movie_removes_strm(self):
        with tempfile.TemporaryDirectory() as tmp:
            movies = os.path.join(tmp, "movies")
            strm_root = os.path.join(tmp, "strm")
            data = os.path.join(tmp, "data")
            title = "Heart of the Beast (2026) [tmdbid-1263337]"
            folder = os.path.join(movies, title)
            strm_dir = os.path.join(strm_root, title)
            os.makedirs(folder)
            os.makedirs(strm_dir)
            os.makedirs(data)
            local = os.path.join(folder, f"{title}.mkv")
            strm = os.path.join(strm_dir, f"{title}.strm")
            with open(local, "wb") as handle:
                handle.write(b"video-bytes")
            with open(strm, "w", encoding="utf-8") as handle:
                handle.write("http://example/movie\n")

            with patch.object(core, "DOWNLOAD_MOVIES_PATH", movies), patch.object(
                core, "STRM_OUTPUT_MOVIES_PATH", strm_root
            ), patch.object(core, "DOWNLOAD_TV_PATH", os.path.join(tmp, "tv")), patch.object(
                core, "STRM_OUTPUT_SERIES_PATH", os.path.join(tmp, "strm-series")
            ), patch.object(core, "DATA_DIR", data), patch.object(
                library_strm_watch, "MIN_VIDEO_BYTES", 1
            ), patch.object(library_strm_watch, "SETTLE_SECONDS", 0), patch.object(
                library_strm_watch, "request_silo_rescan"
            ) as rescan, patch.object(
                library_strm_watch, "notify_media_servers_after_local_download",
                return_value=[],
            ):
                clear_folder_match_cache()
                result = tick_library_strm_watch()

            self.assertEqual(result["removed"], 1)
            self.assertFalse(os.path.exists(strm))
            self.assertTrue(rescan.called)
            scanned = rescan.call_args[0][0]
            self.assertIn(local, scanned)
            self.assertTrue(any(path.endswith(".strm") for path in scanned))

    def test_settled_episode_without_local_marker_removes_strm(self):
        with tempfile.TemporaryDirectory() as tmp:
            tv = os.path.join(tmp, "tv")
            strm_root = os.path.join(tmp, "strm")
            data = os.path.join(tmp, "data")
            series = "Show (2022) [tmdbid-9]"
            os.makedirs(os.path.join(tv, series, "Season 01"))
            os.makedirs(os.path.join(strm_root, series, "Season 01"))
            os.makedirs(data)
            local = os.path.join(
                tv, series, "Season 01", "Show.S01E02.1080p.mkv"
            )
            strm = os.path.join(
                strm_root, series, "Season 01", "Show - S01E02.strm"
            )
            with open(local, "wb") as handle:
                handle.write(b"video-bytes")
            with open(strm, "w", encoding="utf-8") as handle:
                handle.write("http://example/episode\n")

            with patch.object(core, "DOWNLOAD_TV_PATH", tv), patch.object(
                core, "STRM_OUTPUT_SERIES_PATH", strm_root
            ), patch.object(
                core, "DOWNLOAD_MOVIES_PATH", os.path.join(tmp, "movies")
            ), patch.object(
                core, "STRM_OUTPUT_MOVIES_PATH", os.path.join(tmp, "strm-movies")
            ), patch.object(core, "DATA_DIR", data), patch.object(
                library_strm_watch, "MIN_VIDEO_BYTES", 1
            ), patch.object(library_strm_watch, "SETTLE_SECONDS", 0), patch.object(
                library_strm_watch, "request_silo_rescan"
            ), patch.object(
                library_strm_watch, "notify_media_servers_after_local_download",
                return_value=[],
            ):
                clear_folder_match_cache()
                result = tick_library_strm_watch()
                self.assertEqual(
                    relative_library_dir(local),
                    ("series", f"{series}/Season 01"),
                )

            self.assertEqual(result["removed"], 1)
            self.assertFalse(os.path.exists(strm))

    def test_growing_file_is_not_removed_until_size_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            movies = os.path.join(tmp, "movies")
            strm_root = os.path.join(tmp, "strm")
            data = os.path.join(tmp, "data")
            title = "Growing (2026)"
            os.makedirs(os.path.join(movies, title))
            os.makedirs(os.path.join(strm_root, title))
            os.makedirs(data)
            local = os.path.join(movies, title, f"{title}.mkv")
            strm = os.path.join(strm_root, title, f"{title}.strm")
            with open(local, "wb") as handle:
                handle.write(b"aa")
            with open(strm, "w", encoding="utf-8") as handle:
                handle.write("http://example/movie\n")

            patches = (
                patch.object(core, "DOWNLOAD_MOVIES_PATH", movies),
                patch.object(core, "STRM_OUTPUT_MOVIES_PATH", strm_root),
                patch.object(core, "DOWNLOAD_TV_PATH", os.path.join(tmp, "tv")),
                patch.object(core, "STRM_OUTPUT_SERIES_PATH", os.path.join(tmp, "series")),
                patch.object(core, "DATA_DIR", data),
                patch.object(library_strm_watch, "MIN_VIDEO_BYTES", 1),
                patch.object(library_strm_watch, "SETTLE_SECONDS", 30),
                patch.object(library_strm_watch, "request_silo_rescan"),
                patch.object(
                    library_strm_watch,
                    "notify_media_servers_after_local_download",
                    return_value=[],
                ),
            )
            for item in patches:
                item.start()
            self.addCleanup(lambda: [item.stop() for item in patches])
            clear_folder_match_cache()
            with patch.object(library_strm_watch.time, "time", return_value=1000.0):
                first = tick_library_strm_watch()
            self.assertEqual(first["removed"], 0)
            self.assertTrue(os.path.exists(strm))
            with open(local, "ab") as handle:
                handle.write(b"bb")
            with patch.object(library_strm_watch.time, "time", return_value=1010.0):
                second = tick_library_strm_watch()
            self.assertEqual(second["removed"], 0)
            self.assertTrue(os.path.exists(strm))
            with patch.object(library_strm_watch.time, "time", return_value=1040.0):
                third = tick_library_strm_watch()
            self.assertEqual(third["removed"], 1)
            self.assertFalse(os.path.exists(strm))

    def test_request_silo_rescan_posts_union_path(self):
        client = MagicMock()
        client.list_libraries.return_value = [
            {"type": "movies", "enabled": True, "paths": ["/mnt/union/Movies"]}
        ]
        with tempfile.TemporaryDirectory() as tmp:
            movies = os.path.join(tmp, "movies")
            title = "Heart (2026)"
            folder = os.path.join(movies, title)
            os.makedirs(folder)
            local = os.path.join(folder, "movie.mkv")
            with open(local, "wb") as handle:
                handle.write(b"x")
            with patch.object(core, "DOWNLOAD_MOVIES_PATH", movies), patch.object(
                core, "STRM_OUTPUT_MOVIES_PATH", os.path.join(tmp, "strm")
            ), patch.object(
                library_strm_watch, "_silo_client", return_value=client
            ):
                strm = os.path.join(tmp, "strm", title, f"{title}.strm")
                library_strm_watch.request_silo_rescan([local, strm])
        client.request_scan.assert_called_once_with(f"/mnt/union/Movies/{title}")

    def test_cleanup_ignores_video_outside_the_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            stray = os.path.join(tmp, "notes.mkv")
            with open(stray, "wb") as handle:
                handle.write(b"video")
            with patch.object(core, "DOWNLOAD_MOVIES_PATH", os.path.join(tmp, "movies")), patch.object(
                core, "DOWNLOAD_TV_PATH", os.path.join(tmp, "tv")
            ), patch.object(
                core, "STRM_OUTPUT_MOVIES_PATH", os.path.join(tmp, "strm-m")
            ), patch.object(
                core, "STRM_OUTPUT_SERIES_PATH", os.path.join(tmp, "strm-s")
            ):
                self.assertEqual(cleanup_superseded_strm(stray), [])


if __name__ == "__main__":
    unittest.main()
