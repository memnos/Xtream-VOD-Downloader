"""Quality ranking: tags win; without tags prefer newer Xtream `added`."""

from __future__ import annotations

import unittest

from core import pick_best_catalog_item, sort_catalog_versions


class QualityRankAddedTests(unittest.TestCase):
    def test_untagged_prefers_newer_added(self):
        older = {"name": "Odissea (2026)", "stream_id": "1", "added": "100"}
        newer = {"name": "Odissea (2026)", "stream_id": "2", "added": "200"}
        best = pick_best_catalog_item([older, newer], allow_4k=True)
        self.assertEqual(best["stream_id"], "2")
        ordered = sort_catalog_versions([older, newer])
        self.assertEqual([i["stream_id"] for i in ordered], ["2", "1"])

    def test_quality_tag_beats_newer_untagged(self):
        tagged = {
            "name": "Odissea (2026) WEB-DL 1080p",
            "stream_id": "1",
            "added": "100",
        }
        newer = {"name": "Odissea (2026)", "stream_id": "2", "added": "999"}
        best = pick_best_catalog_item([tagged, newer], allow_4k=True)
        self.assertEqual(best["stream_id"], "1")

    def test_same_tags_prefers_newer_added(self):
        older = {
            "name": "Film (2020) WEB-DL",
            "stream_id": "1",
            "added": "10",
        }
        newer = {
            "name": "Film (2020) WEB-DL",
            "stream_id": "2",
            "added": "50",
        }
        best = pick_best_catalog_item([older, newer], allow_4k=True)
        self.assertEqual(best["stream_id"], "2")


if __name__ == "__main__":
    unittest.main()
