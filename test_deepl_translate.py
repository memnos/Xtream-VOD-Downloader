import json
import os
import tempfile
import unittest
from unittest import mock

from deepl_translate import (
    DeepLQuotaExhausted,
    deepl_api_base,
    extract_srt_texts,
    rebuild_srt_with_translations,
    translate_srt,
)


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:02,500
Hello there

2
00:00:03,000 --> 00:00:04,000
Foreign words
on two lines
"""


class DeepLTranslateTest(unittest.TestCase):
    def test_free_key_uses_api_free_host(self):
        self.assertEqual(deepl_api_base("abc:fx"), "https://api-free.deepl.com")
        self.assertEqual(deepl_api_base("abc"), "https://api.deepl.com")

    def test_extract_and_rebuild_preserves_timing(self):
        texts = extract_srt_texts(SAMPLE_SRT)
        self.assertEqual(texts, ["Hello there", "Foreign words\non two lines"])
        rebuilt = rebuild_srt_with_translations(SAMPLE_SRT, ["Ciao", "Parole straniere\nsu due righe"])
        self.assertIn("00:00:01,000 --> 00:00:02,500", rebuilt)
        self.assertIn("Ciao", rebuilt)
        self.assertIn("Parole straniere\nsu due righe", rebuilt)
        self.assertNotIn("Hello there", rebuilt)

    def test_translate_srt_calls_deepl_and_rebuilds(self):
        with mock.patch("deepl_translate.translate_texts") as translate_texts:
            translate_texts.return_value = ["Ciao", "Parole"]
            with mock.patch("deepl_translate.load_deepl_usage_status", return_value={}):
                with mock.patch("deepl_translate.fetch_deepl_usage", return_value={}):
                    out = translate_srt(SAMPLE_SRT, api_key="test:fx")
        translate_texts.assert_called_once()
        args, kwargs = translate_texts.call_args
        self.assertEqual(args[0], ["Hello there", "Foreign words\non two lines"])
        self.assertEqual(kwargs["target_lang"], "IT")
        self.assertIn("Ciao", out)
        self.assertIn("Parole", out)

    def test_cached_exhausted_quota_blocks_translate(self):
        with mock.patch(
            "deepl_translate.load_deepl_usage_status",
            return_value={"exhausted": True, "message": "done", "remaining": 0},
        ):
            with self.assertRaises(DeepLQuotaExhausted):
                translate_srt(SAMPLE_SRT, api_key="test:fx")

    def test_usage_status_roundtrip(self):
        from deepl_translate import load_deepl_usage_status, save_deepl_usage_status

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "deepl_usage.json")
            with mock.patch("deepl_translate.deepl_usage_path", return_value=path):
                save_deepl_usage_status(
                    {
                        "character_count": 10,
                        "character_limit": 100,
                        "remaining": 90,
                        "exhausted": False,
                    }
                )
                data = load_deepl_usage_status()
                self.assertEqual(data["character_count"], 10)
                self.assertTrue(os.path.isfile(path))
                with open(path, encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh)["character_limit"], 100)


if __name__ == "__main__":
    unittest.main()
