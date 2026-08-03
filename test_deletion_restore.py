import os
import tempfile
import unittest
from unittest.mock import patch

from core import LOCAL_DOWNLOAD_MARKER, clear_folder_match_cache
from deletion import (
    delete_series_downloads,
    delete_series_downloads_and_restore_strm,
    inventory_local_episodes,
    restore_strm_for_episodes,
)


class DeletionRestoreTest(unittest.TestCase):
    def tearDown(self):
        clear_folder_match_cache()

    def test_inventory_local_episodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            series = os.path.join(tmp, "Show (2022) [tmdbid-9]")
            season = os.path.join(series, "Season 01")
            os.makedirs(season)
            video = os.path.join(
                season, f"Show (2022) [tmdbid-9] - S01E02{LOCAL_DOWNLOAD_MARKER}.mkv"
            )
            with open(video, "wb") as handle:
                handle.write(b"video")
            with open(os.path.join(season, "Show - S01E02.nfo"), "w", encoding="utf-8") as handle:
                handle.write("<episodedetails/>")

            items = inventory_local_episodes([series])
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["season"], 1)
            self.assertEqual(items[0]["episode"], 2)
            self.assertEqual(items[0]["series_folder"], "Show (2022) [tmdbid-9]")

    def test_delete_series_downloads_only_under_download_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = os.path.join(tmp, "download")
            outside = os.path.join(tmp, "outside")
            series = os.path.join(dl, "Show")
            os.makedirs(series)
            os.makedirs(outside)
            with patch("deletion.SERIES_DOWNLOAD_ROOTS", (dl,)):
                deleted = delete_series_downloads([series, outside])
            self.assertEqual(deleted, [os.path.realpath(series)])
            self.assertFalse(os.path.isdir(series))
            self.assertTrue(os.path.isdir(outside))

    def test_restore_strm_for_episodes_writes_and_aligns_nfo(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            series_folder = "Show (2022) [tmdbid-9]"
            season_dir = os.path.join(strm_root, series_folder, "Season 01")
            os.makedirs(season_dir)
            old_nfo = os.path.join(
                season_dir, f"Show (2022) [tmdbid-9] - S01E01{LOCAL_DOWNLOAD_MARKER}.nfo"
            )
            with open(old_nfo, "w", encoding="utf-8") as handle:
                handle.write("<episodedetails><title>E1</title></episodedetails>")

            episodes = [
                {
                    "season": 1,
                    "episode": 1,
                    "path": "/x",
                    "series_folder": series_folder,
                }
            ]

            with patch(
                "deletion.load_credentials",
                return_value={"host": "http://x", "user": "u", "password": "p"},
            ), patch(
                "deletion.load_auto_download_config", return_value={"allow_4k": False}
            ), patch(
                "deletion.load_strm_sync_config",
                return_value={"series_output": strm_root, "use_tmdb": False},
            ), patch(
                "deletion.find_xtream_series",
                return_value={"series_id": 1, "name": "Show"},
            ), patch(
                "deletion.get_series_info",
                return_value={
                    "episodes": {
                        "1": [
                            {
                                "id": 42,
                                "episode_num": 1,
                                "container_extension": "mkv",
                            }
                        ]
                    }
                },
            ), patch("deletion.prepare_output_dir"):
                result = restore_strm_for_episodes(
                    "Show",
                    episodes,
                    series_folder_hint=series_folder,
                )

            self.assertEqual(len(result["created"]), 1)
            strm_path = result["created"][0]
            self.assertTrue(strm_path.endswith(".strm"))
            self.assertTrue(os.path.isfile(strm_path))
            with open(strm_path, encoding="utf-8") as handle:
                self.assertIn("/series/u/p/42.mkv", handle.read())
            target_nfo = os.path.splitext(strm_path)[0] + ".nfo"
            self.assertTrue(os.path.isfile(target_nfo))
            self.assertFalse(os.path.exists(old_nfo))
            self.assertGreaterEqual(result["nfo_aligned"], 1)

    def test_delete_and_restore_orchestrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = os.path.join(tmp, "download")
            series = os.path.join(dl, "Show (2022) [tmdbid-9]")
            season = os.path.join(series, "Season 01")
            os.makedirs(season)
            video = os.path.join(
                season, f"Show - S01E01{LOCAL_DOWNLOAD_MARKER}.mkv"
            )
            with open(video, "wb") as handle:
                handle.write(b"video")

            with patch("deletion.SERIES_DOWNLOAD_ROOTS", (dl,)), patch(
                "deletion.SERIES_DOWNLOAD_PATHS", (dl,)
            ), patch(
                "deletion.restore_strm_for_episodes",
                return_value={
                    "created": ["/strm/Show/Season 01/Show - S01E01.strm"],
                    "updated": [],
                    "skipped": 0,
                    "missing": [],
                    "nfo_aligned": 1,
                    "errors": [],
                },
            ) as restore_mock, patch(
                "deletion.notify_media_servers_after_local_download",
                return_value=["emby:ok"],
            ):
                result = delete_series_downloads_and_restore_strm(
                    [series], series_name="Show", notify=True
                )

            self.assertFalse(os.path.isdir(series))
            self.assertEqual(len(result["episodes"]), 1)
            restore_mock.assert_called_once()
            self.assertEqual(result["notify"], ["emby:ok"])


if __name__ == "__main__":
    unittest.main()
