import unittest

from core import (
    episode_num_value,
    format_episode_choice,
    iter_season_episodes,
)


def _ep(num, title="Ep", stream_id="1"):
    return {"episode_num": num, "title": title, "id": stream_id}


class ManualSeriesSeasonsTest(unittest.TestCase):
    def test_iter_orders_by_season_then_episode(self):
        episodes_map = {
            "2": [_ep(3, "S2E3"), _ep(1, "S2E1")],
            "1": [_ep("2", "S1E2"), _ep(1, "S1E1")],
        }
        ordered = iter_season_episodes(episodes_map, ["2", "1"])
        keys = [
            (int(season), episode_num_value(ep), ep["title"])
            for season, ep in ordered
        ]
        self.assertEqual(
            keys,
            [
                (1, 1, "S1E1"),
                (1, 2, "S1E2"),
                (2, 1, "S2E1"),
                (2, 3, "S2E3"),
            ],
        )

    def test_iter_skips_missing_and_invalid_entries(self):
        episodes_map = {
            "1": [_ep(1, "ok"), "bad", None],
            "3": {"not": "a list"},
        }
        ordered = iter_season_episodes(episodes_map, ["3", "1", "9"])
        self.assertEqual(len(ordered), 1)
        self.assertEqual(ordered[0][1]["title"], "ok")

    def test_subset_keeps_canonical_order_not_click_order(self):
        ordered = iter_season_episodes(
            {
                "1": [_ep(1, "one"), _ep(2, "two")],
                "2": [_ep(1, "s2")],
            },
            ["1", "2"],
        )
        wanted = {
            format_episode_choice("2", _ep(1, "s2")),
            format_episode_choice("1", _ep(2, "two")),
        }
        chosen = [
            pair
            for pair in ordered
            if format_episode_choice(*pair) in wanted
        ]
        self.assertEqual(
            [format_episode_choice(*pair) for pair in chosen],
            ["S01E02 - two", "S02E01 - s2"],
        )

    def test_format_episode_choice_pads_numbers(self):
        self.assertEqual(
            format_episode_choice("4", _ep(7, "Pilot")),
            "S04E07 - Pilot",
        )


if __name__ == "__main__":
    unittest.main()
