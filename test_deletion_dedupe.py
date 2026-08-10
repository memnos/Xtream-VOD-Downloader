import os
import tempfile
import unittest
from unittest.mock import patch

import deletion


class DeletionPromptDedupeTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.prompts_file = os.path.join(self._tmpdir.name, "deletion_prompts.json")
        self._patcher = patch.object(deletion, "DELETION_PROMPTS_FILE", self.prompts_file)
        self._patcher.start()
        deletion.save_deletion_prompts({"pending": [], "dismissed": []})

    def tearDown(self):
        self._patcher.stop()
        self._tmpdir.cleanup()

    def test_add_same_path_different_series_ids_dedupes(self):
        path = "/download/tv/House of the Dragon (2022) [tmdbid-94997]"
        self.assertTrue(
            deletion.add_deletion_prompt("jelly-guid", "House of the Dragon", [path])
        )
        self.assertFalse(
            deletion.add_deletion_prompt("emby-6013186", "House of the Dragon", [path])
        )
        data = deletion.load_deletion_prompts()
        self.assertEqual(len(data["pending"]), 1)
        item = data["pending"][0]
        self.assertEqual(item["series_id"], "jelly-guid")
        self.assertIn("emby-6013186", item.get("alternate_series_ids") or [])

    def test_load_dedupes_existing_duplicates(self):
        path = "/download/tv/House of the Dragon (2022) [tmdbid-94997]"
        deletion.save_deletion_prompts(
            {
                "pending": [
                    {
                        "series_id": "jelly-guid",
                        "series_name": "House of the Dragon",
                        "paths": [path],
                    },
                    {
                        "series_id": "emby-6013186",
                        "series_name": "House of the Dragon",
                        "paths": [path],
                    },
                ],
                "dismissed": [],
            }
        )
        data = deletion.load_deletion_prompts()
        self.assertEqual(len(data["pending"]), 1)
        self.assertEqual(data["pending"][0]["series_id"], "jelly-guid")
        self.assertIn("emby-6013186", data["pending"][0].get("alternate_series_ids") or [])

    def test_remove_clears_all_ids_for_same_path(self):
        path = "/download/tv/House of the Dragon (2022) [tmdbid-94997]"
        deletion.add_deletion_prompt("jelly-guid", "House of the Dragon", [path])
        deletion.add_deletion_prompt("emby-6013186", "House of the Dragon", [path])
        removed = deletion.remove_deletion_prompt("emby-6013186")
        self.assertIsNotNone(removed)
        self.assertEqual(deletion.load_deletion_prompts()["pending"], [])

    def test_dismiss_clears_all_ids_for_same_path(self):
        path = "/download/tv/House of the Dragon (2022) [tmdbid-94997]"
        deletion.add_deletion_prompt("jelly-guid", "House of the Dragon", [path])
        deletion.add_deletion_prompt("emby-6013186", "House of the Dragon", [path])
        deletion.dismiss_deletion_prompt("emby-6013186")
        data = deletion.load_deletion_prompts()
        self.assertEqual(data["pending"], [])
        self.assertIn("jelly-guid", data["dismissed"])
        self.assertIn("emby-6013186", data["dismissed"])


if __name__ == "__main__":
    unittest.main()
