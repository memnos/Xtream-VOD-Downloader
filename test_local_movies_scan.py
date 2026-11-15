import os
import tempfile
import unittest
from unittest.mock import patch

from core import (
    LOCAL_DOWNLOAD_MARKER,
    clear_folder_match_cache,
    delete_movie_local_and_restore_strm,
    find_xtream_movie,
    resolve_movie_restore_url,
    scan_local_movie_downloads,
    write_movie_strm_url_sidecar,
)


class LocalMoviesScanTest(unittest.TestCase):
    def tearDown(self):
        clear_folder_match_cache()

    def test_scan_marks_strm_in_catalog_from_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = os.path.join(tmp, "download")
            strm_root = os.path.join(tmp, "strm")
            folder = os.path.join(dl, "Film (2024) [tmdbid-1]")
            os.makedirs(folder)
            local = os.path.join(folder, f"Film (2024) [tmdbid-1]{LOCAL_DOWNLOAD_MARKER}.mkv")
            with open(local, "wb") as handle:
                handle.write(b"video-bytes")
            write_movie_strm_url_sidecar(
                local,
                "http://xtream.example/movie/u/p/99.mkv",
                strm_path=os.path.join(
                    strm_root, "Film (2024) [tmdbid-1]", "Film (2024) [tmdbid-1].strm"
                ),
            )

            with patch("core.DOWNLOAD_MOVIES_PATH", dl), patch(
                "core.STRM_OUTPUT_MOVIES_PATH", strm_root
            ):
                rows = scan_local_movie_downloads(dl)

            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["strm_in_catalog"])
            self.assertFalse(rows[0]["strm_exists"])
            self.assertIn("Film (2024)", rows[0]["title"])
            self.assertTrue(rows[0]["size_bytes"] > 0)

    def test_scan_without_url_is_not_in_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "Orphan (2020) [tmdbid-2]")
            os.makedirs(folder)
            local = os.path.join(folder, f"Orphan (2020){LOCAL_DOWNLOAD_MARKER}.mkv")
            with open(local, "wb") as handle:
                handle.write(b"x")

            rows = scan_local_movie_downloads(tmp)
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["strm_in_catalog"])
            self.assertEqual(resolve_movie_restore_url(local), "")

    def test_scan_detects_non_local_mkv_and_skips_trailer(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "Patriot (2000) [tmdbid-2024]")
            os.makedirs(folder)
            main = os.path.join(
                folder, "Patriot (2000)-Bluray-1080p-tt0187393-MIRCrew.mkv"
            )
            trailer = os.path.join(
                folder, "Patriot (2000)-Bluray-1080p-tt0187393-MIRCrew-trailer.mkv"
            )
            with open(main, "wb") as handle:
                handle.write(b"main-video-content-xxxx")
            with open(trailer, "wb") as handle:
                handle.write(b"trailer")

            rows = scan_local_movie_downloads(tmp)
            self.assertEqual(len(rows), 1)
            self.assertEqual(os.path.realpath(rows[0]["local_path"]), os.path.realpath(main))
            self.assertEqual(rows[0]["size_bytes"], os.path.getsize(main))

    def test_scan_marks_xtream_catalog_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "300 (2006) [tmdbid-1271]")
            os.makedirs(folder)
            local = os.path.join(folder, "300 (2006)-WEBDL.mkv")
            with open(local, "wb") as handle:
                handle.write(b"movie")
            catalog = [
                {
                    "name": "300 (2007)",
                    "stream_id": 123,
                    "container_extension": "mkv",
                },
                {
                    "name": "Other (1999)",
                    "stream_id": 9,
                    "container_extension": "mp4",
                },
            ]
            rows = scan_local_movie_downloads(
                tmp,
                host="http://x",
                user="u",
                password="p",
                catalog=catalog,
            )
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["strm_in_catalog"])
            self.assertIn("/movie/u/p/123.mkv", rows[0]["strm_url"])

    def test_find_xtream_movie_prefers_matching_year(self):
        catalog = [
            {"name": "Film (2010)", "stream_id": 1, "container_extension": "mp4"},
            {"name": "Film (2012)", "stream_id": 2, "container_extension": "mkv"},
        ]
        hit = find_xtream_movie(
            "http://x", "u", "p", "Film (2012) [tmdbid-1]", catalog=catalog
        )
        self.assertEqual(hit["stream_id"], 2)

    def test_resolve_url_from_existing_strm_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = os.path.join(tmp, "download")
            strm_root = os.path.join(tmp, "strm")
            folder_name = "Show (2021) [tmdbid-3]"
            dl_folder = os.path.join(dl, folder_name)
            strm_folder = os.path.join(strm_root, folder_name)
            os.makedirs(dl_folder)
            os.makedirs(strm_folder)
            local = os.path.join(dl_folder, f"{folder_name}{LOCAL_DOWNLOAD_MARKER}.mkv")
            strm = os.path.join(strm_folder, f"{folder_name}.strm")
            with open(local, "wb") as handle:
                handle.write(b"x")
            with open(strm, "w", encoding="utf-8") as handle:
                handle.write("http://xtream.example/movie/u/p/7.mkv\n")

            with patch("core.DOWNLOAD_MOVIES_PATH", dl), patch(
                "core.STRM_OUTPUT_MOVIES_PATH", strm_root
            ):
                url = resolve_movie_restore_url(local)
                rows = scan_local_movie_downloads(dl)

            self.assertEqual(url, "http://xtream.example/movie/u/p/7.mkv")
            self.assertTrue(rows[0]["strm_in_catalog"])
            self.assertTrue(rows[0]["strm_exists"])

    def test_delete_restores_strm_and_removes_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            dl = os.path.join(tmp, "download")
            strm_root = os.path.join(tmp, "strm")
            folder_name = "Alpha (2022) [tmdbid-9]"
            folder = os.path.join(dl, folder_name)
            os.makedirs(folder)
            local = os.path.join(folder, f"{folder_name}{LOCAL_DOWNLOAD_MARKER}.mkv")
            with open(local, "wb") as handle:
                handle.write(b"video")
            target = os.path.join(strm_root, folder_name, f"{folder_name}.strm")
            write_movie_strm_url_sidecar(
                local,
                "http://xtream.example/movie/u/p/42.mkv",
                strm_path=target,
            )

            with patch("core.DOWNLOAD_MOVIES_PATH", dl), patch(
                "core.STRM_OUTPUT_MOVIES_PATH", strm_root
            ), patch(
                "core.notify_media_servers_after_local_download", return_value=[]
            ):
                result = delete_movie_local_and_restore_strm(local, notify=False)

            self.assertTrue(result["strm_restored"])
            self.assertTrue(os.path.isfile(target))
            self.assertFalse(os.path.isfile(local))
            with open(target, encoding="utf-8") as handle:
                body = handle.read()
            self.assertTrue(body.strip())


if __name__ == "__main__":
    unittest.main()
