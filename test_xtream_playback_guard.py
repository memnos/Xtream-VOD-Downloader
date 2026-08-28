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


if __name__ == "__main__":
    unittest.main()
