import configparser
import io
import json
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import Mock, patch

from tools import backup_pair as pair
from tools.backup import BackupError, new_backup_id, select_pc
from tools.tests import test_backup


class PairTests(TestCase):
    setUp = test_backup.BackupTests.setUp

    def run_pair(self, pc_error=None, android='success', restart='success', lost=False, mode='recovery'):
        backup_id = new_backup_id()
        remote = Mock()
        remote.preflight.return_value = {'state':'ready','mode':mode}
        remote.start.return_value = {'backup_id':backup_id,'mode':mode,'state':'started'}
        if lost: remote.start.side_effect=BackupError('ssh_unavailable')
        remote.status.side_effect = [BackupError('ssh_unavailable'), {'backup_id':backup_id,'mode':mode,'state':'finished',
            'android_result':android,'restart_result':restart,'zip_sha256':'a'*64,
            'zip_name':f'CodexMobileDashboard-Android-{backup_id}.zip'}]
        if pc_error:
            with patch.object(pair,'create_pc_zip',side_effect=pc_error):
                result = pair.coordinate(select_pc(self.config, mode=mode),backup_id,remote,[],1,poll=0)
        else:
            result = pair.coordinate(select_pc(self.config, mode=mode),backup_id,remote,[],1,poll=0)
        self.assertEqual(1,remote.start.call_count)
        self.assertEqual(2,remote.status.call_count)
        self.assertEqual(result,json.loads((self.output/f'{backup_id}.pair.json').read_text()))
        self.assertEqual(mode, result['mode'])
        self.assertEqual((backup_id, mode), remote.preflight.call_args.args)
        self.assertEqual((backup_id, mode), remote.start.call_args.args)
        return result

    def test_success_including_reconnect_after_lost_start_ack(self):
        result=self.run_pair(lost=True)
        self.assertEqual('complete',result['pair_state'])
        self.assertEqual('restored',result['recovery_state'])

    def test_explicit_full_propagates_to_both_sides(self):
        result = self.run_pair(mode='full')
        self.assertEqual('complete', result['pair_state'])

    def test_pc_failure_still_waits_for_android(self):
        result=self.run_pair(pc_error=OSError('SECRET'))
        self.assertEqual('incomplete',result['pair_state'])
        self.assertEqual('restored',result['recovery_state'])
        self.assertNotIn('SECRET',json.dumps(result))

    def test_android_failure_keeps_pc_success(self):
        result=self.run_pair(android='failed')
        self.assertEqual('success',result['pc_result'])
        self.assertEqual('incomplete',result['pair_state'])

    def test_restart_failure_is_separate_from_pair_completeness(self):
        result=self.run_pair(restart='failed')
        self.assertEqual('complete',result['pair_state'])
        self.assertEqual('failed',result['recovery_state'])
        self.assertIn('restart_failed',result['errors'])

    def test_preflight_error_never_starts_android(self):
        remote=Mock()
        remote.preflight.side_effect=BackupError('ssh_unavailable')
        result=pair.coordinate(select_pc(self.config),new_backup_id(),remote,[],1)
        remote.start.assert_not_called()
        self.assertEqual('unchanged',result['recovery_state'])

    def test_timeout_does_not_restart_or_claim_complete(self):
        remote=Mock()
        remote.preflight.return_value={'state':'ready','mode':'recovery'}
        backup_id=new_backup_id()
        remote.start.return_value={'backup_id':backup_id,'mode':'recovery','state':'started'}
        with patch.object(pair.time,'monotonic',side_effect=[0,2]):
            result=pair.coordinate(select_pc(self.config),backup_id,remote,[],1)
        self.assertEqual('unknown',result['android_result'])
        self.assertEqual('incomplete',result['pair_state'])
        self.assertEqual(1,remote.start.call_count)

    def test_cli_holds_mutex_until_coordinate_returns(self):
        config=pair.read_config(self.config)
        config['android']={'wait_seconds':'2'}
        with self.config.open('w') as f:config.write(f)
        with patch.object(pair,'Remote'),patch.object(pair,'CollectorMutex') as mutex,patch.object(pair,'coordinate') as coordinate,redirect_stdout(io.StringIO()):
            def finish(*args):
                mutex.return_value.__exit__.assert_not_called()
                return dict(backup_id=args[1],mode='recovery',pair_state='complete',recovery_state='restored',pc_result='success',android_result='success',restart_result='success',errors=[])
            coordinate.side_effect=finish
            self.assertEqual(0,pair.main(['--config',str(self.config)]))
            mutex.return_value.__exit__.assert_called_once()

    def test_cli_full_is_explicit_and_holds_mutex(self):
        config=pair.read_config(self.config)
        config['android']={'wait_seconds':'2'}
        with self.config.open('w') as f:config.write(f)
        with (
            patch.object(pair, 'Remote'),
            patch.object(pair, 'CollectorMutex') as mutex,
            patch.object(pair, 'coordinate') as coordinate,
            redirect_stdout(io.StringIO()),
        ):
            coordinate.return_value = dict(
                backup_id='20260928T000000Z-a31f82c4', mode='full',
                pair_state='complete', recovery_state='restored',
                pc_result='success', android_result='success',
                restart_result='success', errors=[])
            self.assertEqual(
                0,
                pair.main([
                    '--config', str(self.config), '--mode', 'full',
                    '--backup-id', '20260928T000000Z-a31f82c4',
                ]),
            )
        mutex.assert_called_once()
        self.assertEqual('full', coordinate.call_args.args[0]['mode'])

    def test_remote_quotes_paths_and_disables_interactive_ssh(self):
        config=configparser.ConfigParser()
        config['android']=dict(ssh_host='test-alias',repository="/test path/with'quote",config='/config',output='/output',ca='/ca',health_url='https://test/health')
        remote=pair.Remote(config)
        response=Mock(returncode=0,stdout=b'{"state":"ready"}')
        with patch.object(pair.subprocess,'run',return_value=response) as run:
            remote.preflight(new_backup_id())
            args=run.call_args.args[0]
            self.assertIn('BatchMode=yes',args)
            self.assertIn('StrictHostKeyChecking=yes',args)
            self.assertIn('--mode recovery', args[-1])
            self.assertEqual(1800, run.call_args.kwargs['timeout'])
            self.assertIn("'\"'\"'",args[-1])
        config['android']['ssh_host']='-oProxyCommand=bad'
        with self.assertRaises(BackupError):pair.Remote(config)
