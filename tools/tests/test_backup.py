import configparser
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
import zipfile

from tools import backup
from tools.https_sender import SnapshotUpload, new_delivery_id
from tools.pending_snapshot_queue import PendingSnapshotQueue
from tools.collector_state import CollectorState, save_collector_state, load_collector_state
from tools.collector_history import CollectorHistory, save_collector_history
from tools.inference_ledger import load as load_ledger, append as append_ledger, InferenceLedgerEntry


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        (self.data / 'workspace-1').mkdir(parents=True)
        (self.data / 'workspace-1' / 'metadata.json').write_text('{"snapshot_id":"snapshot-1"}')
        self.state = self.root / 'state.json'
        save_collector_state(CollectorState(), self.state)
        self.history = self.root / 'history.json'
        save_collector_history(CollectorHistory(), self.history)
        self.ledger = self.root / 'ledger.json'
        append_ledger(self.ledger, InferenceLedgerEntry('workspace-1', 'session-1', 'turn-1', 'a'*64, {'cached': True}, '2026-09-10T00:00:00Z', 'decision'))
        self.token = self.root / 'sender.token'
        self.token.write_text('TOP-SECRET-DO-NOT-LOG')
        (self.root / 'ca.crt').write_text('test certificate')
        self.queue = self.root / 'queue'
        self.queued = PendingSnapshotQueue(self.queue).enqueue('workspace-1', 'snapshot-1',
            [SnapshotUpload('metadata.json', b'{"test":1}', new_delivery_id())], commit_delivery_id=new_delivery_id())
        self.collector = self.root / 'collector.ini'
        c = configparser.ConfigParser(interpolation=None)
        c['discovery'] = dict(allowed_roots=str(self.root / 'repos'), sessions_dir=str(self.root / 'sessions'), archived_sessions_dir=str(self.root / 'archive'))
        c['sender'] = dict(base_url='https://example.test', token_file=str(self.token), ca_file=str(self.root / 'ca.crt'))
        c['storage'] = dict(state_file=str(self.state), history_file=str(self.history), inference_ledger_file=str(self.ledger), output_dir=str(self.data), queue_dir=str(self.queue), workspace_registry=str(self.root / 'registry.json'))
        with self.collector.open('w') as f: c.write(f)
        self.config = self.root / 'backup.ini'
        self.output = self.data / 'backups'
        b = configparser.ConfigParser(interpolation=None)
        b['backup'] = dict(collector_config=str(self.collector), output_dir=str(self.output), tls_sources='', minimum_free_bytes='0', mutex_wait_seconds='1', task_names='')
        with self.config.open('w') as f: b.write(f)

    def create(self):
        plan = backup.select_pc(self.config)
        result = backup.create_pc_zip(plan, backup.new_backup_id(), [])
        return self.output / result['zip_name'], result

    def test_roundtrip_restores_queue_state_ledger_and_excludes_backups(self):
        self.output.mkdir()
        (self.output / 'old.zip').write_bytes(b'old-backup')
        (self.data / 'server.pid').write_text('123')
        (self.data / 'incomplete.json.partial').write_text('secret')
        archive, result = self.create()
        manifest = backup.verify_zip(archive)
        self.assertEqual('incomplete', result['pair_state'])
        self.assertEqual('not_run', result['android_result'])
        self.assertEqual(backup.digest(archive), result['zip_sha256'])
        self.assertFalse(Path(str(archive) + '.partial').exists())
        self.assertEqual(b'old-backup', (self.output / 'old.zip').read_bytes())
        self.assertNotIn('TOP-SECRET', json.dumps(manifest))
        self.assertEqual(['workspace-1'], manifest['workspaces'])
        names = [f['path'] for f in manifest['files']]
        self.assertFalse(any(n.endswith(('.pid', '.partial', '.zip')) for n in names))
        restored = self.root / 'restored'
        with zipfile.ZipFile(archive) as z:
            z.extractall(restored)  # Only our verified archive into a fresh temporary directory.
        queued = PendingSnapshotQueue(restored / 'payload/queue').pending()[0]
        self.assertEqual(self.queued.commit_delivery_id, queued.commit_delivery_id)
        self.assertEqual(self.queued.uploads, queued.uploads)
        self.assertEqual(CollectorState(), load_collector_state(restored / 'payload/state/state.json'))
        self.assertEqual(load_ledger(self.ledger), load_ledger(restored / 'payload/ledger/ledger.json'))
        self.assertEqual(self.token.read_bytes(), (restored / 'payload/token/sender.token').read_bytes())

    def test_full_uses_recovery_pc_targets_and_writes_full_manifest(self):
        recovery = backup.select_pc(self.config)
        full = backup.select_pc(self.config, mode='full')
        self.assertEqual(
            [(target['label'], target['source']) for target in recovery['targets']],
            [(target['label'], target['source']) for target in full['targets']],
        )
        result = backup.create_pc_zip(full, backup.new_backup_id(), [])
        archive = self.output / result['zip_name']
        manifest = backup.verify_zip(archive, expected_mode='full')
        self.assertEqual('full', manifest['mode'])
        self.assertEqual('full', result['mode'])
        with self.assertRaisesRegex(backup.BackupError, 'manifest_invalid'):
            backup.verify_zip(archive)

    def test_symlink_or_junction_source_is_rejected(self):
        with patch('tools.backup.linked', side_effect=lambda p: p == self.token):
            with self.assertRaisesRegex(backup.BackupError, 'linked_source'):
                backup.select_pc(self.config)

    def test_payload_hash_change_is_rejected(self):
        archive, _ = self.create()
        altered = self.root / 'altered.zip'
        with zipfile.ZipFile(archive) as source, zipfile.ZipFile(altered, 'w') as target:
            for name in source.namelist():
                target.writestr(name, b'changed' if name == 'payload/token/sender.token' else source.read(name))
        with self.assertRaisesRegex(backup.BackupError, 'hash_mismatch'):
            backup.verify_zip(altered)

    def test_task_export_is_read_only_and_reports_absent_task(self):
        with patch('tools.backup.subprocess.run', return_value=type('Result', (), {'returncode': 0, 'stdout': b'{}'})()) as run:
            self.assertEqual([{}], backup.task_definitions(['a-task']))
            self.assertNotIn('Disable-ScheduledTask', repr(run.call_args))
            self.assertIn('Export-ScheduledTask', repr(run.call_args))

    def test_missing_required_and_corrupt_state_fail(self):
        self.state.write_text('{broken')
        with self.assertRaises(ValueError): backup.select_pc(self.config)
        self.state.unlink()
        self.token.unlink()
        with self.assertRaisesRegex(backup.BackupError, 'required_source_missing'): backup.select_pc(self.config)

    def test_corrupt_queue_fails(self):
        (self.queued.item_directory / 'files/metadata.json').write_bytes(b'changed')
        with self.assertRaises(ValueError): backup.select_pc(self.config)

    def test_history_fallback_and_absent_registry(self):
        c = backup.read_config(self.collector)
        c.remove_option('storage', 'history_file')
        with self.collector.open('w') as f: c.write(f)
        plan = backup.select_pc(self.config)
        history = next(t for t in plan['targets'] if t['label'] == 'history')
        self.assertEqual(str(self.root / 'state/collector-history.json'), history['source'])
        self.assertFalse(history['present'])

    def test_capacity_failure_keeps_existing_and_creates_no_partial(self):
        plan = backup.select_pc(self.config)
        with patch('tools.backup.shutil.disk_usage', return_value=type('Usage', (), {'free': 0})()):
            with self.assertRaisesRegex(backup.BackupError, 'capacity_insufficient'):
                backup.create_pc_zip(plan, backup.new_backup_id(), [])
        self.assertEqual([], list(self.output.iterdir()))

    def test_failed_verification_leaves_only_partial(self):
        with patch('tools.backup.verify_zip', side_effect=backup.BackupError('zip_invalid')):
            with self.assertRaises(backup.BackupError): self.create()
        self.assertEqual(1, len(list(self.output.glob('*.partial'))))
        self.assertEqual([], list(self.output.glob('*.zip')))

    def test_id_collision_never_overwrites(self):
        archive, result = self.create()
        before = archive.read_bytes()
        with self.assertRaisesRegex(backup.BackupError, 'backup_id_exists'):
            backup.create_pc_zip(backup.select_pc(self.config), result['backup_id'], [])
        self.assertEqual(before, archive.read_bytes())

    def test_corruption_and_path_traversal_detected(self):
        archive, _ = self.create()
        broken = self.root / 'broken.zip'
        broken.write_bytes(archive.read_bytes()[:50])
        with self.assertRaises(backup.BackupError): backup.verify_zip(broken)
        with zipfile.ZipFile(broken, 'w') as z: z.writestr('../outside', b'x')
        with self.assertRaises(backup.BackupError): backup.verify_zip(broken)
        with zipfile.ZipFile(archive, 'a') as z: z.writestr('unlisted.txt', 'x')
        with self.assertRaises(backup.BackupError): backup.verify_zip(archive)

    def test_source_inside_backup_rejected(self):
        c = backup.read_config(self.config)
        c['backup']['output_dir'] = str(self.root)
        with self.config.open('w') as f: c.write(f)
        with self.assertRaisesRegex(backup.BackupError, 'target_inside_backup'): backup.select_pc(self.config)

    def test_cli_failure_does_not_log_exception_or_secrets(self):
        out = io.StringIO()
        with patch('tools.backup.CollectorMutex') as mutex, patch('tools.backup.select_pc', side_effect=OSError('TOP-SECRET-DO-NOT-LOG')):
            with redirect_stdout(out): code = backup.main(['--config', str(self.config)])
            mutex.return_value.__exit__.assert_called_once()
        self.assertEqual(2, code)
        self.assertNotIn('TOP-SECRET', out.getvalue())
        self.assertIn('backup_failed', out.getvalue())

    def test_cli_success_logs_and_records_pc_only(self):
        out = io.StringIO()
        with patch('tools.backup.CollectorMutex'), redirect_stdout(out):
            self.assertEqual(0, backup.main(['--config', str(self.config)]))
        self.assertNotIn('TOP-SECRET', out.getvalue())
        self.assertIn('mutex_released', out.getvalue())
        result = json.loads(next(self.output.glob('*.PC.result.json')).read_text())
        self.assertEqual('incomplete', result['pair_state'])
        self.assertIn('pc_zip_verified', (self.output / 'backup.log').read_text())

    def test_cli_failure_persists_incomplete_and_unlocks(self):
        with patch('tools.backup.CollectorMutex') as mutex, patch('tools.backup.create_pc_zip', side_effect=backup.BackupError('capacity_insufficient')), redirect_stdout(io.StringIO()):
            self.assertEqual(2, backup.main(['--config', str(self.config)]))
            mutex.return_value.__exit__.assert_called_once()
        result = json.loads(next(self.output.glob('*.PC.failure.json')).read_text())
        self.assertEqual('incomplete', result['pair_state'])
        self.assertEqual('capacity_insufficient', result['error'])

    def test_codex_originals_are_not_accepted_as_tls_source(self):
        original = self.root / 'sessions'
        original.mkdir()
        (original / 'original.jsonl').write_text('private raw data')
        c = backup.read_config(self.config)
        c['backup']['tls_sources'] = str(original)
        with self.config.open('w') as f: c.write(f)
        with self.assertRaisesRegex(backup.BackupError, 'source_scope_invalid'):
            backup.select_pc(self.config)

    def test_full_rejected_before_mutex(self):
        with patch('tools.backup.CollectorMutex') as mutex, redirect_stdout(io.StringIO()):
            self.assertEqual(2, backup.main(['--config', str(self.config), '--mode', 'full']))
            mutex.assert_not_called()


