import configparser
from contextlib import redirect_stdout
import io
import json
import os
import ssl
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch
import zipfile

from server import backup_worker as worker
from tools.backup import BackupError, new_backup_id, verify_zip


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'backups'
        self.output.mkdir()
        self.public = self.root / 'public'
        for snapshot in ('old', 'current'):
            path = self.public / 'workspace-1' / 'snapshots' / snapshot
            path.mkdir(parents=True)
            (path / 'metadata.json').write_text(json.dumps({'snapshot_id': snapshot}))
        (self.public / 'workspace-1/current.json').write_text('{"snapshot_id":"current"}')
        self.boot = self.root / 'boot'
        self.boot.mkdir()
        (self.boot / 'custom-start').write_text('termux-wake-lock\n')
        self.config = self.root / 'server.ini'
        for name in ('server.token', 'server.crt', 'server.key'):
            (self.root / name).write_text('DO-NOT-LOG-SECRET')
        c = configparser.ConfigParser()
        c['server'] = dict(host='0.0.0.0', port='8765', certificate_file=str(self.root/'server.crt'), private_key_file=str(self.root/'server.key'), static_directory=str(self.root/'client'), public_directory=str(self.public), staging_directory=str(self.root/'staging'))
        c['auth'] = dict(token_file=str(self.root/'server.token'))
        with self.config.open('w') as stream: c.write(stream)
        self.request = dict(backup_id=new_backup_id(), config=str(self.config), output=str(self.output), boot=str(self.boot), reserve=0, health_url='https://localhost:8765/health', ca=str(self.root/'ca.crt'))
        self.job = self.output / self.request['backup_id']
        self.job.mkdir()
        self.control = Mock()
        self.control.running.return_value = True

    def run_worker(self):
        return worker.run_worker(self.request, self.job, self.control)

    def test_current_only_roundtrip_and_restart(self):
        result = self.run_worker()
        self.assertEqual('success', result['android_result'])
        self.assertEqual('success', result['restart_result'])
        self.assertEqual('incomplete', result['pair_state'])
        archive = self.output / result['zip_name']
        manifest = verify_zip(archive, expected_side='Android')
        self.assertEqual([{'workspace_id':'workspace-1','snapshot_id':'current'}], manifest['workspaces'])
        names = [f['path'] for f in manifest['files']]
        self.assertFalse(any('old' in n or 'staging' in n for n in names))
        restored = self.root / 'restore'
        with zipfile.ZipFile(archive) as z: z.extractall(restored)
        self.assertEqual('current', json.loads((restored/'payload/current_workspace-1/current.json').read_text())['snapshot_id'])
        self.assertEqual('current', json.loads((restored/'payload/snapshot_workspace-1/metadata.json').read_text())['snapshot_id'])
        self.control.stop.assert_called_once()
        self.control.resume.assert_called_once()
        self.assertNotIn('DO-NOT-LOG', (self.job/'events.jsonl').read_text())
        self.assertFalse(list(self.output.glob('*.partial')))

    def test_full_saves_all_public_staging_and_receipts(self):
        staging = self.root / 'staging'
        (staging / 'workspace-1/pending').mkdir(parents=True)
        (staging / 'workspace-1/pending/metadata.json').write_text(
            '{"snapshot_id":"pending"}'
        )
        (staging / '.deliveries').mkdir()
        (staging / '.deliveries/delivery.json').write_text(json.dumps({
            'workspace_id': 'workspace-1',
            'snapshot_id': 'pending',
            'relative_json_path': 'metadata.json',
            'body_sha256': 'a' * 64,
        }))
        (staging / '.commits').mkdir()
        (staging / '.commits/commit.json').write_text(json.dumps({
            'workspace_id': 'workspace-1',
            'snapshot_id': 'current',
            'body_sha256': 'b' * 64,
        }))
        plan = worker.select_android(self.config, self.output, self.boot, mode='full')
        result = worker.write_zip(plan, self.output, self.request['backup_id'])
        archive = self.output / result['zip_name']
        manifest = verify_zip(archive, expected_side='Android', expected_mode='full')
        names = {entry['path'] for entry in manifest['files']}
        self.assertIn('payload/public/workspace-1/snapshots/old/metadata.json', names)
        self.assertIn('payload/public/workspace-1/snapshots/current/metadata.json', names)
        self.assertIn('payload/staging/workspace-1/pending/metadata.json', names)
        self.assertIn('payload/staging/.deliveries/delivery.json', names)
        self.assertIn('payload/staging/.commits/commit.json', names)
        self.assertNotIn('staging', manifest['excluded'])
        self.assertNotIn('receipts', manifest['excluded'])
        with self.assertRaisesRegex(BackupError, 'manifest_invalid'):
            verify_zip(archive, expected_side='Android')

    def test_full_requires_staging_root(self):
        with self.assertRaisesRegex(BackupError, 'required_source_missing'):
            worker.select_android(self.config, self.output, self.boot, mode='full')

    def test_health_context_disables_python_314_strict_x509_only(self):
        context = Mock(verify_flags=ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_TRUSTED_FIRST)
        with patch.object(worker.ssl, 'create_default_context', return_value=context) as create:
            self.assertIs(context, worker.health_context(self.root/'ca.crt'))
        create.assert_called_once_with(cafile=str(self.root/'ca.crt'))
        self.assertFalse(context.verify_flags & ssl.VERIFY_X509_STRICT)
        self.assertTrue(context.verify_flags & ssl.VERIFY_X509_TRUSTED_FIRST)

    def test_restore_server_signals_resets_worker_ignored_signals(self):
        with (patch.object(worker.signal, 'SIGHUP', 1, create=True),
              patch.object(worker.signal, 'signal') as set_signal):
            worker.restore_server_signals()
        set_signal.assert_has_calls([
            call(worker.signal.SIGTERM, worker.signal.SIG_DFL),
            call(1, worker.signal.SIG_DFL),
        ])

    def test_deployment_without_git_records_code_fingerprint(self):
        with patch.object(worker.subprocess, 'check_output', side_effect=FileNotFoundError()):
            result = self.run_worker()
        self.assertEqual('success', result['android_result'])
        manifest = verify_zip(self.output/result['zip_name'], expected_side='Android')
        self.assertIsNone(manifest['git_commit'])
        self.assertTrue(manifest['git_dirty'])
        self.assertRegex(manifest['source_fingerprint'], r'^[0-9a-f]{64}$')

    def test_dotted_snapshot_id_matches_existing_server_contract(self):
        old = self.public/'workspace-1/snapshots/current'
        old.rename(old.with_name('snapshot.1'))
        (self.public/'workspace-1/current.json').write_text('{"snapshot_id":"snapshot.1"}')
        result = self.run_worker()
        self.assertEqual('success', result['android_result'])
        manifest = verify_zip(self.output/result['zip_name'], expected_side='Android')
        self.assertEqual('snapshot.1', manifest['workspaces'][0]['snapshot_id'])

    def test_originally_stopped_stays_stopped(self):
        self.control.running.return_value = False
        result = self.run_worker()
        self.assertEqual('success', result['android_result'])
        self.assertEqual('not_needed', result['restart_result'])
        self.control.stop.assert_not_called()
        self.control.resume.assert_not_called()

    def test_write_failure_still_restarts(self):
        with patch.object(worker, 'write_zip', side_effect=OSError('DO-NOT-LOG-SECRET')):
            result = self.run_worker()
        self.assertEqual('failed', result['android_result'])
        self.assertEqual('success', result['restart_result'])
        self.assertEqual(['backup_failed'], result['errors'])

    def test_restart_failure_preserves_zip_success(self):
        self.control.resume.side_effect = OSError('secret')
        result = self.run_worker()
        self.assertEqual('success', result['android_result'])
        self.assertEqual('failed', result['restart_result'])
        self.assertTrue((self.output/result['zip_name']).exists())

    def test_stop_failure_does_not_zip_and_attempts_recovery(self):
        self.control.stop.side_effect = BackupError('server_stop_failed')
        with patch.object(worker, 'write_zip') as write:
            result = self.run_worker()
        write.assert_not_called()
        self.control.resume.assert_called_once()
        self.assertEqual('failed', result['android_result'])

    def test_capacity_preflight_does_not_stop(self):
        with patch.object(worker, 'capacity', side_effect=BackupError('capacity_insufficient')):
            result = self.run_worker()
        self.control.stop.assert_not_called()
        self.control.resume.assert_not_called()
        self.assertIn('capacity_insufficient', result['errors'])

    def test_missing_current_snapshot_does_not_stop(self):
        (self.public/'workspace-1/current.json').write_text('{"snapshot_id":"missing"}')
        result = self.run_worker()
        self.assertIn('snapshot_missing', result['errors'])
        self.control.stop.assert_not_called()

    def test_traversal_and_symlink_rejected(self):
        (self.public/'workspace-1/current.json').write_text('{"snapshot_id":"../escape"}')
        with self.assertRaisesRegex(BackupError, 'snapshot_invalid'):
            worker.select_android(self.config, self.output, self.boot)
        with patch.object(worker, 'linked', return_value=True):
            with self.assertRaises(BackupError): worker.select_android(self.config,self.output,self.boot)

    def test_failed_verification_does_not_publish(self):
        with patch.object(worker, 'verify_zip', side_effect=BackupError('zip_invalid')):
            result = self.run_worker()
        self.assertEqual('failed', result['android_result'])
        self.assertFalse(list(self.output.glob('*.zip')))
        self.assertTrue(list(self.output.glob('*.partial')))
        self.control.resume.assert_called_once()

    def test_backup_id_collision_keeps_existing_zip(self):
        first = self.run_worker()
        archive = self.output/first['zip_name']
        before = archive.read_bytes()
        second = self.run_worker()
        self.assertEqual('failed', second['android_result'])
        self.assertEqual(before, archive.read_bytes())

    def test_launch_detaches_and_passes_lock(self):
        # Exercise POSIX launch contract on Windows using a fake flock API.
        self.job.rmdir()
        fake = Mock(LOCK_EX=2, LOCK_NB=4)
        with patch.dict('sys.modules', {'fcntl':fake}), patch.object(worker.Path, 'home', return_value=self.root), patch.object(worker.subprocess,'Popen') as popen:
            result = worker.launch(self.request)
            self.assertEqual('started',result['state'])
            options = popen.call_args.kwargs
            self.assertTrue(options['start_new_session'])
            self.assertTrue(options['close_fds'])
            self.assertEqual(1,len(options['pass_fds']))
            self.assertEqual(worker.subprocess.DEVNULL,options['stdin'])
            self.assertEqual(worker.subprocess.DEVNULL,options['stdout'])
            self.assertEqual('existing',worker.launch(self.request)['state'])
            self.assertEqual(1,popen.call_count)

    def test_worker_busy_does_not_launch(self):
        self.job.rmdir()
        fake = Mock(LOCK_EX=2,LOCK_NB=4)
        fake.flock.side_effect=BlockingIOError()
        with patch.dict('sys.modules', {'fcntl':fake}), patch.object(worker.Path,'home',return_value=self.root), patch.object(worker.subprocess,'Popen') as popen:
            with self.assertRaisesRegex(BackupError,'backup_busy'): worker.launch(self.request)
            popen.assert_not_called()

    def test_interruption_restarts(self):
        with patch.object(worker,'write_zip',side_effect=BackupError('worker_interrupted')):
            result=self.run_worker()
        self.assertIn('worker_interrupted',result['errors'])
        self.control.resume.assert_called_once()
