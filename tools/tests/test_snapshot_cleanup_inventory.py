import json
from pathlib import Path
import tempfile
import unittest
import io
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from tools.snapshot_cleanup_inventory import inspect, main, Progress


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'staging/.commits').mkdir(parents=True)
        self.queue = {'pending_snapshots': 1, 'items': [{'workspace_id': 'w', 'snapshot_id': 'pending'}]}
        for sid in ('current', 'old', 'pending', 'unfinished', 'mismatch'):
            for area in ('public/w/snapshots', 'staging/w'):
                p = self.root / area / sid
                p.mkdir(parents=True)
                (p / 'data.json').write_text('{}')
            if sid != 'unfinished':
                (self.root / 'staging/.commits' / sid).write_text(json.dumps(dict(workspace_id='w', snapshot_id=sid, body_sha256='a'*64)))
        (self.root / 'public/w/current.json').write_text('{"snapshot_id":"current"}')
        (self.root / 'staging/w/mismatch/data.json').write_text('{"changed":true}')

    def test_candidates_and_no_mutation(self):
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result = inspect(self.root, self.queue)
        self.assertEqual({(x['area'], x['snapshot_id']) for x in result['candidates']},
                         {('public', 'old'), ('staging', 'old')})
        self.assertEqual(result['logical_bytes'], 4)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_current_and_retry_queue_are_protected_in_both_areas(self):
        result = inspect(self.root, self.queue)
        protected = {(item["area"], item["snapshot_id"])
                     for item in result["protected"] if "area" in item}
        self.assertIn(("public", "current"), protected)
        self.assertIn(("staging", "current"), protected)
        self.assertIn(("public", "pending"), protected)
        self.assertIn(("staging", "pending"), protected)

    def test_receive_in_progress_without_commit_is_deferred(self):
        receiving = self.root / "staging/w/receiving"
        receiving.mkdir()
        (receiving / "data.json").write_text('{"partial":true}')
        result = inspect(self.root, self.queue)
        self.assertNotIn("receiving", {item["snapshot_id"] for item in result["candidates"]})
        self.assertIn(
            ("staging", "receiving", "commit_not_confirmed"),
            {(item.get("area"), item.get("snapshot_id"), item["reason"]) for item in result["deferred"]},
        )
        self.assertTrue(receiving.is_dir())

    def test_current_change_during_inventory_aborts(self):
        current = self.root / "public/w/current.json"

        def change_at_recheck(stage):
            if stage == "rechecking current pointers":
                current.write_text('{"snapshot_id":"old"}')

        with self.assertRaisesRegex(ValueError, "current_changed"):
            inspect(self.root, self.queue, change_at_recheck)
        self.assertTrue((self.root / "public/w/snapshots/current").is_dir())
        self.assertTrue((self.root / "public/w/snapshots/old").is_dir())

    def test_cli_progress_keeps_json_clean(self):
        queue = self.root / 'queue.json'
        queue.write_text(json.dumps(self.queue))
        out, err = io.StringIO(), io.StringIO()
        with patch('sys.argv', ['inventory', '--data', str(self.root), '--queue-status', str(queue), '--writers-stopped']), redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(out.getvalue())['candidate_count'], 2)
        self.assertIn('[running', err.getvalue())
        self.assertIn('[completed]', err.getvalue())

    def test_heartbeat_shows_stage(self):
        err = io.StringIO()
        with redirect_stderr(err):
            progress = Progress()
            progress.update('checking public/w/old')
            progress.emit()
        self.assertIn('checking public/w/old', err.getvalue())

    def test_missing_current_fails(self):
        (self.root / 'public/w/current.json').write_text('{"snapshot_id":"missing"}')
        with self.assertRaises(ValueError):
            inspect(self.root, self.queue)

    def test_bad_queue_fails(self):
        self.queue['pending_snapshots'] = 0
        with self.assertRaises(ValueError):
            inspect(self.root, self.queue)

    def test_invalid_receipt_fails(self):
        (self.root / 'staging/.commits/old').write_text('{}')
        with self.assertRaises(ValueError):
            inspect(self.root, self.queue)


if __name__ == '__main__':
    unittest.main()