@unittest.skipUnless(os.name == 'nt', 'Windows mutex required')
class MutexTests(unittest.TestCase):
    def test_dotnet_mutex_blocks_backup_then_releases(self):
        name = 'Local\\CodexMobileDashboard-Test-' + uuid.uuid4().hex
        command = "$m=New-Object System.Threading.Mutex($false,$env:TEST_MUTEX); $null=$m.WaitOne(); [Console]::WriteLine('locked'); $null=[Console]::ReadLine(); $m.ReleaseMutex(); $m.Dispose()"
        process = subprocess.Popen(['powershell.exe','-NoProfile','-Command',command], env=dict(os.environ, TEST_MUTEX=name), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.assertEqual('locked', process.stdout.readline().strip())
            with self.assertRaisesRegex(backup.BackupError, 'mutex_timeout'):
                with backup.CollectorMutex(0, name): pass
            process.communicate('\n', timeout=15)
            with backup.CollectorMutex(1, name): pass
        finally:
            if process.poll() is None: process.communicate('\n', timeout=15)

    def test_abandoned_mutex_can_be_acquired(self):
        name = 'Local\\CodexMobileDashboard-Test-' + uuid.uuid4().hex
        owners = []
        def abandon():
            owner = backup.CollectorMutex(0, name)
            owner.__enter__()
            owners.append(owner)
        thread = threading.Thread(target=abandon)
        thread.start(); thread.join(10)
        try:
            with backup.CollectorMutex(0, name): pass
        finally:
            for owner in owners: owner.api.CloseHandle(owner.handle)

    def test_exception_releases_mutex_for_other_thread(self):
        name = 'Local\\CodexMobileDashboard-Test-' + uuid.uuid4().hex
        with self.assertRaises(RuntimeError):
            with backup.CollectorMutex(1, name): raise RuntimeError('test')
        results = []
        def acquire():
            with backup.CollectorMutex(0, name): results.append(True)
        thread = threading.Thread(target=acquire)
        thread.start(); thread.join(10)
        self.assertEqual([True], results)
