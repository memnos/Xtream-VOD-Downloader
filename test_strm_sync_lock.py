import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from core import default_strm_sync_status
import strm_sync


class SyncLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.pid_file = os.path.join(self.tmp.name, "strm_sync.pid")
        self.status = default_strm_sync_status()
        self.status["running"] = True
        self.status["heartbeat_unix"] = time.time()
        self.patches = [
            mock.patch.object(strm_sync, "SYNC_PID_FILE", self.pid_file),
            mock.patch.object(strm_sync, "load_strm_sync_status", side_effect=lambda: self.status),
            mock.patch.object(strm_sync, "save_strm_sync_status", side_effect=self._save),
            mock.patch.object(strm_sync, "_sync_thread", None),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def _save(self, data: dict) -> None:
        self.status = data

    def _write_legacy_pid(self, pid: int) -> None:
        with open(self.pid_file, "w", encoding="utf-8") as handle:
            handle.write(str(pid))

    def _write_record(self, pid: int, starttime: str, boot_id: str = "") -> None:
        with open(self.pid_file, "w", encoding="utf-8") as handle:
            json.dump({"pid": pid, "starttime": starttime, "boot_id": boot_id}, handle)

    def test_reused_own_pid_is_not_alive(self):
        """Container restart often reuses watcher PID 12; that must not keep the lock."""
        self._write_legacy_pid(os.getpid())
        self.assertFalse(strm_sync._sync_pid_alive())
        self.assertTrue(strm_sync.clear_stale_sync_running(reason="test"))
        self.assertFalse(self.status["running"])
        self.assertFalse(os.path.exists(self.pid_file))
        self.assertIn("Cleared stale running=True", self.status["log"][-1])

    def test_dead_pid_clears_stale_running(self):
        self._write_legacy_pid(2**22)
        self.assertFalse(strm_sync._sync_pid_alive())
        self.assertTrue(strm_sync.clear_stale_sync_running(reason="test"))
        self.assertFalse(self.status["running"])

    def test_other_process_with_matching_starttime_is_alive(self):
        pid = 1
        starttime = strm_sync._process_starttime(pid)
        if not starttime:
            self.skipTest("/proc/1/stat not readable")
        self._write_record(pid, starttime, strm_sync._boot_id())
        self.assertTrue(strm_sync._sync_pid_alive())
        self.assertFalse(strm_sync.clear_stale_sync_running())
        self.assertTrue(self.status["running"])

    def test_reused_foreign_pid_wrong_starttime_is_stale(self):
        pid = 1
        if not strm_sync._pid_is_running(pid):
            self.skipTest("pid 1 not signalable")
        self._write_record(pid, "1", strm_sync._boot_id())
        self.assertFalse(strm_sync._sync_pid_alive())
        self.assertTrue(strm_sync.clear_stale_sync_running(reason="test"))
        self.assertFalse(self.status["running"])

    def test_legacy_pid_file_requires_fresh_heartbeat(self):
        self._write_legacy_pid(1)
        if not strm_sync._pid_is_running(1):
            self.skipTest("pid 1 not signalable")
        self.status["heartbeat_unix"] = time.time()
        self.assertTrue(strm_sync._sync_pid_alive())
        self.status["heartbeat_unix"] = time.time() - 1000
        self.assertFalse(strm_sync._sync_pid_alive())
        self.status["heartbeat_unix"] = 0
        self.assertFalse(strm_sync._sync_pid_alive())

    def test_write_sync_pid_stores_identity(self):
        strm_sync._write_sync_pid()
        with open(self.pid_file, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["pid"], os.getpid())
        self.assertTrue(record["starttime"])
        self.assertEqual(record["starttime"], strm_sync._process_starttime(os.getpid()))

    def test_is_strm_sync_running_clears_orphan(self):
        self._write_legacy_pid(os.getpid())
        self.assertFalse(strm_sync.is_strm_sync_running())
        self.assertFalse(self.status["running"])
        self.assertFalse(os.path.exists(self.pid_file))

    def test_start_strm_sync_does_not_deadlock(self):
        self.status["running"] = False
        result: dict = {}

        def _fake_run(*_args, **_kwargs):
            time.sleep(0.01)

        def _call() -> None:
            with mock.patch.object(strm_sync, "run_strm_sync", _fake_run):
                result["ok"] = strm_sync.start_strm_sync("h", "u", "p", {})

        worker = threading.Thread(target=_call)
        worker.start()
        worker.join(2.0)
        self.assertFalse(worker.is_alive(), "start_strm_sync deadlocked")
        self.assertTrue(result.get("ok"))

    def test_heartbeat_is_stale_without_timestamp(self):
        self.status["heartbeat_unix"] = 0
        self.assertTrue(strm_sync._heartbeat_is_stale(self.status))
        self.status["heartbeat_unix"] = time.time()
        self.assertFalse(strm_sync._heartbeat_is_stale(self.status))


if __name__ == "__main__":
    unittest.main()
