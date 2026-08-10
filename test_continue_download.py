import os
import tempfile
import unittest
from unittest.mock import patch

import continue_download


class ContinueDownloadTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = self._tmpdir.name
        self.dl = os.path.join(self.root, "download")
        self.strm = os.path.join(self.root, "strm")
        self.pending = os.path.join(self.root, "pending.json")
        os.makedirs(self.dl)
        os.makedirs(self.strm)
        self._patches = [
            patch.object(continue_download, "PENDING_AUTO_DOWNLOADS_FILE", self.pending),
            patch.object(continue_download, "SERIES_DOWNLOAD_PATHS", (self.dl,)),
            patch(
                "continue_download.load_auto_download_config",
                return_value={"enabled": True, "continue_download_incomplete": True},
            ),
            patch(
                "continue_download.load_strm_sync_config",
                return_value={"series_output": self.strm},
            ),
        ]
        for p in self._patches:
            p.start()
        continue_download.save_pending_auto_downloads({"items": []})

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmpdir.cleanup()

    def _make_hotd(self):
        series = "House of the Dragon (2022) [tmdbid-94997]"
        local_season = os.path.join(self.dl, series, "Season 03")
        strm_season = os.path.join(self.strm, series, "Season 03")
        os.makedirs(local_season)
        os.makedirs(strm_season)
        for ep in (2, 3, 4, 5):
            open(
                os.path.join(
                    local_season,
                    f"{series} - S03E{ep:02d} [LOCAL].mkv",
                ),
                "w",
            ).close()
        for ep in (1, 6, 7):
            open(
                os.path.join(strm_season, f"House of the Dragon (2022) - S03E{ep:02d}.strm"),
                "w",
            ).close()
        # Older seasons as strm only — must NOT be queued
        s01 = os.path.join(self.strm, series, "Season 01")
        os.makedirs(s01)
        open(os.path.join(s01, "House of the Dragon (2022) - S01E01.strm"), "w").close()
        return os.path.join(self.dl, series)

    def test_watermark_and_newer_only(self):
        folder = self._make_hotd()
        self.assertEqual(continue_download.local_episode_watermark(folder), (3, 5))
        newer = continue_download.find_newer_strm_episodes_for_series(
            folder, strm_root=self.strm
        )
        keys = [(i["season"], i["episode"]) for i in newer]
        self.assertEqual(keys, [(3, 6), (3, 7)])

    def test_scan_enqueues(self):
        self._make_hotd()
        result = continue_download.scan_and_enqueue_continue_downloads(strm_root=self.strm)
        self.assertEqual(result["episodes"], 2)
        self.assertEqual(result["queued"], 2)
        pending = continue_download.take_pending_auto_downloads()
        self.assertEqual(len(pending), 2)
        self.assertEqual(
            {(p["season"], p["episode"]) for p in pending},
            {(3, 6), (3, 7)},
        )
        self.assertEqual(continue_download.take_pending_auto_downloads(), [])


class TmdbEndedGateTest(unittest.TestCase):
    def test_production_finished_requires_ended(self):
        from deletion import series_production_finished

        class FakeClient:
            def __init__(self, ended):
                self._ended = ended

            def is_tv_series_ended(self, tid):
                return self._ended

            def save_cache(self):
                pass

        with patch("deletion._tmdb_client_for_status", return_value=FakeClient(False)):
            self.assertFalse(
                series_production_finished(
                    ["/download/tv/House of the Dragon (2022) [tmdbid-94997]"]
                )
            )
        with patch("deletion._tmdb_client_for_status", return_value=FakeClient(True)):
            self.assertTrue(
                series_production_finished(
                    ["/download/tv/Show (2020) [tmdbid-1]"]
                )
            )
        with patch("deletion._tmdb_client_for_status", return_value=None):
            self.assertFalse(
                series_production_finished(["/download/tv/Show (2020) [tmdbid-1]"])
            )


if __name__ == "__main__":
    unittest.main()
