import unittest
from unittest import mock

from core import xtream_playback_blocks_extra_streams
from auto_subtitles import (
    _copy_non_subtitle_streams,
    _ensure_video_stream,
    _item_has_http_subtitle,
    _item_has_video_stream,
)


class XtreamPlaybackGuardTest(unittest.TestCase):
    def test_idle_allows_extra_streams(self):
        with mock.patch(
            "core.load_watcher_status",
            return_value={"playback_active": False, "current_playing": ""},
        ):
            self.assertFalse(xtream_playback_blocks_extra_streams())

    def test_local_playback_allows_extra_streams(self):
        with mock.patch(
            "core.load_watcher_status",
            return_value={
                "playback_active": True,
                "current_playing": "Some Movie (jellyfin) (locale)",
            },
        ):
            self.assertFalse(xtream_playback_blocks_extra_streams())

    def test_strm_playback_blocks_extra_streams(self):
        with mock.patch(
            "core.load_watcher_status",
            return_value={
                "playback_active": True,
                "current_playing": "The Night Manager S01E01 (jellyfin) (strm)",
            },
        ):
            self.assertTrue(xtream_playback_blocks_extra_streams())


class HttpSubtitleStreamGuardTest(unittest.TestCase):
    def test_subtitle_only_item_gets_video_restored(self):
        item = {
            "MediaStreams": [
                {"Type": "Subtitle", "Path": "http://x/p/sub/episode/abc.srt"}
            ]
        }
        streams = _ensure_video_stream(_copy_non_subtitle_streams(item))
        self.assertEqual([s["Type"] for s in streams], ["Video"])
        self.assertFalse(_item_has_video_stream(item))
        self.assertTrue(_item_has_http_subtitle(item, "http://x/p/sub/episode/abc.srt"))

    def test_existing_video_is_kept(self):
        item = {
            "MediaStreams": [
                {"Type": "Video", "Codec": "hevc", "Width": 1920, "Height": 1080},
                {"Type": "Audio", "Codec": "aac", "Channels": 2},
                {"Type": "Subtitle", "Path": "http://old/sub.srt"},
            ]
        }
        streams = _ensure_video_stream(_copy_non_subtitle_streams(item))
        self.assertEqual([s["Type"] for s in streams], ["Video", "Audio"])
        self.assertEqual(streams[0]["Codec"], "hevc")

    def test_empty_item_gets_placeholder_video(self):
        streams = _ensure_video_stream(_copy_non_subtitle_streams({}))
        self.assertEqual(streams[0]["Type"], "Video")


class XtreamPassthroughSlotTest(unittest.TestCase):
    def setUp(self) -> None:
        from stream_proxy import xtream_passthrough_reset

        xtream_passthrough_reset()
        self.addCleanup(xtream_passthrough_reset)

    def test_same_episode_index_uses_second_slot(self):
        from stream_proxy import (
            xtream_passthrough_acquire,
            xtream_passthrough_end,
        )

        b1, g1 = xtream_passthrough_acquire("ep-a", mode="primary")
        self.assertIsNone(b1)
        b2, g2 = xtream_passthrough_acquire("ep-a", mode="share")
        self.assertIsNone(b2)
        self.assertNotEqual(g1, g2)
        b3, g3 = xtream_passthrough_acquire("ep-a", mode="share")
        self.assertEqual(b3, "ep-a")
        self.assertEqual(g3, 0)
        xtream_passthrough_end("ep-a", g2)
        xtream_passthrough_end("ep-a", g1)

    def test_duplicate_play_does_not_take_index_slot(self):
        from stream_proxy import xtream_passthrough_acquire, xtream_passthrough_end

        self.assertIsNone(xtream_passthrough_acquire("ep-a", mode="primary")[0])
        self.assertEqual(
            xtream_passthrough_acquire("ep-a", mode="primary")[0], "ep-a"
        )
        self.assertIsNone(xtream_passthrough_acquire("ep-a", mode="share")[0])
        xtream_passthrough_end("ep-a")
        xtream_passthrough_end("ep-a")

    def test_same_episode_seek_preempts(self):
        import threading
        import time

        from stream_proxy import (
            xtream_passthrough_aborted,
            xtream_passthrough_acquire,
            xtream_passthrough_end,
        )

        blocker, gen = xtream_passthrough_acquire("ep-a", mode="primary")
        self.assertIsNone(blocker)
        self.assertGreater(gen, 0)
        released = threading.Event()

        def holder() -> None:
            while not xtream_passthrough_aborted(gen):
                time.sleep(0.01)
            xtream_passthrough_end("ep-a", gen)
            released.set()

        threading.Thread(target=holder, daemon=True).start()
        blocker2, gen2 = xtream_passthrough_acquire("ep-a", mode="preempt")
        self.assertIsNone(blocker2)
        self.assertNotEqual(gen2, gen)
        self.assertTrue(released.wait(2.0))
        xtream_passthrough_end("ep-a", gen2)

    def test_second_episode_is_denied_until_first_ends(self):
        from stream_proxy import xtream_passthrough_begin, xtream_passthrough_end

        self.assertIsNone(xtream_passthrough_begin("night-manager"))
        self.assertEqual(xtream_passthrough_begin("rick-morty"), "night-manager")
        xtream_passthrough_end("night-manager")
        self.assertIsNone(xtream_passthrough_begin("rick-morty"))
        xtream_passthrough_end("rick-morty")


