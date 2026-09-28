import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

from tools.snapshot_retention import (
    PUBLIC_AGE_SECONDS, STAGING_AGE_SECONDS, apply_plan, build_plan,
)


class RetentionCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.public = self.data / "public/w/snapshots"
        self.staging = self.data / "staging/w"
        self.deliveries = self.data / "staging/.deliveries"
        self.commits = self.data / "staging/.commits"
        for path in (self.public, self.staging, self.deliveries, self.commits):
            path.mkdir(parents=True, exist_ok=True)
        self.now_ns = 2_000_000_000_000_000_000
        self.queue = {"pending_snapshots": 1, "items": [{"workspace_id": "w", "snapshot_id": "pending"}]}
        ages = {
            "current": 20_000,
            "previous-1": 2 * PUBLIC_AGE_SECONDS,
            "previous-2": 3 * PUBLIC_AGE_SECONDS,
            "old": 10 * PUBLIC_AGE_SECONDS,
            "pending": 12 * PUBLIC_AGE_SECONDS,
        }
        for sid, age in ages.items():
            self.snapshot(self.public, sid, age)
            self.commit(sid, age)
        for sid in ("current", "old", "pending"):
            self.snapshot(self.staging, sid, ages[sid])
            self.delivery(sid, ages[sid])
        self.snapshot(self.staging, "unfinished-old", STAGING_AGE_SECONDS + 3600)
        self.delivery("unfinished-old", STAGING_AGE_SECONDS + 3600)
        self.snapshot(self.staging, "unfinished-recent", STAGING_AGE_SECONDS - 3600)
        self.delivery("unfinished-recent", STAGING_AGE_SECONDS - 3600)
        (self.data / "public/w/current.json").write_text('{"snapshot_id":"current"}')
        self.touch(self.data / "public/w/current.json", 0)

    def touch(self, path, age_seconds):
        stamp = (self.now_ns - age_seconds * 1_000_000_000) / 1_000_000_000
        os.utime(path, (stamp, stamp))

    def snapshot(self, root, sid, age_seconds, body="{}"):
        target = root / sid
        target.mkdir()
        value = target / "data.json"
        value.write_text(body)
        self.touch(value, age_seconds)
        self.touch(target, age_seconds)
        return target

    def receipt(self, root, document, age_seconds):
        path = root / f"{uuid.uuid4()}.json"
        path.write_text(json.dumps(document, separators=(",", ":")))
        self.touch(path, age_seconds)
        return path

    def commit(self, sid, age_seconds):
        return self.receipt(self.commits, {
            "workspace_id": "w", "snapshot_id": sid, "body_sha256": "a" * 64,
        }, age_seconds)

    def delivery(self, sid, age_seconds):
        return self.receipt(self.deliveries, {
            "workspace_id": "w", "snapshot_id": sid,
            "relative_json_path": "data.json", "body_sha256": "b" * 64,
        }, age_seconds)

    def test_plan_keeps_current_previous_pending_and_retention_age(self):
        report, _, _ = build_plan(self.data, self.queue, now_ns=self.now_ns)
        candidates = {(item["kind"], item["area"], item["snapshot_id"]) for item in report["candidates"]}
        self.assertIn(("snapshot", "public", "old"), candidates)
        self.assertIn(("snapshot", "staging", "old"), candidates)
        self.assertIn(("snapshot", "staging", "unfinished-old"), candidates)
        for sid in ("current", "previous-1", "previous-2", "pending", "unfinished-recent"):
            self.assertNotIn(("snapshot", "public", sid), candidates)
            self.assertNotIn(("snapshot", "staging", sid), candidates)
        protected = {(item.get("area"), item.get("snapshot_id"), item["reason"]) for item in report["protected"]}
        self.assertIn(("public", "current", "current"), protected)
        self.assertIn(("staging", "pending", "pending"), protected)

    def test_related_and_old_orphan_receipts_precede_snapshot_deletion(self):
        orphan = self.receipt(self.deliveries, {
            "workspace_id": "w", "snapshot_id": "orphan",
            "relative_json_path": "data.json", "body_sha256": "c" * 64,
        }, STAGING_AGE_SECONDS + 3600)
        report, _, _ = build_plan(self.data, self.queue, now_ns=self.now_ns)
        kinds = [item["kind"] for item in report["candidates"]]
        first_snapshot = kinds.index("snapshot")
        self.assertTrue(all(kind == "receipt" for kind in kinds[:first_snapshot]))
        self.assertTrue(any(item["kind"] == "receipt" and item["snapshot_id"] == "orphan"
                            for item in report["candidates"]))
        self.assertTrue(orphan.is_file())

    def test_apply_removes_expired_data_and_receipts_only(self):
        orphan_old = self.receipt(self.commits, {
            "workspace_id": "w", "snapshot_id": "orphan-old", "body_sha256": "d" * 64,
        }, STAGING_AGE_SECONDS + 3600)
        orphan_recent = self.receipt(self.commits, {
            "workspace_id": "w", "snapshot_id": "orphan-recent", "body_sha256": "e" * 64,
        }, STAGING_AGE_SECONDS - 3600)
        report, _, _ = build_plan(self.data, self.queue, now_ns=self.now_ns)
        result = apply_plan(self.data, self.queue, report)
        self.assertEqual("completed", result["state"])
        self.assertFalse((self.public / "old").exists())
        self.assertFalse((self.staging / "old").exists())
        self.assertFalse((self.staging / "unfinished-old").exists())
        self.assertFalse(orphan_old.exists())
        self.assertTrue(orphan_recent.is_file())
        for sid in ("current", "previous-1", "previous-2", "pending"):
            self.assertTrue((self.public / sid).is_dir())
        self.assertTrue((self.staging / "current").is_dir())
        self.assertTrue((self.staging / "pending").is_dir())
        self.assertTrue((self.staging / "unfinished-recent").is_dir())

    def test_current_change_after_dry_run_aborts(self):
        report, _, _ = build_plan(self.data, self.queue, now_ns=self.now_ns)
        (self.data / "public/w/current.json").write_text('{"snapshot_id":"old"}')
        with self.assertRaisesRegex(ValueError, "candidate_set_changed"):
            apply_plan(self.data, self.queue, report)
        self.assertTrue((self.public / "old").is_dir())

    def test_future_activity_is_deferred(self):
        future = self.snapshot(self.staging, "future", -400)
        report, _, _ = build_plan(self.data, self.queue, now_ns=self.now_ns)
        self.assertIn("future", {item.get("snapshot_id") for item in report["deferred"]
                                  if item.get("reason") == "future_activity"})
        self.assertTrue(future.is_dir())

    def test_unknown_workspace_data_and_receipts_are_deferred(self):
        unknown = self.data / "staging/unknown/snapshot-x"
        unknown.mkdir(parents=True)
        (unknown / "data.json").write_text('{}')
        receipt = self.receipt(self.deliveries, {
            "workspace_id": "unknown", "snapshot_id": "snapshot-x",
            "relative_json_path": "data.json", "body_sha256": "f" * 64,
        }, STAGING_AGE_SECONDS + 3600)
        report, _, _ = build_plan(self.data, self.queue, now_ns=self.now_ns)
        self.assertFalse(any(item.get("workspace") == "unknown" for item in report["candidates"]))
        self.assertTrue(unknown.is_dir())
        self.assertTrue(receipt.is_file())

    def test_invalid_receipt_aborts_without_deletion(self):
        (self.commits / "not-a-uuid.json").write_text('{}')
        with self.assertRaisesRegex(ValueError, "invalid_receipt"):
            build_plan(self.data, self.queue, now_ns=self.now_ns)
        self.assertTrue((self.public / "old").is_dir())


if __name__ == "__main__":
    unittest.main()