import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core import (
    LOCAL_DOWNLOAD_MARKER,
    align_episode_nfo_after_local_download,
    clear_folder_match_cache,
    delete_strm_after_local_download,
    finalize_after_local_download,
    find_local_files_for_strm,
    find_strm_files_for_local,
    local_download_exists_for_strm,
    map_local_path_to_media_server,
    parse_episode_numbers_from_path,
)


class LocalStrmHelpersTest(unittest.TestCase):
    def tearDown(self):
        clear_folder_match_cache()

    def test_parse_episode_numbers_from_filename(self):
        self.assertEqual(
            parse_episode_numbers_from_path(
                "/tv/Yellowstone (2018)/Season 04/Yellowstone (2018) - S04E06 [LOCAL].mp4"
            ),
            (4, 6),
        )
        self.assertEqual(
            parse_episode_numbers_from_path(
                "/tv/Yellowstone (2018)/Season 04/S04E06 - Episodio 6.strm"
            ),
            (4, 6),
        )

    def test_find_local_and_strm_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            dl_root = os.path.join(tmp, "download")
            series = "Yellowstone (2018)"
            season_dir = os.path.join(strm_root, series, "Season 04")
            dl_season_dir = os.path.join(dl_root, series, "Season 04")
            os.makedirs(season_dir)
            os.makedirs(dl_season_dir)

            strm_path = os.path.join(season_dir, "S04E06 - Episodio 6.strm")
            local_path = os.path.join(
                dl_season_dir,
                f"Yellowstone (2018) - S04E06{LOCAL_DOWNLOAD_MARKER}.mp4",
            )
            with open(strm_path, "w", encoding="utf-8") as handle:
                handle.write("http://example/stream\n")
            with open(local_path, "wb") as handle:
                handle.write(b"video")

            with patch("core.STRM_OUTPUT_SERIES_PATH", strm_root), patch(
                "core.SERIES_DOWNLOAD_PATHS", (dl_root,)
            ), patch("core.DOWNLOAD_TV_PATH", dl_root):
                self.assertTrue(local_download_exists_for_strm(strm_path))
                self.assertEqual(find_local_files_for_strm(strm_path), [local_path])
                self.assertEqual(find_strm_files_for_local(local_path), [strm_path])

                deleted = delete_strm_after_local_download(
                    local_path, strm_path=strm_path
                )
                self.assertEqual(deleted, [strm_path])
                self.assertFalse(os.path.exists(strm_path))

    def test_delete_strm_also_removes_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            dl_root = os.path.join(tmp, "download")
            series = "HotD (2022) [tmdbid-94997]"
            season_dir = os.path.join(strm_root, series, "Season 03")
            dl_season_dir = os.path.join(dl_root, series, "Season 03")
            os.makedirs(season_dir)
            os.makedirs(dl_season_dir)
            strm_path = os.path.join(season_dir, "HotD (2022) - S03E02.strm")
            strm_nfo = os.path.join(season_dir, "HotD (2022) - S03E02.nfo")
            local_path = os.path.join(
                dl_season_dir,
                f"HotD (2022) [tmdbid-94997] - S03E02{LOCAL_DOWNLOAD_MARKER}.mkv",
            )
            for path, data in (
                (strm_path, "http://x\n"),
                (strm_nfo, "<episodedetails/>"),
                (local_path, b"video"),
            ):
                mode = "wb" if isinstance(data, bytes) else "w"
                with open(
                    path, mode, encoding=None if isinstance(data, bytes) else "utf-8"
                ) as handle:
                    handle.write(data)

            with patch("core.STRM_OUTPUT_SERIES_PATH", strm_root):
                deleted = delete_strm_after_local_download(
                    local_path, strm_path=strm_path
                )
            self.assertIn(os.path.realpath(strm_path), deleted)
            self.assertIn(os.path.realpath(strm_nfo), deleted)
            self.assertFalse(os.path.exists(strm_path))
            self.assertFalse(os.path.exists(strm_nfo))

    def test_align_episode_nfo_renames_old_and_deletes_leftovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            series = "HotD (2022) [tmdbid-94997]"
            season_dir = os.path.join(tmp, "download", series, "Season 03")
            os.makedirs(season_dir)
            local_path = os.path.join(
                season_dir,
                f"HotD (2022) [tmdbid-94997] - S03E02{LOCAL_DOWNLOAD_MARKER}.mkv",
            )
            old_nfo = os.path.join(season_dir, "HotD (2022) - S03E02.nfo")
            other_nfo = os.path.join(season_dir, "HotD (2022) - S03E03.nfo")
            with open(local_path, "wb") as handle:
                handle.write(b"video")
            with open(old_nfo, "w", encoding="utf-8") as handle:
                handle.write("<episodedetails><title>E2</title></episodedetails>")
            with open(other_nfo, "w", encoding="utf-8") as handle:
                handle.write("<episodedetails><title>E3</title></episodedetails>")

            with patch("core.STRM_OUTPUT_SERIES_PATH", os.path.join(tmp, "strm")):
                result = align_episode_nfo_after_local_download(local_path)

            target = os.path.splitext(local_path)[0] + ".nfo"
            self.assertEqual(result["renamed_from"], os.path.realpath(old_nfo))
            self.assertTrue(os.path.isfile(target))
            self.assertFalse(os.path.exists(old_nfo))
            self.assertTrue(os.path.exists(other_nfo))  # different episode kept
            with open(target, encoding="utf-8") as handle:
                self.assertIn("E2", handle.read())

    def test_align_episode_nfo_deletes_old_when_local_nfo_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            series = "HotD (2022) [tmdbid-94997]"
            season_dir = os.path.join(tmp, "download", series, "Season 03")
            os.makedirs(season_dir)
            local_path = os.path.join(
                season_dir,
                f"HotD (2022) [tmdbid-94997] - S03E02{LOCAL_DOWNLOAD_MARKER}.mkv",
            )
            target = os.path.splitext(local_path)[0] + ".nfo"
            old_nfo = os.path.join(season_dir, "HotD (2022) - S03E02.nfo")
            with open(local_path, "wb") as handle:
                handle.write(b"video")
            with open(target, "w", encoding="utf-8") as handle:
                handle.write("<episodedetails><title>LOCAL</title></episodedetails>")
            with open(old_nfo, "w", encoding="utf-8") as handle:
                handle.write("<episodedetails><title>OLD</title></episodedetails>")

            with patch("core.STRM_OUTPUT_SERIES_PATH", os.path.join(tmp, "strm")):
                result = align_episode_nfo_after_local_download(local_path)

            self.assertEqual(result["renamed_from"], "")
            self.assertEqual(result["deleted"], [os.path.realpath(old_nfo)])
            self.assertFalse(os.path.exists(old_nfo))
            self.assertTrue(os.path.exists(target))

    def test_map_local_path_to_media_server(self):
        config = {
            "jellyfin_series_root": "/media/tv",
            "jellyfin_movies_root": "/media/movies",
            "emby_series_root": "/data/tv",
            "emby_movies_root": "/data/movies",
        }
        with patch("core.DOWNLOAD_TV_PATH", "/download/tv"), patch(
            "core.STRM_OUTPUT_SERIES_PATH", "/strm/series"
        ):
            self.assertEqual(
                map_local_path_to_media_server(
                    "/download/tv/Show/Season 01/Show - S01E01 [LOCAL].mkv",
                    server="jellyfin",
                    config=config,
                ),
                "/media/tv/Show/Season 01/Show - S01E01 [LOCAL].mkv",
            )
            self.assertEqual(
                map_local_path_to_media_server(
                    "/strm/series/Show/Season 01/Show - S01E01.strm",
                    server="emby",
                    config=config,
                ),
                "/data/tv/Show/Season 01/Show - S01E01.strm",
            )

    def test_finalize_notifies_media_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            dl_root = os.path.join(tmp, "download")
            series = "Show (2022) [tmdbid-123]"
            season_dir = os.path.join(dl_root, series, "Season 01")
            strm_season = os.path.join(strm_root, series, "Season 01")
            os.makedirs(season_dir)
            os.makedirs(strm_season)
            local_path = os.path.join(
                season_dir,
                f"Show (2022) [tmdbid-123] - S01E01{LOCAL_DOWNLOAD_MARKER}.mkv",
            )
            strm_path = os.path.join(strm_season, "Show (2022) - S01E01.strm")
            old_nfo = os.path.join(season_dir, "Show (2022) - S01E01.nfo")
            with open(local_path, "wb") as handle:
                handle.write(b"video")
            with open(strm_path, "w", encoding="utf-8") as handle:
                handle.write("http://x\n")
            with open(old_nfo, "w", encoding="utf-8") as handle:
                handle.write("<episodedetails/>")

            mock_client = MagicMock()
            mock_client.find_series_near_path.return_value = [{"Id": "series-1"}]
            mock_client.find_series_by_tmdb_id.return_value = []
            mock_cls = MagicMock(return_value=mock_client)

            with patch("core.STRM_OUTPUT_SERIES_PATH", strm_root), patch(
                "core.DOWNLOAD_TV_PATH", dl_root
            ), patch(
                "core.load_auto_download_config",
                return_value={
                    "emby_enabled": True,
                    "emby_url": "http://emby",
                    "emby_api_key": "k",
                    "jellyfin_enabled": True,
                    "jellyfin_url": "http://jf",
                    "jellyfin_api_key": "k",
                    "jellyfin_series_root": "/media/tv",
                    "jellyfin_movies_root": "/media/movies",
                    "emby_series_root": "/data/tv",
                    "emby_movies_root": "/data/movies",
                },
            ), patch("emby_watcher.MediaServerClient", mock_cls):
                result = finalize_after_local_download(
                    local_path, strm_path=strm_path, notify=True
                )

            self.assertFalse(os.path.exists(strm_path))
            target_nfo = os.path.splitext(local_path)[0] + ".nfo"
            self.assertTrue(os.path.isfile(target_nfo))
            self.assertFalse(os.path.exists(old_nfo))
            self.assertEqual(mock_cls.call_count, 2)
            self.assertTrue(mock_client.notify_library_paths.called)
            self.assertTrue(mock_client.refresh_item_metadata.called)
            self.assertTrue(any("ok" in note for note in result["notify"]))

    def test_find_local_detects_scene_release_without_local_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            dl_root = os.path.join(tmp, "download")
            series = "Black Rabbit (2025) [tmdbid-249039]"
            season_dir = os.path.join(strm_root, series, "Season 01")
            dl_season_dir = os.path.join(dl_root, series, "Season 01")
            os.makedirs(season_dir)
            os.makedirs(dl_season_dir)

            strm_path = os.path.join(season_dir, "Black Rabbit (2025) - S01E01.strm")
            local_path = os.path.join(
                dl_season_dir,
                "Black.Rabbit.S01E01.1080p.ENG.ITA.H265-TheBlackKing.mkv",
            )
            with open(strm_path, "w", encoding="utf-8") as handle:
                handle.write("http://example/stream\n")
            with open(local_path, "wb") as handle:
                handle.write(b"video")

            with patch("core.SERIES_DOWNLOAD_PATHS", (dl_root,)), patch(
                "core.DOWNLOAD_TV_PATH", dl_root
            ):
                self.assertTrue(local_download_exists_for_strm(strm_path))
                self.assertEqual(find_local_files_for_strm(strm_path), [local_path])

    def test_find_local_prefers_local_marker_over_scene_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl_root = os.path.join(tmp, "download")
            series = "Black Rabbit (2025) [tmdbid-249039]"
            dl_season_dir = os.path.join(dl_root, series, "Season 01")
            os.makedirs(dl_season_dir)
            scene = os.path.join(dl_season_dir, "Black.Rabbit.S01E01.mkv")
            marked = os.path.join(
                dl_season_dir, f"Black Rabbit - S01E01{LOCAL_DOWNLOAD_MARKER}.mkv"
            )
            for path in (scene, marked):
                with open(path, "wb") as handle:
                    handle.write(b"video")

            strm_path = os.path.join(
                tmp, "strm", series, "Season 01", "Black Rabbit - S01E01.strm"
            )
            os.makedirs(os.path.dirname(strm_path))
            with open(strm_path, "w", encoding="utf-8") as handle:
                handle.write("http://example/stream\n")

            with patch("core.SERIES_DOWNLOAD_PATHS", (dl_root,)):
                self.assertEqual(find_local_files_for_strm(strm_path), [marked])

    def test_find_local_movie_matches_by_tmdb_when_titles_differ(self):
        """IT strm folder vs EN Radarr folder sharing the same tmdbid."""
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            dl_root = os.path.join(tmp, "download")
            strm_folder = "Official Secrets - Segreto di stato (2019) [tmdbid-393624]"
            dl_folder = "Official Secrets (2019) [tmdbid-393624]"
            os.makedirs(os.path.join(strm_root, strm_folder))
            os.makedirs(os.path.join(dl_root, dl_folder))
            strm_path = os.path.join(strm_root, strm_folder, f"{strm_folder}.strm")
            local_path = os.path.join(
                dl_root,
                dl_folder,
                "Official Secrets (2019)-Bluray-1080p.mkv",
            )
            with open(strm_path, "w", encoding="utf-8") as handle:
                handle.write("http://example/stream\n")
            with open(local_path, "wb") as handle:
                handle.write(b"video")

            with patch("core.DOWNLOAD_MOVIES_PATH", dl_root), patch(
                "core.STRM_OUTPUT_MOVIES_PATH", strm_root
            ):
                clear_folder_match_cache()
                self.assertTrue(local_download_exists_for_strm(strm_path))
                self.assertEqual(find_local_files_for_strm(strm_path), [local_path])


if __name__ == "__main__":
    unittest.main()
