"""Tests for 4K-only download/convert after STRM sync."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

import convert_4k_movies as c4k
from core import LOCAL_DOWNLOAD_MARKER, is_4k_title


class FourKOnlySelectTests(unittest.TestCase):
    def test_group_is_4k_only(self):
        only = [
            {"name": "Film (2026) [4K]", "stream_id": "1"},
            {"name": "Film (2026) 2160p", "stream_id": "2"},
        ]
        mixed = only + [{"name": "Film (2026) 1080p", "stream_id": "3"}]
        self.assertTrue(c4k.group_is_4k_only(only))
        self.assertFalse(c4k.group_is_4k_only(mixed))
        self.assertFalse(c4k.group_is_4k_only([]))
        self.assertTrue(is_4k_title("Sniper: Senza Nazione (2026) [4K]"))

    def test_select_by_tmdb_rating_high_to_low(self):
        groups = {
            "old": [
                {
                    "name": "Old [4K]",
                    "stream_id": "1",
                    "added": "10",
                    "vote_average": 4.0,
                }
            ],
            "new": [
                {
                    "name": "New [4K]",
                    "stream_id": "2",
                    "added": "99",
                    "vote_average": 6.1,
                }
            ],
            "best": [
                {
                    "name": "Best [4K]",
                    "stream_id": "6",
                    "added": "1",
                    "vote_average": 8.5,
                    "vote_count": 1200,
                }
            ],
            "hd": [
                {
                    "name": "HD 1080p",
                    "stream_id": "3",
                    "added": "100",
                    "vote_average": 9.0,
                }
            ],
            "both": [
                {"name": "Both [4K]", "stream_id": "4", "added": "80", "vote_average": 9.9},
                {"name": "Both 1080p", "stream_id": "5", "added": "81"},
            ],
        }
        picked = c4k.select_4k_only_items(groups, limit=1)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0][0], "best")
        self.assertAlmostEqual(picked[0][1]["_tmdb_vote"], 8.5)
        keys = [row[0] for row in c4k.select_4k_only_items(groups, limit=10)]
        self.assertEqual(keys, ["best", "new", "old"])
        unbounded = [row[0] for row in c4k.select_4k_only_items(groups)]
        self.assertEqual(unbounded, ["best", "new", "old"])

    def test_select_uses_rating_callback(self):
        groups = {
            "low": [{"name": "Low [4K]", "stream_id": "1", "vote_average": 9.0}],
            "high": [{"name": "High [4K]", "stream_id": "2", "vote_average": 1.0}],
        }

        def rating_of(item):
            name = str(item.get("name") or "")
            return (9.9, 10) if name.startswith("High") else (1.0, 1)

        keys = [row[0] for row in c4k.select_4k_only_items(groups, limit=2, rating_of=rating_of)]
        self.assertEqual(keys, ["high", "low"])


class FourKRevertAndConvertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = os.path.join(self.tmp.name, "converted.json")
        self.pending = os.path.join(self.tmp.name, "pending.json")
        self._patch = patch.object(c4k, "CONVERTED_4K_FILE", self.reg)
        self._pending_patch = patch.object(c4k, "PENDING_4K_FILE", self.pending)
        self._patch.start()
        self._pending_patch.start()

    def tearDown(self):
        self._pending_patch.stop()
        self._patch.stop()
        self.tmp.cleanup()

    def test_revert_removes_local_and_marks_registry(self):
        folder = os.path.join(self.tmp.name, "Film (2026) [tmdbid-1]")
        os.makedirs(folder)
        strm = os.path.join(folder, "Film (2026) [tmdbid-1].strm")
        local = os.path.join(
            folder, f"Film (2026) [tmdbid-1]{LOCAL_DOWNLOAD_MARKER}.mp4"
        )
        with open(strm, "w", encoding="utf-8") as handle:
            handle.write("http://example/4k\n")
        with open(local, "wb") as handle:
            handle.write(b"mp4")
        c4k.register_converted(
            catalog_key="film",
            strm_path=strm,
            converted_path=local,
            stream_id="9",
            name="Film [4K]",
        )
        self.assertTrue(c4k.revert_converted_if_local(strm))
        self.assertFalse(os.path.isfile(local))
        payload = c4k.load_converted_movies()
        self.assertEqual(payload["movies"]["film"]["status"], "reverted")

    def test_convert_pipeline_limit_and_transcode(self):
        movies_out = os.path.join(self.tmp.name, "strm")
        dl_root = os.path.join(self.tmp.name, "dl")
        os.makedirs(movies_out)
        os.makedirs(dl_root)
        groups = {
            "alpha": [
                {
                    "name": "Alpha [4K]",
                    "stream_id": "11",
                    "container_extension": "mkv",
                    "added": "50",
                }
            ]
        }
        strm_path = os.path.join(movies_out, "Alpha (2026)", "Alpha (2026).strm")
        downloaded = {}

        def fake_resolve(item):
            return strm_path, ""

        def fake_download(url, output_path, **_kwargs):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as handle:
                handle.write(b"mkv")
            downloaded["path"] = output_path
            return True

        def fake_transcode(src, dst, hdr=True):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as handle:
                handle.write(b"mp4")

        status = {"log": [], "phase": ""}
        with patch.object(c4k, "DOWNLOAD_MOVIES_PATH", dl_root), patch.object(
            c4k, "find_local_files_for_strm", return_value=[]
        ), patch(
            "strm_sync._append_log", lambda st, msg: st.setdefault("log", []).append(msg)
        ), patch("strm_sync._save_status", lambda st: None), patch(
            "core.finalize_after_local_download", lambda *a, **k: {}
        ):
            result = c4k.run_post_sync_4k_convert(
                "http://h",
                "u",
                "p",
                {"convert_4k_only_after_sync": True, "convert_4k_only_limit": 1},
                status,
                groups=groups,
                movies_output=movies_out,
                resolve_paths=fake_resolve,
                transcode=fake_transcode,
                download=fake_download,
                playback_blocked=lambda: False,
            )
        self.assertEqual(result["converted"], 1)
        self.assertEqual(result["failed"], 0)
        payload = c4k.load_converted_movies()
        self.assertEqual(payload["movies"]["alpha"]["status"], "converted")
        self.assertTrue(os.path.isfile(payload["movies"]["alpha"]["converted_path"]))
        self.assertFalse(os.path.isfile(downloaded["path"]))

    def test_already_local_does_not_eat_convert_quota(self):
        """Substantial library copies skip without registering; quota still converts others."""
        movies_out = os.path.join(self.tmp.name, "strm")
        dl_root = os.path.join(self.tmp.name, "dl")
        os.makedirs(movies_out)
        os.makedirs(dl_root)
        groups = {}
        strm_by_id = {}
        for i, (key, vote) in enumerate(
            [
                ("local1", 9.0),
                ("local2", 8.9),
                ("local3", 8.8),
                ("local4", 8.7),
                ("local5", 8.6),
                ("need_a", 8.5),
                ("need_b", 8.4),
            ],
            start=1,
        ):
            groups[key] = [
                {
                    "name": f"{key} [4K]",
                    "stream_id": str(i),
                    "container_extension": "mkv",
                    "vote_average": vote,
                }
            ]
            folder = os.path.join(movies_out, key)
            os.makedirs(folder)
            strm_by_id[str(i)] = os.path.join(folder, f"{key}.strm")

        library_file = os.path.join(self.tmp.name, "Bluray-1080p.mkv")
        with open(library_file, "wb") as handle:
            handle.write(b"x" * 200)

        def fake_resolve(item):
            return strm_by_id[str(item["stream_id"])], ""

        def fake_download(url, output_path, **_kwargs):
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "wb") as handle:
                handle.write(b"mkv")
            return True

        def fake_transcode(src, dst, hdr=True):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as handle:
                handle.write(b"mp4")

        def fake_local(strm_path):
            name = os.path.basename(os.path.dirname(strm_path))
            if name.startswith("local"):
                return [library_file]
            return []

        status = {"log": [], "phase": ""}
        with patch.object(c4k, "DOWNLOAD_MOVIES_PATH", dl_root), patch.object(
            c4k, "LIBRARY_COPY_MIN_BYTES", 100
        ), patch.object(
            c4k, "find_local_files_for_strm", side_effect=fake_local
        ), patch(
            "strm_sync._append_log", lambda st, msg: st.setdefault("log", []).append(msg)
        ), patch("strm_sync._save_status", lambda st: None), patch(
            "core.finalize_after_local_download", lambda *a, **k: {}
        ):
            result = c4k.run_post_sync_4k_convert(
                "http://h",
                "u",
                "p",
                {"convert_4k_only_after_sync": True, "convert_4k_only_limit": 2},
                status,
                groups=groups,
                movies_output=movies_out,
                resolve_paths=fake_resolve,
                transcode=fake_transcode,
                download=fake_download,
                playback_blocked=lambda: False,
            )
        self.assertEqual(result["skipped"], 5)
        self.assertEqual(result["converted"], 2)
        payload = c4k.load_converted_movies()
        self.assertEqual(payload["movies"]["need_a"]["status"], "converted")
        self.assertEqual(payload["movies"]["need_b"]["status"], "converted")
        self.assertNotIn("local1", payload["movies"])

    def test_trailers_are_ignored_pipeline_local_is_registered(self):
        trailer = os.path.join(self.tmp.name, "Film-Bluray-1080p-trailer.mkv")
        local = os.path.join(self.tmp.name, f"Film{LOCAL_DOWNLOAD_MARKER}.mp4")
        bluray = os.path.join(self.tmp.name, "Film-Bluray-1080p.mkv")
        with open(trailer, "wb") as handle:
            handle.write(b"t")
        with open(local, "wb") as handle:
            handle.write(b"local")
        with open(bluray, "wb") as handle:
            handle.write(b"x" * 200)
        self.assertTrue(c4k.is_trailer_or_sample(trailer))
        self.assertTrue(c4k.is_pipeline_local_video(local))
        self.assertFalse(c4k.is_pipeline_local_video(bluray))
        pipeline, library = c4k.classify_local_videos([trailer, bluray])
        self.assertEqual(pipeline, [])
        with patch.object(c4k, "LIBRARY_COPY_MIN_BYTES", 100):
            pipeline, library = c4k.classify_local_videos([trailer, bluray, local])
        self.assertEqual(pipeline, [local])
        self.assertEqual(library, [bluray])

    def test_prune_drops_non_pipeline_registry_rows(self):
        trailer = os.path.join(self.tmp.name, "Jurassic-trailer.mkv")
        local = os.path.join(self.tmp.name, f"OneMile{LOCAL_DOWNLOAD_MARKER}.mp4")
        with open(trailer, "wb") as handle:
            handle.write(b"t")
        with open(local, "wb") as handle:
            handle.write(b"ok")
        c4k.register_converted(
            catalog_key="keep",
            strm_path="/strm/one.strm",
            converted_path=local,
            stream_id="1",
            name="One Mile [4K]",
        )
        payload = c4k.load_converted_movies()
        payload["movies"]["junk"] = {
            "catalog_key": "junk",
            "name": "Jurassic Park [4K]",
            "converted_path": trailer,
            "status": "converted",
        }
        c4k.save_converted_movies(payload)
        dropped = c4k.prune_non_pipeline_registry()
        self.assertEqual(dropped, 1)
        kept = c4k.load_converted_movies()["movies"]
        self.assertIn("keep", kept)
        self.assertNotIn("junk", kept)

    def test_reverted_titles_are_not_selected_again(self):
        c4k.register_converted(
            catalog_key="la captura 2026",
            strm_path="/strm/x.strm",
            converted_path=os.path.join(self.tmp.name, f"x{LOCAL_DOWNLOAD_MARKER}.mp4"),
            stream_id="1",
            name="La captura (2026) [4K]",
        )
        # File missing → not pipeline-local; mark reverted like last night.
        payload = c4k.load_converted_movies()
        payload["movies"]["la captura 2026"]["status"] = "reverted"
        c4k.save_converted_movies(payload)
        groups = {
            "la captura 2026": [{"name": "La captura (2026) [4K]", "stream_id": "1"}],
            "other": [{"name": "Other [4K]", "stream_id": "2", "vote_average": 1.0}],
        }
        keys = [row[0] for row in c4k.select_4k_only_items(
            groups, skip_keys=c4k._already_converted_keys()
        )]
        self.assertEqual(keys, ["other"])

    def test_pending_job_roundtrip_and_pause_file(self):
        mkv = os.path.join(self.tmp.name, "Film [LOCAL].mkv")
        with open(mkv, "wb") as handle:
            handle.write(b"partial")
        c4k.save_pending_4k_job(
            {
                "catalog_key": "film 2026",
                "name": "Film (2026) [4K]",
                "stream_id": "9",
                "mkv_path": mkv,
                "status": "pending",
            }
        )
        job = c4k.load_pending_4k_job()
        self.assertEqual(job["catalog_key"], "film 2026")
        paused = c4k.pause_incomplete_mkv(mkv)
        self.assertTrue(paused.endswith(c4k.PAUSE_SUFFIX))
        self.assertFalse(os.path.isfile(mkv))
        self.assertTrue(os.path.isfile(paused))
        restored = c4k.restore_paused_mkv(mkv)
        self.assertEqual(restored, mkv)
        self.assertTrue(os.path.isfile(mkv))
        c4k.clear_pending_4k_job()
        self.assertIsNone(c4k.load_pending_4k_job())

    def test_disabled_is_noop(self):
        result = c4k.run_post_sync_4k_convert(
            "h",
            "u",
            "p",
            {"convert_4k_only_after_sync": False, "convert_4k_only_limit": 5},
            {},
            groups={"x": [{"name": "X [4K]", "stream_id": "1"}]},
            movies_output="/tmp",
        )
        self.assertEqual(result, {"converted": 0, "skipped": 0, "failed": 0, "paused": 0})

    def test_416_means_download_complete(self):
        from core import download_already_complete

        mkv = os.path.join(self.tmp.name, "done.mkv")
        with open(mkv, "wb") as handle:
            handle.write(b"x" * 2048)
        self.assertTrue(
            download_already_complete(
                "ERROR: unable to download video data: HTTP Error 416: Requested Range Not Satisfiable",
                mkv,
            )
        )
        self.assertFalse(download_already_complete("HTTP Error 404", mkv))
        self.assertIn("HTTP Error 416", c4k._brief_error(RuntimeError(
            "[generic] Extracting URL: http://example/secret\n"
            "ERROR: unable to download video data: HTTP Error 416: Requested Range Not Satisfiable"
        )))
        self.assertNotIn("secret", c4k._brief_error(RuntimeError(
            "[generic] Extracting URL: http://example/secret\n"
            "ERROR: unable to download video data: HTTP Error 416: Requested Range Not Satisfiable"
        )))

    def test_tick_pending_respects_backoff(self):
        c4k.save_pending_4k_job(
            {
                "catalog_key": "film 2026",
                "name": "Film [4K]",
                "status": "pending",
                "next_attempt_unix": time.time() + 3600,
            }
        )
        result = c4k.tick_pending_4k_convert()
        self.assertEqual(result["reason"], "backoff")


if __name__ == "__main__":
    unittest.main()
