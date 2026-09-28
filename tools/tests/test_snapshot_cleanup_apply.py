import json
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.snapshot_cleanup_apply import acquire_exclusive_lock, apply_cleanup, candidate_signature, execute_cleanup
from tools.snapshot_cleanup_inventory import inspect


class CleanupApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "staging/.commits").mkdir(parents=True)
        (self.root / "staging/.deliveries").mkdir()
        (self.root / "staging/.deliveries/retry.json").write_text('{"receipt":"preserve"}')
        self.queue = {"pending_snapshots": 1, "items": [{"workspace_id": "w", "snapshot_id": "pending"}]}
        for sid in ("current", "old", "pending"):
            for area in ("public/w/snapshots", "staging/w"):
                target = self.root / area / sid
                target.mkdir(parents=True)
                (target / "data.json").write_text("{}")
            (self.root / "staging/.commits" / sid).write_text(json.dumps({
                "workspace_id": "w", "snapshot_id": sid, "body_sha256": "a" * 64,
            }))
        (self.root / "public/w/current.json").write_text('{"snapshot_id":"current"}')

    def test_removes_only_approved_old_pair(self):
        approved = inspect(self.root, self.queue)
        result = apply_cleanup(self.root, self.queue, approved)
        self.assertEqual(result["removed_count"], 2)
        self.assertFalse((self.root / "public/w/snapshots/old").exists())
        self.assertFalse((self.root / "staging/w/old").exists())
        self.assertTrue((self.root / "public/w/snapshots/current").is_dir())
        self.assertTrue((self.root / "staging/w/current").is_dir())
        self.assertTrue((self.root / "public/w/snapshots/pending").is_dir())
        self.assertTrue((self.root / "staging/w/pending").is_dir())

    def test_changed_candidate_set_aborts_without_deletion(self):
        approved = inspect(self.root, self.queue)
        changed = self.root / "public/w/snapshots/new"
        changed.mkdir()
        (changed / "data.json").write_text("{}")
        (self.root / "staging/w/new").mkdir()
        (self.root / "staging/w/new/data.json").write_text("{}")
        (self.root / "staging/.commits/new").write_text(json.dumps({
            "workspace_id": "w", "snapshot_id": "new", "body_sha256": "b" * 64,
        }))
        with self.assertRaisesRegex(ValueError, "candidate_set_changed"):
            apply_cleanup(self.root, self.queue, approved)
        self.assertTrue((self.root / "public/w/snapshots/old").is_dir())

    def test_new_retry_queue_after_dry_run_aborts_without_deletion(self):
        approved = inspect(self.root, self.queue)
        queue = {
            "pending_snapshots": 2,
            "items": self.queue["items"] + [{
                "workspace_id": "w", "snapshot_id": "old",
                "last_error_kind": "network", "last_error_status": None,
            }],
        }
        with self.assertRaisesRegex(ValueError, "candidate_set_changed"):
            apply_cleanup(self.root, queue, approved)
        self.assertTrue((self.root / "public/w/snapshots/old").is_dir())
        self.assertTrue((self.root / "staging/w/old").is_dir())

    def test_current_switch_after_dry_run_aborts_without_deletion(self):
        approved = inspect(self.root, self.queue)
        (self.root / "public/w/current.json").write_text('{"snapshot_id":"old"}')
        with self.assertRaisesRegex(ValueError, "candidate_set_changed"):
            apply_cleanup(self.root, self.queue, approved)
        self.assertTrue((self.root / "public/w/snapshots/old").is_dir())
        self.assertTrue((self.root / "staging/w/old").is_dir())

    def test_receive_in_progress_after_dry_run_is_preserved(self):
        approved = inspect(self.root, self.queue)
        receiving = self.root / "staging/w/receiving"
        receiving.mkdir()
        (receiving / "data.json").write_text('{"partial":true}')
        result = apply_cleanup(self.root, self.queue, approved)
        self.assertEqual(2, result["removed_count"])
        self.assertTrue(receiving.is_dir())
        self.assertEqual('{"partial":true}', (receiving / "data.json").read_text())

    def test_current_change_immediately_before_delete_aborts(self):
        approved = inspect(self.root, self.queue)
        current = self.root / "public/w/current.json"

        def change_before_delete(stage):
            if stage.startswith("deleting 1/"):
                current.write_text('{"snapshot_id":"old"}')

        with self.assertRaisesRegex(RuntimeError, "current_changed_during_cleanup"):
            apply_cleanup(self.root, self.queue, approved, change_before_delete)
        self.assertTrue((self.root / "public/w/snapshots/old").is_dir())
        self.assertTrue((self.root / "staging/w/old").is_dir())

    def test_partial_delete_failure_preserves_current_pending_and_receipts(self):
        approved = inspect(self.root, self.queue)
        real_rmtree = shutil.rmtree
        calls = 0

        def fail_second(target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated cleanup failure")
            real_rmtree(target)

        with patch("tools.snapshot_cleanup_apply.shutil.rmtree", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated cleanup failure"):
                apply_cleanup(self.root, self.queue, approved)
        self.assertFalse((self.root / "public/w/snapshots/old").exists())
        self.assertTrue((self.root / "staging/w/old").is_dir())
        for sid in ("current", "pending"):
            self.assertTrue((self.root / f"public/w/snapshots/{sid}").is_dir())
            self.assertTrue((self.root / f"staging/w/{sid}").is_dir())
        self.assertTrue((self.root / "staging/.commits/current").is_file())
        self.assertTrue((self.root / "staging/.commits/pending").is_file())
        self.assertTrue((self.root / "staging/.deliveries/retry.json").is_file())

    def test_exclusive_lock_reports_maintenance_busy(self):
        handle = Mock()
        handle.fileno.return_value = 10
        lock_api = Mock(LOCK_EX=2, LOCK_NB=4)
        lock_api.flock.side_effect = BlockingIOError()
        with self.assertRaisesRegex(RuntimeError, "maintenance_busy"):
            acquire_exclusive_lock(handle, lock_api)
        lock_api.flock.assert_called_once_with(10, 6)

    def test_dry_run_does_not_delete(self):
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        report = execute_cleanup(self.root, self.queue, apply=False)
        self.assertTrue(report["dry_run"])
        self.assertEqual(2, report["candidate_count"])
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_content_change_after_rescan_aborts_before_deletion(self):
        approved = inspect(self.root, self.queue)

        def inspect_then_change(*args, **kwargs):
            fresh = inspect(*args, **kwargs)
            (self.root / "public/w/snapshots/old/data.json").write_text('{"changed":true}')
            return fresh

        with patch("tools.snapshot_cleanup_apply.inspect", side_effect=inspect_then_change):
            with self.assertRaisesRegex(RuntimeError, "candidate_content_changed"):
                apply_cleanup(self.root, self.queue, approved)
        self.assertTrue((self.root / "public/w/snapshots/old").is_dir())
        self.assertTrue((self.root / "staging/w/old").is_dir())

    def test_invalid_approved_report_rejected(self):
        with self.assertRaisesRegex(ValueError, "approved_report_invalid"):
            candidate_signature({"dry_run": False, "candidates": []})


if __name__ == "__main__":
    unittest.main()
