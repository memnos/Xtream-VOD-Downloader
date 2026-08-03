import time
import unittest
from unittest import mock

from tmdb import TmdbClient


class TmdbNegativeCacheTest(unittest.TestCase):
    def _client(self, **kwargs) -> TmdbClient:
        kwargs.setdefault("negative_ttl_seconds", 3600)
        return TmdbClient("test-key", **kwargs)

    def test_empty_search_stores_negative_and_hits_cache(self):
        client = self._client()
        with mock.patch.object(client, "_get", return_value={"results": []}) as get:
            self.assertIsNone(client.search_movie("Unknown Movie 2099"))
            self.assertEqual(client.lookups, 1)
            self.assertEqual(get.call_count, 1)

            self.assertIsNone(client.search_movie("Unknown Movie 2099"))
            self.assertEqual(client.lookups, 1)
            self.assertEqual(client.cache_hits, 1)
            self.assertEqual(get.call_count, 1)

    def test_network_failure_does_not_store_negative(self):
        client = self._client()
        with mock.patch.object(client, "_get", return_value=None) as get:
            self.assertIsNone(client.search_movie("Flaky Movie 2099"))
            self.assertIsNone(client.search_movie("Flaky Movie 2099"))
            self.assertEqual(client.lookups, 2)
            self.assertEqual(get.call_count, 2)

    def test_expired_negative_is_retried(self):
        client = self._client(negative_ttl_seconds=1)
        with mock.patch.object(client, "_get", return_value={"results": []}) as get:
            self.assertIsNone(client.search_movie("Retry Me 2099"))
            self.assertEqual(get.call_count, 1)

            # Age the negative entry past TTL.
            with client._cache_lock:
                for entry in client._cache.values():
                    if not entry.get("matched"):
                        entry["cached_at"] = time.time() - 10

            self.assertIsNone(client.search_movie("Retry Me 2099"))
            self.assertEqual(get.call_count, 2)

    def test_purge_expired_negative_cache(self):
        client = self._client(negative_ttl_seconds=60)
        client._store("movie:old:2000", {"matched": False})
        client._store("movie:fresh:2000", {"matched": False})
        with client._cache_lock:
            client._cache["movie:old:2000"]["cached_at"] = time.time() - 120

        purged = client.purge_expired_negative_cache()
        self.assertEqual(purged, 1)
        with client._cache_lock:
            self.assertNotIn("movie:old:2000", client._cache)
            self.assertIn("movie:fresh:2000", client._cache)

    def test_positive_match_still_cached(self):
        client = self._client()
        payload = {
            "results": [
                {
                    "id": 42,
                    "title": "The Matrix",
                    "release_date": "1999-03-31",
                    "adult": False,
                }
            ]
        }
        with mock.patch.object(client, "_get", return_value=payload) as get:
            hit = client.search_movie("The Matrix (1999)")
            self.assertEqual(hit["tmdb_id"], 42)
            again = client.search_movie("The Matrix (1999)")
            self.assertEqual(again["tmdb_id"], 42)
            self.assertEqual(get.call_count, 1)
            self.assertEqual(client.cache_hits, 1)


class TmdbEpisodeValidationTest(unittest.TestCase):
    def test_is_valid_tv_episode_uses_season_counts(self):
        client = TmdbClient("test-key", negative_ttl_seconds=3600)
        with mock.patch.object(
            client,
            "get_tv_season_episode_counts",
            return_value={1: 10, 2: 8},
        ):
            self.assertTrue(client.is_valid_tv_episode(81292, 1, 10))
            self.assertFalse(client.is_valid_tv_episode(81292, 1, 11))
            self.assertFalse(client.is_valid_tv_episode(81292, 3, 1))
            self.assertFalse(client.is_valid_tv_episode(81292, 1, 0))

    def test_is_valid_tv_episode_fails_open_without_data(self):
        client = TmdbClient("test-key", negative_ttl_seconds=3600)
        with mock.patch.object(client, "get_tv_season_episode_counts", return_value=None):
            self.assertIsNone(client.is_valid_tv_episode(81292, 1, 11))
        self.assertIsNone(client.is_valid_tv_episode(None, 1, 1))


if __name__ == "__main__":
    unittest.main()
