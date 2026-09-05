import os
import tempfile
import unittest
from unittest import mock

import intro_skip


class RemainingEpisodesTest(unittest.TestCase):
    def test_includes_later_seasons(self):
        items = [
            {"ParentIndexNumber": 0, "IndexNumber": 1, "Id": "special"},
            {"ParentIndexNumber": 1, "IndexNumber": 5, "Id": "s1e5", "Path": "/a.strm"},
            {"ParentIndexNumber": 1, "IndexNumber": 6, "Id": "s1e6"},
            {"ParentIndexNumber": 2, "IndexNumber": 1, "Id": "s2e1"},
            {"ParentIndexNumber": 2, "IndexNumber": 2, "Id": "s2e2"},
        ]
        remaining = intro_skip.remaining_episode_items(items, 1, 5, include_current=True)
        self.assertEqual(
            [(r["season"], r["episode"], r["id"]) for r in remaining],
            [(1, 5, "s1e5"), (1, 6, "s1e6"), (2, 1, "s2e1"), (2, 2, "s2e2")],
        )
        later = intro_skip.remaining_episode_items(items, 1, 5, include_current=False)
        self.assertEqual(
            [(r["season"], r["episode"]) for r in later],
            [(1, 6), (2, 1), (2, 2)],
        )

    def test_skips_specials_and_invalid(self):
        rows = intro_skip.parse_series_episode_items(
            [
                {"ParentIndexNumber": 1, "IndexNumber": 1},
                {"ParentIndexNumber": "x", "IndexNumber": 1, "Id": "bad"},
            ]
        )
        self.assertEqual(rows, [])


class IntroCachePathTest(unittest.TestCase):
    def test_sample_path_is_under_data_dir_not_download_tv(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "intro-cache")
            with mock.patch.dict(os.environ, {"INTRO_CACHE_DIR": cache}):
                path = intro_skip._sample_path(
                    "The Night Manager (2016) [tmdbid-61859]", 2, 3
                )
            self.assertTrue(path.startswith(cache))
            self.assertNotIn("/download/tv", path.replace("\\", "/"))
            self.assertTrue(path.endswith("S02E03.sample.mkv"))

    def test_list_cache_samples_all_seasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "Series (2020)")
            os.makedirs(folder)
            for name, size in (
                ("S01E01.sample.mkv", 2_000_000),
                ("S02E04.sample.mkv", 2_000_000),
                ("S01E02.sample.mkv", 10),
            ):
                with open(os.path.join(folder, name), "wb") as fh:
                    fh.write(b"0" * size)
            with mock.patch.object(intro_skip, "get_intro_cache_dir", return_value=tmp):
                found = intro_skip.list_intro_cache_samples("Series (2020)")
            self.assertEqual(
                [(s, e) for s, e, _p in found],
                [(1, 1), (2, 4)],
            )

    def test_find_local_uses_cache_without_download_tv(self):
        with tempfile.TemporaryDirectory() as tmp:
            series = "TNM"
            sample = os.path.join(tmp, series, "S01E02.sample.mkv")
            os.makedirs(os.path.dirname(sample))
            with open(sample, "wb") as fh:
                fh.write(b"0" * 1_500_000)
            with mock.patch.object(intro_skip, "get_intro_cache_dir", return_value=tmp):
                found = intro_skip.find_local_episode_video(series, 1, 2)
            self.assertEqual(found, sample)

    def test_series_refs_prefer_same_season(self):
        with tempfile.TemporaryDirectory() as tmp:
            series = "TNM"
            folder = os.path.join(tmp, series)
            os.makedirs(folder)
            for name in ("S01E01.sample.mkv", "S02E01.sample.mkv"):
                with open(os.path.join(folder, name), "wb") as fh:
                    fh.write(b"0" * 1_500_000)
            with mock.patch.object(intro_skip, "get_intro_cache_dir", return_value=tmp):
                with mock.patch.object(intro_skip, "DOWNLOAD_TV_PATH", tmp):
                    refs = intro_skip._series_reference_paths(
                        series, prefer_season=2, exclude_season=2, exclude_episode=2
                    )
            self.assertEqual(
                [os.path.basename(p) for p in refs],
                ["S02E01.sample.mkv", "S01E01.sample.mkv"],
            )


