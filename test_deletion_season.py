"""Season-level deletion prompts for still-airing shows."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import deletion


def _ep(season: int, episode: int, played: bool) -> dict:
    return {
        "ParentIndexNumber": season,
        "IndexNumber": episode,
        "UserData": {"Played": played},
    }


class SeasonDeletionLogicTest(unittest.TestCase):
    def test_is_season_fully_watched(self):
        eps = [_ep(3, 1, True), _ep(3, 2, True), _ep(2, 1, False)]
        self.assertTrue(deletion.is_season_fully_watched(eps, 3))
        self.assertFalse(deletion.is_season_fully_watched(eps, 2))
        self.assertFalse(deletion.is_season_fully_watched(eps, 9))

    def test_iter_local_season_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            s3 = os.path.join(tmp, "Season 03")
            s1 = os.path.join(tmp, "Season 01")
            os.makedirs(s3)
            os.makedirs(s1)
            open(os.path.join(s3, "Show - S03E01.mkv"), "wb").close()
            open(os.path.join(s1, "readme.txt"), "w").close()
            found = deletion.iter_local_season_folders(tmp)
            self.assertEqual([(3, s3)], found)

    def test_prompt_identity_and_season_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompts = os.path.join(tmp, "deletion_prompts.json")
            with patch.object(deletion, "DELETION_PROMPTS_FILE", prompts):
                deletion.save_deletion_prompts({"pending": [], "dismissed": []})
                path = "/download/tv/Lioness/Season 03"
                self.assertTrue(
                    deletion.add_deletion_prompt(
                        "series-tmdb-1", "Lioness", [path], season=3
                    )
                )
                self.assertFalse(
                    deletion.add_deletion_prompt(
                        "series-tmdb-1", "Lioness", [path], season=3
                    )
                )
                self.assertTrue(
                    deletion.add_deletion_prompt(
                        "series-tmdb-1",
                        "Lioness",
                        ["/download/tv/Lioness/Season 02"],
                        season=2,
                    )
                )
                data = deletion.load_deletion_prompts()
                self.assertEqual(len(data["pending"]), 2)
                self.assertEqual(data["pending"][0]["season"], 3)
                removed = deletion.remove_deletion_prompt("series-tmdb-1", season=3)
                self.assertEqual(removed.get("season"), 3)
                self.assertEqual(len(deletion.load_deletion_prompts()["pending"]), 1)

    def test_prune_keeps_season_prompts_for_airing_shows(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompts = os.path.join(tmp, "deletion_prompts.json")
            with patch.object(deletion, "DELETION_PROMPTS_FILE", prompts):
                deletion.save_deletion_prompts(
                    {
                        "pending": [
                            {
                                "series_id": "s1",
                                "series_name": "Airing",
                                "paths": ["/download/tv/Airing/Season 03"],
                                "season": 3,
                            },
                            {
                                "series_id": "s2",
                                "series_name": "AlsoAiring",
                                "paths": ["/download/tv/AlsoAiring"],
                            },
                        ],
                        "dismissed": [],
                    }
                )
                with patch.object(deletion, "series_production_finished", return_value=False):
                    removed = deletion.prune_incomplete_deletion_prompts()
                self.assertEqual(removed, 1)
                data = deletion.load_deletion_prompts()
                self.assertEqual(len(data["pending"]), 1)
                self.assertEqual(data["pending"][0]["season"], 3)

    def test_scan_adds_watched_local_season_while_airing(self):
        with tempfile.TemporaryDirectory() as tmp:
            prompts = os.path.join(tmp, "deletion_prompts.json")
            dl = os.path.join(tmp, "tv")
            series = os.path.join(dl, "Lioness (2023) [tmdbid-113962]")
            season = os.path.join(series, "Season 03")
            os.makedirs(season)
            open(os.path.join(season, "Lioness - S03E01.mkv"), "wb").close()

            media = MagicMock()
            media.server_type = "silo"
            media.get_series_episodes.return_value = [
                _ep(3, 1, True),
                _ep(3, 2, True),
                _ep(1, 1, False),
            ]

            with patch.object(deletion, "DELETION_PROMPTS_FILE", prompts), patch.object(
                deletion, "SERIES_DOWNLOAD_ROOTS", (dl,)
            ), patch.object(deletion, "series_production_finished", return_value=False):
                deletion.save_deletion_prompts({"pending": [], "dismissed": []})
                added = deletion.scan_completed_series_prompts(media, "1")
                pending = deletion.load_deletion_prompts()["pending"]
            self.assertEqual(added, 1)
            self.assertEqual(pending[0]["season"], 3)
            self.assertTrue(pending[0]["paths"][0].endswith("Season 03"))

    def test_series_folder_hint_from_season_path(self):
        hint = deletion._series_folder_hint_from_paths(
            ["/download/tv/Operazione speciale Lioness (2023) [tmdbid-113962]/Season 03"]
        )
        self.assertEqual(hint, "Operazione speciale Lioness (2023) [tmdbid-113962]")


if __name__ == "__main__":
    unittest.main()
