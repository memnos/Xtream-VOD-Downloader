"""Silo client mapping, without a live server."""
import unittest
from unittest.mock import patch

from silo_client import SiloClient


def _client() -> SiloClient:
    return SiloClient("http://127.0.0.1:8090", "sa_test")


class SiloClientMappingTest(unittest.TestCase):
    def test_episode_session_uses_playing_file_path(self):
        client = _client()
        row = {
            "session_id": "sess-1",
            "user_id": "7",
            "username": "fabio",
            "content_id": "episode:show-s01e02",
            "media_file_id": "42",
            "media_title": "Episode 2",
            "media_type": "episode",
            "series_name": "The Show",
            "season_number": 1,
            "episode_number": 2,
            "position_seconds": 90.5,
            "file_duration": 3600,
            "source_bitrate_kbps": 2500,
            "is_paused": False,
            "updated_at": "2026-10-12T16:00:00Z",
        }
        watch = {
            "content_id": "episode:show-s01e02",
            "series_id": "series:show",
            "series_title": "The Show",
            "title": "Episode 2",
            "season_number": 1,
            "episode_number": 2,
            "versions": [
                {"file_id": "9", "file_path": "/strm/series/The Show/Season 01/other.strm"},
                {"file_id": "42", "file_path": "/strm/series/The Show/Season 01/ep.mkv"},
            ],
        }
        with patch.object(client, "_watch", return_value=watch):
            shaped = client._shape_session(row)
        self.assertIsNotNone(shaped)
        item = shaped["NowPlayingItem"]
        self.assertEqual(item["Type"], "Episode")
        self.assertEqual(item["Path"], "/strm/series/The Show/Season 01/ep.mkv")
        self.assertEqual(item["SeriesId"], "series:show")
        self.assertEqual(item["ParentIndexNumber"], 1)
        self.assertEqual(item["IndexNumber"], 2)
        self.assertEqual(item["Bitrate"], 2_500_000)
        self.assertEqual(shaped["PlayState"]["PositionTicks"], 905_000_000)
        self.assertEqual(shaped["UserName"], "fabio")

    def test_series_media_type_with_episode_numbers_is_episode(self):
        """Silo live sessions often report media_type=series for episode files."""
        client = _client()
        row = {
            "session_id": "sess-gent",
            "user_id": "1",
            "username": "Fabio",
            "content_id": "episode-tmdb-236235-2-1",
            "media_file_id": "143075",
            "media_title": "The Gentlemen",
            "episode_name": "Alla conquista del regno",
            "media_type": "series",
            "series_name": "The Gentlemen",
            "season_number": 2,
            "episode_number": 1,
            "position_seconds": 100,
            "is_paused": False,
        }
        watch = {
            "content_id": "episode-tmdb-236235-2-1",
            "type": "episode",
            "series_id": "series-tmdb-236235",
            "series_title": "The Gentlemen",
            "title": "Alla conquista del regno",
            "season_number": 2,
            "episode_number": 1,
            "versions": [
                {
                    "file_id": "143075",
                    "file_path": (
                        "/strm/series/The Gentlemen (2024) [tmdbid-236235]/"
                        "Season 02/The Gentlemen (2024) - S02E01.strm"
                    ),
                }
            ],
        }
        with patch.object(client, "_watch", return_value=watch):
            shaped = client._shape_session(row)
        self.assertIsNotNone(shaped)
        item = shaped["NowPlayingItem"]
        self.assertEqual(item["Type"], "Episode")
        self.assertEqual(item["SeriesId"], "series-tmdb-236235")
        self.assertEqual(item["SeriesName"], "The Gentlemen")
        self.assertEqual(item["ParentIndexNumber"], 2)
        self.assertEqual(item["IndexNumber"], 1)
        self.assertEqual(item["Name"], "Alla conquista del regno")
        self.assertTrue(item["Path"].endswith("S02E01.strm"))

    def test_bare_series_media_type_without_episode_is_ignored(self):
        client = _client()
        row = {
            "session_id": "sess-series",
            "user_id": "1",
            "username": "Fabio",
            "content_id": "series-tmdb-236235",
            "media_file_id": "1",
            "media_title": "The Gentlemen",
            "media_type": "series",
            "series_name": "The Gentlemen",
        }
        with patch.object(client, "_watch", return_value={"content_id": "series-tmdb-236235", "type": "series"}):
            self.assertIsNone(client._shape_session(row))

    def test_get_series_episodes_keeps_strm_path_and_played(self):
        client = _client()

        def collect(path, params=None, max_pages=5, **_kwargs):
            del params, max_pages
            if path.endswith("/seasons"):
                return [{"season_number": 1, "content_id": "season:1"}]
            if path.endswith("/episodes"):
                return [
                    {
                        "content_id": "episode:s01e01",
                        "season_number": 1,
                        "episode_number": 1,
                        "title": "One",
                        "user_data": {"played": True},
                    }
                ]
            return []

        with patch.object(client, "_collect", side_effect=collect), patch.object(
            client, "get_series_name", return_value="The Show"
        ), patch.object(
            client, "_file_path", return_value="/strm/series/The Show/Season 01/e01.strm"
        ) as file_path:
            episodes = client.get_series_episodes("7", "series:show", include_user_data=True)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["Path"], "/strm/series/The Show/Season 01/e01.strm")
        self.assertTrue(episodes[0]["UserData"]["Played"])
        self.assertEqual(episodes[0]["IndexNumber"], 1)
        file_path.assert_called_once_with("episode:s01e01")

    def test_get_series_episodes_can_skip_path_resolution(self):
        client = _client()

        def collect(path, params=None, max_pages=5, **_kwargs):
            del params, max_pages
            if path.endswith("/seasons"):
                return [
                    {"season_number": 1},
                    {"season_number": 3},
                ]
            if path.endswith("/seasons/3/episodes"):
                return [
                    {
                        "content_id": "episode:s03e01",
                        "season_number": 3,
                        "episode_number": 1,
                        "title": "Three",
                        "user_data": {"played": True},
                    }
                ]
            if path.endswith("/seasons/1/episodes"):
                return [
                    {
                        "content_id": "episode:s01e01",
                        "season_number": 1,
                        "episode_number": 1,
                        "title": "One",
                        "user_data": {"played": True},
                    }
                ]
            return []

        with patch.object(client, "_collect", side_effect=collect), patch.object(
            client, "get_series_name", return_value="HotD"
        ) as get_name, patch.object(
            client, "_file_path", return_value="/should/not/be/called"
        ) as file_path:
            episodes = client.get_series_episodes(
                "7", "series:show", include_user_data=True, resolve_paths=False
            )
        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["Path"], "")
        self.assertEqual(episodes[1]["ParentIndexNumber"], 3)
        self.assertTrue(episodes[1]["UserData"]["Played"])
        file_path.assert_not_called()
        get_name.assert_not_called()

    def test_search_series_uses_catalog_browse(self):
        client = _client()

        def fake_get(path, params=None):
            self.assertEqual(path, "/api/v2/catalog")
            self.assertEqual(params.get("type"), "series")
            self.assertEqual(params.get("q"), "Big Bang")
            return {
                "items": [
                    {
                        "content_id": "series-tmdb-1418",
                        "type": "series",
                        "title": "The Big Bang Theory",
                    }
                ]
            }

        with patch.object(client, "_get", side_effect=fake_get):
            hits = client.search_series("1", "Big Bang", limit=5)
        self.assertEqual(hits, [{"Id": "series-tmdb-1418", "Name": "The Big Bang Theory"}])

    def test_connection_reports_missing_user(self):
        client = _client()
        with patch.object(client, "_get", return_value={"username": "admin", "id": "1"}), patch.object(
            client, "list_users", return_value=[{"id": "1", "username": "admin"}]
        ):
            ok, detail = client.test_connection("fabio")
        self.assertFalse(ok)
        self.assertIn("fabio", detail)
        self.assertIn("admin", detail)

    def test_play_item_does_not_stop_playback(self):
        client = _client()
        with self.assertRaises(RuntimeError):
            client.play_item("sess", "episode:1")
        with self.assertRaises(RuntimeError):
            client.stop_playback("sess")


if __name__ == "__main__":
    unittest.main()
