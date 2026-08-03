"""Tests for audit cleanup: Italian audio detection and probe-failure batches."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from strm_duration_audit import (
    delete_bad_movie_strm,
    flush_probe_failure_batch,
    media_has_italian_audio,
)


class ItalianAudioTests(unittest.TestCase):
    def test_detects_ita_tag(self):
        media = {
            "streams": [
                {"type": "Video", "language": ""},
                {"type": "Audio", "language": "eng"},
                {"type": "Audio", "language": "ita"},
            ]
        }
        self.assertTrue(media_has_italian_audio(media))

    def test_detects_it_and_title(self):
        media = {
            "streams": [
                {"type": "Audio", "language": "it"},
            ]
        }
        self.assertTrue(media_has_italian_audio(media))
        media2 = {
            "streams": [
                {"type": "Audio", "language": "und", "title": "Italiano AC3"},
            ]
        }
        self.assertTrue(media_has_italian_audio(media2))

    def test_no_italian_when_other_langs_known(self):
        media = {
            "streams": [
                {"type": "Audio", "language": "eng"},
                {"type": "Audio", "language": "fra"},
            ]
        }
        self.assertFalse(media_has_italian_audio(media))

    def test_unknown_when_only_und(self):
        media = {
            "streams": [
                {"type": "Audio", "language": "und"},
                {"type": "Audio", "language": ""},
            ]
        }
        self.assertIsNone(media_has_italian_audio(media))

    def test_unknown_without_audio(self):
        self.assertIsNone(media_has_italian_audio({"streams": [{"type": "Video"}]}))
        self.assertIsNone(media_has_italian_audio(None))


class DeleteBadStrmTests(unittest.TestCase):
    def test_deletes_folder(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "Movie (2024) [tmdbid-1]")
            os.makedirs(folder)
            strm = os.path.join(folder, "Movie (2024) [tmdbid-1].strm")
            nfo = os.path.join(folder, "Movie (2024) [tmdbid-1].nfo")
            open(strm, "w").write("http://x/1.mp4\n")
            open(nfo, "w").write("<movie/>\n")
            result = delete_bad_movie_strm(strm, movies_root=root, keep_folder=False)
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "deleted_folder")
            self.assertFalse(os.path.exists(folder))

    def test_keeps_folder_deletes_strm_nfo(self):
        with tempfile.TemporaryDirectory() as root:
            folder = os.path.join(root, "Movie (2024) [tmdbid-1]")
            os.makedirs(folder)
            strm = os.path.join(folder, "Movie (2024) [tmdbid-1].strm")
            nfo = os.path.join(folder, "Movie (2024) [tmdbid-1].nfo")
            local = os.path.join(folder, "Movie (2024) [tmdbid-1] [LOCAL].mkv")
            open(strm, "w").write("http://x/1.mp4\n")
            open(nfo, "w").write("<movie/>\n")
            open(local, "wb").write(b"x" * 10)
            result = delete_bad_movie_strm(strm, movies_root=root, keep_folder=True)
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "deleted_strm")
            self.assertFalse(os.path.exists(strm))
            self.assertFalse(os.path.exists(nfo))
            self.assertTrue(os.path.exists(local))
            self.assertTrue(os.path.isdir(folder))

    def test_refuses_outside_root(self):
        with tempfile.TemporaryDirectory() as root:
            other = tempfile.mkdtemp()
            try:
                strm = os.path.join(other, "x.strm")
                open(strm, "w").write("u\n")
                result = delete_bad_movie_strm(strm, movies_root=root)
                self.assertFalse(result["ok"])
                self.assertEqual(result["action"], "refused")
            finally:
                import shutil

                shutil.rmtree(other, ignore_errors=True)


class ProbeBatchFlushTests(unittest.TestCase):
    def test_all_failed_keeps_files(self):
        with tempfile.TemporaryDirectory() as root:
            batch = []
            results = {}
            status = {"log": [], "probe_batch_kept": 0, "deleted_folders": 0}
            for i in range(3):
                folder = os.path.join(root, f"M{i} [tmdbid-{i}]")
                os.makedirs(folder)
                strm = os.path.join(folder, f"M{i} [tmdbid-{i}].strm")
                open(strm, "w").write("http://x\n")
                entry = {"status": "probe_failed", "strm_path": strm, "title": f"M{i}"}
                results[strm] = entry
                batch.append({"path": strm, "status": "probe_failed", "entry": entry})

            flush_probe_failure_batch(
                batch, results=results, status=status, movies_root=root
            )
            self.assertEqual(status["probe_batch_kept"], 3)
            self.assertEqual(len(batch), 0)
            for path, entry in results.items():
                self.assertEqual(entry["status"], "probe_failed")
                self.assertTrue(os.path.exists(path))

    def test_mixed_batch_deletes_failures(self):
        with tempfile.TemporaryDirectory() as root:
            batch = []
            results = {}
            status = {
                "log": [],
                "probe_batch_kept": 0,
                "deleted_folders": 0,
                "deleted_strm_only": 0,
            }

            ok_folder = os.path.join(root, "Ok [tmdbid-1]")
            os.makedirs(ok_folder)
            ok_strm = os.path.join(ok_folder, "Ok [tmdbid-1].strm")
            open(ok_strm, "w").write("http://ok\n")
            ok_entry = {"status": "ok", "strm_path": ok_strm, "title": "Ok"}
            results[ok_strm] = ok_entry
            batch.append({"path": ok_strm, "status": "ok", "entry": ok_entry})

            bad_folder = os.path.join(root, "Bad [tmdbid-2]")
            os.makedirs(bad_folder)
            bad_strm = os.path.join(bad_folder, "Bad [tmdbid-2].strm")
            open(bad_strm, "w").write("http://bad\n")
            bad_entry = {"status": "probe_failed", "strm_path": bad_strm, "title": "Bad"}
            results[bad_strm] = bad_entry
            batch.append({"path": bad_strm, "status": "probe_failed", "entry": bad_entry})

            with patch(
                "strm_duration_audit.local_download_exists_for_strm", return_value=False
            ):
                flush_probe_failure_batch(
                    batch, results=results, status=status, movies_root=root
                )

            self.assertEqual(results[bad_strm]["status"], "deleted_probe_failed")
            self.assertFalse(os.path.exists(bad_folder))
            self.assertTrue(os.path.exists(ok_strm))
            self.assertEqual(status["deleted_folders"], 1)


if __name__ == "__main__":
    unittest.main()
