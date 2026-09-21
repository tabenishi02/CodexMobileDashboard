import json
from pathlib import Path
import tempfile
import unittest

from tools.snapshot_cleanup_apply import apply_cleanup, candidate_signature
from tools.snapshot_cleanup_inventory import inspect


class CleanupApplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "staging/.commits").mkdir(parents=True)
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

    def test_invalid_approved_report_rejected(self):
        with self.assertRaisesRegex(ValueError, "approved_report_invalid"):
            candidate_signature({"dry_run": False, "candidates": []})


if __name__ == "__main__":
    unittest.main()
