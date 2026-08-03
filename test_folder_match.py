import os
import tempfile
import time
import unittest
from unittest import mock

from core import (
    build_episode_output,
    build_movie_output,
    clear_folder_match_cache,
    extract_title_year,
    find_strm_folder_match,
    normalize_title,
    resolve_movie_folder_name,
    resolve_series_folder_name,
    titles_match_loosely,
)


class FolderMatchTest(unittest.TestCase):
    def tearDown(self):
        clear_folder_match_cache()

    def test_titles_match_loosely_allows_leading_article(self):
        self.assertTrue(titles_match_loosely("Matrix", "The Matrix (1999)"))
        self.assertTrue(titles_match_loosely("The Matrix (1999)", "Matrix"))

    def test_titles_match_loosely_rejects_prefix_substrings(self):
        self.assertFalse(
            titles_match_loosely(
                "Il cacciatore (1978)",
                "Il cacciatore di vampiri (2013)",
            )
        )

    def test_find_strm_folder_match_respects_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Il cacciatore di vampiri (2013)"))
            os.makedirs(os.path.join(tmp, "Il cacciatore (1978)"))

            self.assertEqual(
                find_strm_folder_match(tmp, "Il cacciatore (1978)"),
                "Il cacciatore (1978)",
            )

    def test_find_strm_folder_match_does_not_match_similar_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Il cacciatore di vampiri (2013)"))

            self.assertIsNone(find_strm_folder_match(tmp, "Il cacciatore (1978)"))

    def test_folder_index_reuses_listing_until_mtime_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Matrix (1999)"))
            self.assertEqual(find_strm_folder_match(tmp, "Matrix (1999)"), "Matrix (1999)")

            # Add a new folder and bump mtime so the index rebuilds.
            os.makedirs(os.path.join(tmp, "Inception (2010)"))
            os.utime(tmp, (time.time() + 5, time.time() + 5))
            self.assertEqual(
                find_strm_folder_match(tmp, "Inception (2010)"),
                "Inception (2010)",
            )

    def test_folder_index_stale_until_mtime_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Matrix (1999)"))
            self.assertEqual(find_strm_folder_match(tmp, "Matrix (1999)"), "Matrix (1999)")

            # Same mtime: new folder is invisible until cache is cleared / mtime bumps.
            old_mtime = os.path.getmtime(tmp)
            os.makedirs(os.path.join(tmp, "Inception (2010)"))
            os.utime(tmp, (old_mtime, old_mtime))
            self.assertIsNone(find_strm_folder_match(tmp, "Inception (2010)"))

            clear_folder_match_cache(tmp)
            self.assertEqual(
                find_strm_folder_match(tmp, "Inception (2010)"),
                "Inception (2010)",
            )

    def test_resolve_movie_folder_name_uses_exact_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Il cacciatore di vampiri (2013)"))
            os.makedirs(os.path.join(tmp, "Il cacciatore (1978)"))

            with mock.patch("core.STRM_MOVIES_PATH", tmp):
                self.assertEqual(
                    resolve_movie_folder_name("Il cacciatore (1978)"),
                    "Il cacciatore (1978)",
                )

    def test_build_movie_output_uses_correct_folder_for_similar_titles(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            dl_root = os.path.join(tmp, "download")
            os.makedirs(os.path.join(strm_root, "Il cacciatore di vampiri (2013)"))

            with mock.patch("core.STRM_MOVIES_PATH", strm_root):
                path, output_file = build_movie_output(
                    "Il cacciatore (1978)", "mkv", dl_root
                )

            self.assertEqual(path, os.path.join(dl_root, "Il cacciatore (1978)"))
            self.assertEqual(
                output_file,
                os.path.join(dl_root, "Il cacciatore (1978)", "Il cacciatore (1978) [LOCAL].mkv"),
            )

    def test_normalize_title_strips_tmdb_folder_tags(self):
        self.assertEqual(
            normalize_title("Gli Irregolari di Baker Street"),
            normalize_title("Gli Irregolari di Baker Street (2021) [tmdbid-98187]"),
        )
        self.assertEqual(extract_title_year("Title (2021) [tmdbid-98187]"), 2021)

    def test_find_strm_folder_match_prefers_tmdb_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "Gli Irregolari di Baker Street"))
            os.makedirs(
                os.path.join(tmp, "Gli Irregolari di Baker Street (2021) [tmdbid-98187]")
            )

            self.assertEqual(
                find_strm_folder_match(tmp, "Gli Irregolari di Baker Street"),
                "Gli Irregolari di Baker Street (2021) [tmdbid-98187]",
            )

    def test_resolve_series_folder_name_uses_strm_tmdb_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            os.makedirs(
                os.path.join(strm_root, "Gli Irregolari di Baker Street (2021) [tmdbid-98187]")
            )

            with mock.patch("core.STRM_SERIES_PATH", strm_root):
                self.assertEqual(
                    resolve_series_folder_name("Gli Irregolari di Baker Street"),
                    "Gli Irregolari di Baker Street (2021) [tmdbid-98187]",
                )

    def test_resolve_series_folder_name_falls_back_to_download_tmdb_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            dl_root = os.path.join(tmp, "download")
            os.makedirs(strm_root)
            os.makedirs(os.path.join(dl_root, "Gli Irregolari di Baker Street"))
            os.makedirs(
                os.path.join(dl_root, "Gli Irregolari di Baker Street (2021) [tmdbid-98187]")
            )

            with mock.patch("core.STRM_SERIES_PATH", strm_root), mock.patch(
                "core.SERIES_DOWNLOAD_PATHS", (dl_root,)
            ):
                self.assertEqual(
                    resolve_series_folder_name("Gli Irregolari di Baker Street"),
                    "Gli Irregolari di Baker Street (2021) [tmdbid-98187]",
                )

    def test_build_episode_output_reuses_tmdb_folder_without_strm_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm_root = os.path.join(tmp, "strm")
            dl_root = os.path.join(tmp, "download")
            os.makedirs(strm_root)
            tmdb_folder = "Gli Irregolari di Baker Street (2021) [tmdbid-98187]"
            os.makedirs(os.path.join(dl_root, tmdb_folder, "Season 01"))

            with mock.patch("core.STRM_SERIES_PATH", strm_root), mock.patch(
                "core.SERIES_DOWNLOAD_PATHS", (dl_root,)
            ):
                path, output_file = build_episode_output(
                    "Gli Irregolari di Baker Street", 1, 2, "mkv", dl_root
                )

            self.assertEqual(path, os.path.join(dl_root, tmdb_folder, "Season 01"))
            self.assertIn(tmdb_folder, output_file)


if __name__ == "__main__":
    unittest.main()
