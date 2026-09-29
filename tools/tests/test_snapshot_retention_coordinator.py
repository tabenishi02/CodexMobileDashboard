import configparser
import json
from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.snapshot_retention_coordinator import (
    RetentionCoordinatorError, _remote_cleanup, _safe_remote_path, main,
)


class SnapshotRetentionCoordinatorTests(unittest.TestCase):
    def config(self):
        value = configparser.ConfigParser(interpolation=None)
        value.read_dict({
            "backup": {"collector_config": "collector.ini", "mutex_wait_seconds": "3"},
            "android": {
                "ssh_host": "dashboard-android",
                "repository": "/data/data/com.termux/files/home/CodexMobileDashboard/app",
                "full_wait_seconds": "100",
            },
        })
        return value

    def test_remote_cleanup_runs_dry_run_then_reviewed_apply(self):
        completed = json.dumps({"state": "completed", "policy": "retention-v1",
                                "removed_count": 4}).encode()
        replies = [Mock(returncode=0, stdout=b""), Mock(returncode=0, stdout=b""),
                   Mock(returncode=0, stdout=completed)]
        with tempfile.TemporaryDirectory() as directory:
            queue = Path(directory) / "queue.json"
            queue.write_text("{}", encoding="utf-8")
            with patch("tools.snapshot_retention_coordinator.subprocess.run", side_effect=replies) as run:
                result = _remote_cleanup(self.config(), queue, "20260929T000000Z-12345678")
        self.assertEqual(4, result["removed_count"])
        command = run.call_args_list[2].args[0][-1]
        self.assertLess(command.index("--dry-run"), command.index("--apply"))
        self.assertIn("--approved-report \"$report\"", command)
        self.assertIn("backup.lock", command)
        self.assertIn("trap 'rm -f", command)

    def test_rejects_unsafe_remote_repository(self):
        for value in ("relative/app", "/safe/../app", "/safe/app;bad"):
            with self.subTest(value=value):
                with self.assertRaises(RetentionCoordinatorError):
                    _safe_remote_path(value)

    def test_main_records_completed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            runtime = Mock(state_file=state)
            remote = {"state": "completed", "policy": "retention-v1",
                      "removed_count": 1, "removed_file_count": 2,
                      "removed_logical_bytes": 3, "protected_current_count": 1}
            with patch("tools.snapshot_retention_coordinator.read_config", return_value=self.config()), \
                 patch("tools.snapshot_retention_coordinator._load_settings", return_value={}), \
                 patch("tools.snapshot_retention_coordinator._runtime_settings", return_value=runtime), \
                 patch("tools.snapshot_retention_coordinator._queue_status", return_value={"pending_snapshots": 0, "items": []}), \
                 patch("tools.snapshot_retention_coordinator._remote_cleanup", return_value=remote), \
                 patch("tools.snapshot_retention_coordinator.CollectorMutex"):
                self.assertEqual(0, main(["--config", "backup.ini"]))
            saved = json.loads((state.parent / "snapshot-retention-result.json").read_text(encoding="utf-8"))
            self.assertEqual("completed", saved["state"])
            self.assertEqual(1, saved["removed_count"])

    def test_hidden_launcher_uses_coordinator_without_console(self):
        launcher = Path(__file__).resolve().parents[2] / "scripts/run_snapshot_retention_hidden.pyw"
        entry = runpy.run_path(str(launcher))["main"]
        with patch("subprocess.run", return_value=Mock(returncode=2)) as run, \
             patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True):
            self.assertEqual(2, entry(["--python", "python.exe", "--config", "backup config.ini"]))
        self.assertEqual(
            ["python.exe", "-m", "tools.snapshot_retention_coordinator", "--config", "backup config.ini"],
            run.call_args.args[0],
        )
        self.assertEqual(0x08000000, run.call_args.kwargs["creationflags"])


if __name__ == "__main__":
    unittest.main()