class IntroBackfillTest(unittest.TestCase):
    def test_save_load_from_episode_and_later_season(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backfill.json")
            with mock.patch.object(intro_skip, "INTRO_BACKFILL_FILE", path):
                with mock.patch("core._ensure_data_dir"):
                    intro_skip.save_intro_season_backfill(
                        series_id="abc",
                        series_folder="TNM",
                        from_season=1,
                        from_episode=4,
                        user_id="u1",
                    )
                    rows = intro_skip.load_intro_season_backfills()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["from_season"], 1)
                    self.assertEqual(rows[0]["from_episode"], 4)
                    intro_skip.save_intro_season_backfill(
                        series_id="abc",
                        series_folder="TNM",
                        from_season=2,
                        from_episode=1,
                    )
                    rows = intro_skip.load_intro_season_backfills()
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0]["from_season"], 2)
                    self.assertEqual(rows[0]["from_episode"], 1)
                    intro_skip.clear_intro_season_backfill("abc")
                    self.assertEqual(intro_skip.load_intro_season_backfills(), [])

    def test_legacy_season_only_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backfill.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(
                    '{"seasons":[{"series_id":"x","series_folder":"TNM","season":2}]}'
                )
            with mock.patch.object(intro_skip, "INTRO_BACKFILL_FILE", path):
                rows = intro_skip.load_intro_season_backfills()
            self.assertEqual(rows[0]["from_season"], 2)
            self.assertEqual(rows[0]["from_episode"], 1)


class FingerprintWithoutBlacksTest(unittest.TestCase):
    def test_fingerprint_used_when_no_blacks(self):
        with mock.patch.object(intro_skip, "_run_blackdetect", return_value=[]):
            with mock.patch.object(
                intro_skip,
                "detect_intro_recap_via_fingerprint",
                return_value=((180.0, 225.0), None),
            ):
                with mock.patch.object(intro_skip.os.path, "isfile", return_value=True):
                    windows = intro_skip.detect_recap_intro_windows(
                        "/tmp/fake.mkv",
                        reference_paths=["/tmp/other.mkv"],
                    )
        self.assertEqual(windows["intro"], (180.0, 225.0))
        self.assertIsNone(windows["recap"])


class ClassifyIntroRecapTest(unittest.TestCase):
    def test_theme_at_start_is_intro_not_recap(self):
        intro, recap = intro_skip.classify_intro_and_recap([(0.0, 48.0)])
        self.assertEqual(intro, (0.0, 48.0))
        self.assertIsNone(recap)

    def test_early_sting_then_theme(self):
        intro, recap = intro_skip.classify_intro_and_recap(
            [(0.0, 40.0), (95.0, 145.0)]
        )
        self.assertEqual(intro, (95.0, 145.0))
        self.assertEqual(recap, (0.0, 40.0))

    def test_overlapping_same_theme_not_recap(self):
        intro, recap = intro_skip.classify_intro_and_recap(
            [(0.0, 50.0), (2.0, 47.0)]
        )
        self.assertEqual(intro, (0.0, 50.0))
        self.assertIsNone(recap)

    def test_identical_fingerprint_block_is_shared(self):
        block = [7] * 220
        lhs = [0] * 8 + block + [0] * 8
        rhs = [0] * 10 + block + [0] * 6
        ranges = intro_skip.shared_audio_ranges(lhs, rhs, min_duration=3.0)
        self.assertTrue(ranges)
        dur = max(le - ls for ls, le, _rs, _re in ranges)
        self.assertGreaterEqual(dur, 15.0)


