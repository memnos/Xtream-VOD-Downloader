import os
import tempfile
import unittest
from unittest import mock

from auto_subtitles import (
    _forced_fallback_message,
    choose_subtitle_entry,
    episode_in_scope,
    episode_strms_in_series,
    fetch_subtitle_variant,
    has_language_sidecar,
    opensubtitles_search_params,
    resolve_server_media_path,
    sidecar_paths_for_language,
    subtitle_job_kwargs,
    subtitle_result_row,
    write_language_sidecar,
)


def _entry(*, forced: bool, hi: bool = False, file_id: int = 1) -> dict:
    return {
        "attributes": {
            "foreign_parts_only": forced,
            "hearing_impaired": hi,
            "files": [{"file_id": file_id}],
            "release": "forced" if forced else "full",
        }
    }


class SubtitleSearchTest(unittest.TestCase):
    def test_movie_forced_query_uses_tmdb_id(self):
        params = opensubtitles_search_params(tmdb_id=42, language="it", forced=True)
        self.assertEqual(params["type"], "movie")
        self.assertEqual(params["tmdb_id"], 42)
        self.assertEqual(params["foreign_parts_only"], "only")
        self.assertNotIn("parent_tmdb_id", params)

    def test_episode_query_uses_parent_tmdb_id(self):
        params = opensubtitles_search_params(
            tmdb_id=7, language="en", forced=False, season=2, episode=3
        )
        self.assertEqual(params["type"], "episode")
        self.assertEqual(params["parent_tmdb_id"], 7)
        self.assertEqual(params["season_number"], 2)
        self.assertEqual(params["episode_number"], 3)
        self.assertEqual(params["foreign_parts_only"], "exclude")

    def test_forced_choice_rejects_full_subtitles(self):
        self.assertIsNone(choose_subtitle_entry([_entry(forced=False)], forced=True))

    def test_forced_choice_keeps_a_marked_hit(self):
        picked = choose_subtitle_entry(
            [_entry(forced=False, file_id=1), _entry(forced=True, file_id=2)],
            forced=True,
        )
        self.assertEqual(picked["attributes"]["files"][0]["file_id"], 2)

    def test_whole_series_includes_every_season(self):
        self.assertTrue(episode_in_scope(1, 1, season=3, from_episode=5, all_seasons=True))
        self.assertFalse(episode_in_scope(3, 4, season=3, from_episode=5, all_seasons=False))
        self.assertTrue(episode_in_scope(3, 5, season=3, from_episode=5, all_seasons=False))

    def test_italian_forced_sidecar_uses_existing_name(self):
        paths = sidecar_paths_for_language("/media/Film.strm", language="it", forced=True)
        self.assertEqual(paths[0], "/media/Film.ita.forced.srt")

    def test_other_language_sidecar_name(self):
        paths = sidecar_paths_for_language("/media/Film.strm", language="en", forced=False)
        self.assertEqual(paths, ["/media/Film.en.srt"])

    def test_local_video_sidecar_drops_mkv_extension(self):
        paths = sidecar_paths_for_language("/media/Film.mkv", language="it", forced=True)
        self.assertEqual(paths[0], "/media/Film.ita.forced.srt")

    def test_union_path_resolves_to_the_local_branch(self):
        with tempfile.TemporaryDirectory() as movies:
            folder = os.path.join(movies, "Lo Hobbit (2012) [tmdbid-49051]")
            os.makedirs(folder)
            video = os.path.join(folder, "Lo Hobbit (2012).mkv")
            with open(video, "w", encoding="utf-8") as fh:
                fh.write("video")
            server_path = "/mnt/union/Movies/Lo Hobbit (2012) [tmdbid-49051]/Lo Hobbit (2012).mkv"
            found = resolve_server_media_path(
                server_path,
                server="silo",
                movie_roots=(movies,),
                series_roots=(),
            )
            self.assertEqual(found, video)

    def test_result_row_says_when_the_file_was_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "Film.ita.forced.srt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("1\n00:00:01,000 --> 00:00:02,000\nCiao\n")
            row = subtitle_result_row(
                {"ok": True, "skipped": False, "forced": True, "language": "it", "path": path},
                label="Film.mkv",
            )
            self.assertEqual(row["outcome"], "saved")
            self.assertEqual(row["name"], "Film.ita.forced.srt")
            self.assertRegex(row["saved_at"], r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}")
            missing = subtitle_result_row(
                {"ok": False, "forced": True, "language": "it", "path": ""},
                label="Film.mkv",
            )
            self.assertEqual(missing["outcome"], "missing")
            self.assertEqual(missing["saved_at"], "")

    def test_write_and_skip_existing_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            strm = os.path.join(tmp, "Film (2020) [tmdbid-9].strm")
            with open(strm, "w", encoding="utf-8") as fh:
                fh.write("http://example\n")
            out = write_language_sidecar(strm, "1\n00:00:01,000 --> 00:00:02,000\nCiao\n", language="it", forced=True)
            self.assertTrue(out.endswith(".ita.forced.srt"))
            self.assertTrue(has_language_sidecar(strm, language="it", forced=True))
            self.assertFalse(has_language_sidecar(strm, language="it", forced=False))
            self.assertFalse(has_language_sidecar(strm, language="en", forced=True))

    def test_episode_scan_covers_every_season(self):
        with tempfile.TemporaryDirectory() as tmp:
            for season, episode in ((1, 2), (2, 1)):
                folder = os.path.join(tmp, f"Season {season:02d}")
                os.makedirs(folder)
                name = f"Show - S{season:02d}E{episode:02d}.strm"
                with open(os.path.join(folder, name), "w", encoding="utf-8") as fh:
                    fh.write("http://example\n")
            found = episode_strms_in_series(tmp)
            self.assertEqual([(item[1], item[2]) for item in found], [(1, 2), (2, 1)])

    def test_episode_path_is_a_subtitle_job(self):
        episode = "/strm/series/Show (2020) [tmdbid-9]/Season 01/Show (2020) - S01E04.strm"
        self.assertEqual(subtitle_job_kwargs(episode), {"season": 1, "episode": 4})
        movie = "/strm/movies/Film (2020) [tmdbid-9]/Film (2020) [tmdbid-9].strm"
        self.assertEqual(subtitle_job_kwargs(movie), {})

    def test_forced_fallback_messages_are_explicit(self):
        self.assertIn("EN/FPO", _forced_fallback_message("S01E01", "en_not_found"))
        self.assertIn("quota DeepL", _forced_fallback_message("Film", "deepl_quota"))
        self.assertIn("chiave DeepL", _forced_fallback_message("Film", "deepl_missing"))

    def test_forced_falls_back_to_english_deepl(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "Film (2020) [tmdbid-42]")
            os.makedirs(folder)
            strm = os.path.join(folder, "Film (2020) [tmdbid-42].strm")
            with open(strm, "w", encoding="utf-8") as fh:
                fh.write("http://example\n")
            config = {"deepl_api_key": "key:fx"}
            creds = {"username": "u", "password": "p", "api_key": "k"}
            with mock.patch("auto_subtitles.load_opensubtitles_credentials", return_value=creds):
                with mock.patch("auto_subtitles.opensubtitles_login", return_value="token"):
                    with mock.patch("auto_subtitles.search_movie_subtitles", return_value=[]) as search:
                        with mock.patch(
                            "auto_subtitles.translate_english_forced_to_italian",
                            return_value=(
                                "1\n00:00:01,000 --> 00:00:02,000\nCiao\n",
                                "",
                            ),
                        ) as translate:
                            result = fetch_subtitle_variant(
                                strm,
                                language="it",
                                forced=True,
                                config=config,
                            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["source"], "opensubtitles+deepl")
            self.assertTrue(result["path"].endswith(".ita.forced.srt"))
            self.assertTrue(os.path.isfile(result["path"]))
            translate.assert_called_once()
            # First IT forced search, then EN forced inside translate helper.
            self.assertEqual(search.call_args_list[0].kwargs["languages"], "it")


if __name__ == "__main__":
    unittest.main()
