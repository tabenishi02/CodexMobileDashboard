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