class PassthroughProbeDetectTest(unittest.TestCase):
    def test_jellyfin_open_range_is_playback_not_probe(self):
        from stream_proxy import _passthrough_is_probe

        self.assertFalse(
            _passthrough_is_probe("Jellyfin-Server/10.10", 0, None)
        )
        self.assertFalse(
            _passthrough_is_probe("Jellyfin-Server/10.10", 50_000_000, None)
        )

    def test_small_closed_range_is_probe(self):
        from stream_proxy import _passthrough_is_probe

        self.assertTrue(_passthrough_is_probe("Jellyfin-Server/10.10", 0, 65535))
        self.assertTrue(_passthrough_is_probe("Mozilla/5.0", None, None, head_only=True))
        self.assertTrue(_passthrough_is_probe("ffprobe", 0, None))
        self.assertFalse(_passthrough_is_probe("Lavf/60.0", 0, None))
        self.assertFalse(_passthrough_is_probe("Lavf/60.0", 50_000_000, None))
        # Remux first chunk (~2MB) is play, not a header sniff.
        self.assertFalse(
            _passthrough_is_probe("Lavf/60.0", 0, 2 * 1024 * 1024 - 1)
        )


class PassthroughPreemptPolicyTest(unittest.TestCase):
    def test_second_start_does_not_preempt(self):
        from stream_proxy import _passthrough_should_preempt, _range_is_past_eof

        self.assertFalse(_passthrough_should_preempt(None, total_size=100))
        self.assertFalse(_passthrough_should_preempt(0, total_size=100))
        self.assertTrue(_passthrough_should_preempt(50_000_000, total_size=100_000_000))
        self.assertFalse(_passthrough_should_preempt(100, total_size=100))
        self.assertTrue(_range_is_past_eof(1439039202, 1439039202))
        self.assertFalse(_range_is_past_eof(0, 1439039202))
        # Last 512KB of S02E01-sized file is index, not Skip.
        self.assertFalse(
            _passthrough_should_preempt(1438514914, total_size=1439039202)
        )
        from stream_proxy import _passthrough_slot_mode

        self.assertEqual(_passthrough_slot_mode(0, total_size=1439039202), "primary")
        self.assertEqual(
            _passthrough_slot_mode(1438514914, total_size=1439039202), "share"
        )
        self.assertEqual(
            _passthrough_slot_mode(50_000_000, total_size=1439039202), "preempt"
        )


class IntroWindowParseTest(unittest.TestCase):
    def test_intro_and_recap_from_segments(self):
        from intro_skip import _intro_window_from_segments

        segs = [
            {"Type": "Recap", "StartTicks": 0, "EndTicks": 60_000_0000},
            {"Type": "Intro", "StartTicks": 90_000_0000, "EndTicks": 135_000_0000},
        ]
        window = _intro_window_from_segments(segs)
        self.assertIsNotNone(window)
        intro, recap = window
        self.assertEqual(intro, (90.0, 135.0))
        self.assertEqual(recap, (0.0, 60.0))

    def test_missing_intro_returns_none(self):
        from intro_skip import _intro_window_from_segments

        self.assertIsNone(_intro_window_from_segments([{"Type": "Recap", "StartTicks": 0, "EndTicks": 10_000_0000}]))


if __name__ == "__main__":
    unittest.main()
