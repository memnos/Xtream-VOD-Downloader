"""yt-dlp must not send a browser fingerprint to Xtream, or print the 90-day warning."""

import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests

import core
from core import DownloadCancelled, run_ytdlp, ytdlp_output_without_update_warning


BODY = b"FAKEVIDEO" * 4000


def _blocked(headers) -> bool:
    ua = headers.get("User-Agent", "")
    accept = headers.get("Accept", "")
    return (
        "Mozilla" in ua
        or "text/html" in accept
        or "Sec-Fetch-Mode" in headers
    )


class _QuietHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return None

    def _send(self, status: int, payload: bytes, *, content_type: str, extra=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Accept-Ranges", "bytes")
        for key, value in extra or ():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.server.seen.append({k: v for k, v in self.headers.items()})
        if self.server.forbid_all or _blocked(self.headers):
            self._send(403, b"Forbidden", content_type="text/plain")
            return
        body = self.server.body
        start = 0
        status = 200
        extra = []
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            spec = range_header.split("=", 1)[1].split(",", 1)[0]
            start_s, _, end_s = spec.partition("-")
            try:
                start = int(start_s or "0")
            except ValueError:
                start = 0
            if start >= len(body):
                self._send(
                    416,
                    b"",
                    content_type="video/mp4",
                    extra=(("Content-Range", f"bytes */{len(body)}"),),
                )
                return
            status = 206
            extra = [("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")]
            self.server.ranges.append(range_header)
        self._send(status, body[start:], content_type="video/mp4", extra=extra)


class MediaServer:
    def __init__(self, body: bytes = BODY, *, forbid_all: bool = False):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
        self.httpd.body = body
        self.httpd.seen = []
        self.httpd.ranges = []
        self.httpd.forbid_all = forbid_all
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address
        return f"http://{host}:{port}/movie/user/pass/519632.mkv"

    @property
    def seen(self):
        return self.httpd.seen

    @property
    def ranges(self):
        return self.httpd.ranges

    def start(self) -> "MediaServer":
        self._thread.start()
        return self

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class YtdlpDownloadTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.output = os.path.join(self._tmpdir.name, "Heart of the Beast.mkv")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_warning_text_is_removed_from_errors(self):
        raw = [
            "WARNING: Your yt-dlp version (2026.06.09) is older than 90 days!",
            "It is strongly recommended to always use the latest version.",
            "You installed yt-dlp with pip or using the wheel from PyPI; Use that to update.",
            "To suppress this warning, add --no-update to your command/config.",
            "[generic] Extracting URL: http://listavod.example/movie/u/p/519632.mkv",
            "[generic] 519632: Downloading webpage",
            "ERROR: [generic] Unable to download webpage: HTTP Error 403: Forbidden "
            "(caused by <HTTPError 403: Forbidden>)",
        ]
        cleaned = ytdlp_output_without_update_warning(raw)
        self.assertNotIn("older than 90 days", cleaned)
        self.assertNotIn("latest version", cleaned)
        self.assertNotIn("--no-update", cleaned)
        self.assertIn("HTTP Error 403", cleaned)
        self.assertIn("519632", cleaned)
        doubled = ytdlp_output_without_update_warning(raw + [raw[-1]])
        self.assertEqual(doubled, cleaned)
        self.assertEqual(cleaned.count("HTTP Error 403"), 1)

    def test_browser_request_is_forbidden_and_app_download_succeeds(self):
        server = MediaServer().start()
        self.addCleanup(server.close)
        probe = requests.get(
            server.url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xhtml+xml",
                "Sec-Fetch-Mode": "navigate",
            },
            timeout=5,
        )
        self.assertEqual(probe.status_code, 403)

        seen_before = len(server.seen)
        progress = []

        def on_progress(value, text):
            progress.append((value, text))

        with patch("yt_dlp.update._get_outdated_warning", side_effect=AssertionError("age check")):
            ok = run_ytdlp(
                server.url,
                self.output,
                progress_callback=on_progress,
                label="Heart of the Beast",
                delete_strm_on_success=False,
            )
        self.assertTrue(ok)
        with open(self.output, "rb") as handle:
            self.assertEqual(handle.read(), BODY)
        self.assertTrue(progress)
        self.assertEqual(progress[-1][0], 1.0)
        self.assertIn("100%", progress[-1][1])
        app_requests = server.seen[seen_before:]
        self.assertGreaterEqual(len(app_requests), 1)
        for headers in app_requests:
            self.assertEqual(headers.get("User-Agent"), core.XTREAM_HTTP_USER_AGENT)
            self.assertNotIn("Sec-Fetch-Mode", headers)
            self.assertNotIn("text/html", headers.get("Accept", ""))

    def test_resume_sends_range(self):
        server = MediaServer().start()
        self.addCleanup(server.close)
        part = self.output + ".part"
        with open(part, "wb") as handle:
            handle.write(BODY[:100])
        ok = run_ytdlp(
            server.url,
            self.output,
            resume=True,
            delete_strm_on_success=False,
        )
        self.assertTrue(ok)
        with open(self.output, "rb") as handle:
            self.assertEqual(handle.read(), BODY)
        self.assertTrue(server.ranges)
        self.assertTrue(any(item.startswith("bytes=100") for item in server.ranges))

    def test_cancel_stops_the_download(self):
        server = MediaServer().start()
        self.addCleanup(server.close)
        state = {"checks": 0}

        def should_cancel():
            state["checks"] += 1
            return state["checks"] > 1

        with self.assertRaises(DownloadCancelled):
            run_ytdlp(
                server.url,
                self.output,
                should_cancel=should_cancel,
                delete_strm_on_success=False,
            )
        self.assertFalse(os.path.isfile(self.output))
        self.assertGreater(state["checks"], 1)

    def test_forbidden_error_has_status_without_version_warning(self):
        server = MediaServer(forbid_all=True).start()
        self.addCleanup(server.close)
        with self.assertRaises(RuntimeError) as caught:
            run_ytdlp(server.url, self.output, delete_strm_on_success=False)
        message = str(caught.exception)
        self.assertIn("403", message)
        self.assertNotIn("older than 90 days", message)
        self.assertNotIn("always use the latest version", message.lower())


if __name__ == "__main__":
    unittest.main()
