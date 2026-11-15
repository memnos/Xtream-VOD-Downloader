"""Weekly movie-URL check stays off the daily sync."""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from strm_scheduler import compute_next_url_check, decide_scheduled_sync
from ui_strm import _sync_config_for_run

ROME = ZoneInfo("Europe/Rome")


def _weekly(**overrides) -> dict:
    config = {
        "schedule_enabled": True,
        "schedule_mode": "daily",
        "schedule_hour": 3,
        "schedule_minute": 0,
        "url_check_mode": "weekly",
        "url_check_weekday": 0,
        "url_check_hour": 6,
        "url_check_minute": 0,
    }
    config.update(overrides)
    return config


class UrlCheckScheduleTests(unittest.TestCase):
    def setUp(self):
        self._tz = patch("strm_scheduler._timezone", return_value=ROME)
        self._tz.start()

    def tearDown(self):
        self._tz.stop()

    def test_sunday_afternoon_points_at_monday_morning(self):
        nxt = compute_next_url_check(
            _weekly(),
            after=datetime(2026, 11, 15, 12, 0, tzinfo=ROME),
        )
        self.assertEqual(nxt, datetime(2026, 11, 16, 6, 0, tzinfo=ROME))

    def test_exact_slot_waits_until_next_week(self):
        nxt = compute_next_url_check(
            _weekly(),
            after=datetime(2026, 11, 16, 6, 0, tzinfo=ROME),
        )
        self.assertEqual(nxt, datetime(2026, 11, 23, 6, 0, tzinfo=ROME))

    def test_just_before_the_slot_stays_on_that_morning(self):
        nxt = compute_next_url_check(
            _weekly(url_check_weekday=2, url_check_hour=9, url_check_minute=30),
            after=datetime(2026, 11, 18, 9, 29, tzinfo=ROME),
        )
        self.assertEqual(nxt, datetime(2026, 11, 18, 9, 30, tzinfo=ROME))

    def test_off_and_every_sync_have_no_weekly_slot(self):
        after = datetime(2026, 11, 15, 12, 0, tzinfo=ROME)
        self.assertIsNone(compute_next_url_check(_weekly(url_check_mode="off"), after=after))
        self.assertIsNone(
            compute_next_url_check(_weekly(url_check_mode="every_sync"), after=after)
        )

    def test_daily_run_does_not_open_urls(self):
        decision = decide_scheduled_sync(
            _weekly(),
            {
                "schedule_next_run": "2026-11-15 03:00:00",
                "url_check_next_run": "2026-11-16 06:00:00",
            },
            datetime(2026, 11, 15, 3, 0, tzinfo=ROME),
        )
        self.assertTrue(decision["run"])
        self.assertFalse(decision["verify_existing_urls"])
        self.assertTrue(decision["advance_daily"])
        self.assertFalse(decision["advance_url"])

    def test_monday_slot_opens_urls_without_moving_the_daily_clock(self):
        decision = decide_scheduled_sync(
            _weekly(),
            {
                "schedule_next_run": "2026-11-17 03:00:00",
                "url_check_next_run": "2026-11-16 06:00:00",
            },
            datetime(2026, 11, 16, 6, 0, tzinfo=ROME),
        )
        self.assertTrue(decision["run"])
        self.assertTrue(decision["verify_existing_urls"])
        self.assertFalse(decision["advance_daily"])
        self.assertTrue(decision["advance_url"])

    def test_overlapping_slots_run_once_with_the_url_check(self):
        decision = decide_scheduled_sync(
            _weekly(),
            {
                "schedule_next_run": "2026-11-16 06:00:00",
                "url_check_next_run": "2026-11-16 06:00:00",
            },
            datetime(2026, 11, 16, 6, 0, tzinfo=ROME),
        )
        self.assertTrue(decision["verify_existing_urls"])
        self.assertTrue(decision["advance_daily"])
        self.assertTrue(decision["advance_url"])

    def test_first_tick_only_stores_the_next_times(self):
        decision = decide_scheduled_sync(
            _weekly(),
            {},
            datetime(2026, 11, 15, 12, 0, tzinfo=ROME),
        )
        self.assertFalse(decision["run"])
        self.assertIsNotNone(decision["set_daily_next"])
        self.assertEqual(
            decision["set_url_next"],
            datetime(2026, 11, 16, 6, 0, tzinfo=ROME),
        )

    def test_every_sync_verifies_on_the_daily_run(self):
        decision = decide_scheduled_sync(
            _weekly(url_check_mode="every_sync"),
            {"schedule_next_run": "2026-11-15 03:00:00", "url_check_next_run": "2026-11-16 06:00:00"},
            datetime(2026, 11, 15, 3, 0, tzinfo=ROME),
        )
        self.assertTrue(decision["run"])
        self.assertTrue(decision["verify_existing_urls"])
        self.assertFalse(decision["advance_url"])
        self.assertTrue(decision["clear_url_next"])

    def test_manual_flag_follows_the_saved_choice(self):
        weekly = _weekly()
        self.assertFalse(_sync_config_for_run(weekly, manual=True)["verify_existing_urls"])
        weekly["url_check_on_manual"] = True
        self.assertTrue(_sync_config_for_run(weekly, manual=True)["verify_existing_urls"])
        self.assertFalse(_sync_config_for_run(weekly, manual=False)["verify_existing_urls"])
        every = _weekly(url_check_mode="every_sync")
        self.assertTrue(_sync_config_for_run(every, manual=False)["verify_existing_urls"])


if __name__ == "__main__":
    unittest.main()