class ConsensusIntroDurationTest(unittest.TestCase):
    def test_s2_uses_shortest_not_75s_or_87s(self):
        dur = intro_skip.consensus_intro_duration(
            [62.3, 73.8, 87.1, 77.9, 77.4, 87.1]
        )
        self.assertEqual(dur, 62.3)

    def test_s1_uses_shortest_not_e06_outlier(self):
        dur = intro_skip.consensus_intro_duration(
            [50.2, 52.2, 50.6, 52.7, 51.0, 66.6]
        )
        self.assertEqual(dur, 50.2)

    def test_already_tight_uses_median(self):
        dur = intro_skip.consensus_intro_duration([50.2, 51.0, 52.2, 50.6])
        self.assertIsNotNone(dur)
        self.assertGreaterEqual(dur, 50.0)
        self.assertLessEqual(dur, 52.2)


class AlignSeasonIntroDurationsTest(unittest.TestCase):
    def test_delays_early_start_keeps_end_and_recap(self):
        ticks = 10_000_000
        windows = {
            "e1": (117.6, 179.9, (0.0, 16.7)),
            "e2": (141.2, 215.0, None),
            "e3": (171.0, 258.1, None),
            "e4": (133.1, 211.0, None),
            "e5": (101.1, 178.5, None),
            "e6": (156.1, 243.3, None),
        }
        segments = {}
        for item_id, (start, end, recap) in windows.items():
            segs = [
                {
                    "Type": "Intro",
                    "StartTicks": int(round(start * ticks)),
                    "EndTicks": int(round(end * ticks)),
                }
            ]
            if recap:
                segs.insert(
                    0,
                    {
                        "Type": "Recap",
                        "StartTicks": int(round(recap[0] * ticks)),
                        "EndTicks": int(round(recap[1] * ticks)),
                    },
                )
            segments[item_id] = segs
        written: list[tuple] = []

        class Client:
            def get_series_episodes(self, _user_id, _series_id):
                return [
                    {"Id": f"e{n}", "ParentIndexNumber": 2, "IndexNumber": n}
                    for n in range(1, 7)
                ]

        def fake_list(_client, item_id):
            return segments[item_id]

        def fake_set(_client, item_id, start, end, recap=None, **_kw):
            written.append((item_id, start, end, recap))
            return True

        with mock.patch.object(intro_skip, "jellyfin_list_segments", fake_list):
            with mock.patch.object(intro_skip, "jellyfin_set_intro", fake_set):
                out = intro_skip.align_season_intro_durations(
                    Client(),
                    user_id="u",
                    series_id="s",
                    season=2,
                )
        self.assertEqual(out["duration"], 62.3)
        self.assertGreaterEqual(out["spread"], 20.0)
        by_id = {row[0]: row for row in written}
        self.assertNotIn("e1", by_id)
        self.assertIn("e2", by_id)
        self.assertAlmostEqual(by_id["e2"][1], 152.7, places=1)
        self.assertAlmostEqual(by_id["e2"][2], 215.0, places=1)
        self.assertIn("e3", by_id)
        self.assertAlmostEqual(by_id["e3"][1], 195.8, places=1)
        self.assertAlmostEqual(by_id["e3"][2], 258.1, places=1)
        self.assertIsNone(by_id["e3"][3])
        self.assertAlmostEqual(by_id["e6"][1], 181.0, places=1)
        self.assertAlmostEqual(by_id["e6"][2], 243.3, places=1)


class IntroSampleFfmpegCmdTest(unittest.TestCase):
    def test_drops_subtitles_and_does_not_map_all_streams(self):
        cmd = intro_skip.intro_sample_ffmpeg_cmd("http://x/stream", "/tmp/a.part", 420)
        self.assertIn("-sn", cmd)
        maps = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-map"]
        self.assertEqual(maps, ["0:v:0", "0:a:0?"])


if __name__ == "__main__":
    unittest.main()
