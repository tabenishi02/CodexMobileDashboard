import hashlib
import zipfile
import http.client
import json
import shutil
import threading
import unittest
from tools import backup, backup_restore
from tools.tests import test_backup
from server.tests import test_backup_worker
from server.server import create_server
from tools.pending_snapshot_queue import PendingSnapshotQueue
from tools.collector_state import load_collector_state
from tools.inference_ledger import load as load_ledger


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.pc=test_backup.BackupTests()
        self.pc.setUp()
        self.addCleanup(self.pc.doCleanups)
        self.android=test_backup_worker.WorkerTests()
        self.android.setUp()
        self.addCleanup(self.android.doCleanups)
        self.id=backup.new_backup_id()
        self.presult=backup.create_pc_zip(backup.select_pc(self.pc.config),self.id,[])
        self.android.request['backup_id']=self.id
        self.aresult=self.android.run_worker()
        self.pzip=self.pc.output/self.presult['zip_name']
        self.azip=self.android.output/self.aresult['zip_name']
        self.pair=dict(backup_id=self.id,pair_state='complete',pc=self.presult,android=self.aresult)

    def restore_pc(self, pair=None):
        dest=self.pc.root/'pc-restored'
        backup_restore.stage(self.pzip,dest,'PC',backup.digest(self.pzip),pair)
        self.assertEqual(load_collector_state(self.pc.state),load_collector_state(dest/'payload/state/state.json'))
        self.assertEqual(load_ledger(self.pc.ledger),load_ledger(dest/'payload/ledger/ledger.json'))
        items=PendingSnapshotQueue(dest/'payload/queue').pending()
        self.assertEqual(self.pc.queued.commit_delivery_id,items[0].commit_delivery_id)
        self.assertEqual(self.pc.queued.uploads,items[0].uploads)
        self.assertEqual(self.id,(dest/'RESTORE_VERIFIED').read_text())
        return dest

    def restore_android(self,pair=None):
        dest=self.android.root/'android-restored'
        backup_restore.stage(self.azip,dest,'Android',backup.digest(self.azip),pair)
        public=dest/'public'
        workspace=public/'workspace-1'
        workspace.mkdir(parents=True)
        shutil.copyfile(dest/'payload/current_workspace-1/current.json',workspace/'current.json')
        shutil.copytree(dest/'payload/snapshot_workspace-1',workspace/'snapshots/current')
        server=create_server('127.0.0.1',0,public_directory=str(public))
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                connection.request('GET','/data/workspace-1/metadata.json')
                response=connection.getresponse()
                self.assertEqual(200,response.status)
                self.assertEqual('current',json.loads(response.read())['snapshot_id'])
            finally:connection.close()
        finally:
            server.shutdown();thread.join();server.server_close()
        self.assertFalse((workspace/'snapshots/old').exists())
        return dest

    def test_pc_only_restores_state_ledger_and_pending_delivery(self):self.restore_pc()
    def test_android_only_serves_restored_current(self):self.restore_android()
    def test_both_restore_using_same_pair(self):
        self.restore_pc(self.pair)
        self.restore_android(self.pair)

    def test_full_both_sides_stage_all_android_history(self):
        staging = self.android.root / 'staging'
        (staging / '.deliveries').mkdir(parents=True)
        (staging / '.deliveries/receipt.json').write_text('{"ok":true}')
        (staging / '.commits').mkdir()
        (staging / '.commits/receipt.json').write_text('{"ok":true}')
        (staging / 'workspace-1/pending').mkdir(parents=True)
        (staging / 'workspace-1/pending/metadata.json').write_text(
            '{"snapshot_id":"pending"}'
        )
        backup_id = backup.new_backup_id()
        pc_result = backup.create_pc_zip(
            backup.select_pc(self.pc.config, mode='full'), backup_id, []
        )
        android_result = test_backup_worker.worker.write_zip(
            test_backup_worker.worker.select_android(
                self.android.config, self.android.output, self.android.boot, mode='full'
            ),
            self.android.output,
            backup_id,
        )
        pair = dict(
            backup_id=backup_id,
            mode='full',
            pair_state='complete',
            pc=pc_result,
            android=android_result,
        )
        pc_zip = self.pc.output / pc_result['zip_name']
        android_zip = self.android.output / android_result['zip_name']
        pc_destination = self.pc.root / 'pc-full-restored'
        android_destination = self.android.root / 'android-full-restored'
        backup_restore.stage(
            pc_zip, pc_destination, 'PC', backup.digest(pc_zip), pair, mode='full'
        )
        backup_restore.stage(
            android_zip, android_destination, 'Android',
            backup.digest(android_zip), pair, mode='full'
        )
        self.assertTrue(
            (android_destination /
             'payload/public/workspace-1/snapshots/old/metadata.json').is_file()
        )
        self.assertTrue(
            (android_destination /
             'payload/staging/workspace-1/pending/metadata.json').is_file()
        )
        self.assertTrue(
            (android_destination /
             'payload/staging/.deliveries/receipt.json').is_file()
        )
        self.assertTrue(
            (android_destination /
             'payload/staging/.commits/receipt.json').is_file()
        )
        with self.assertRaisesRegex(backup.BackupError, 'manifest_invalid'):
            backup_restore.stage(
                android_zip, self.android.root/'wrong-mode', 'Android',
                backup.digest(android_zip), pair
            )

    def test_restore_cli_requires_explicit_full_mode(self):
        staging = self.android.root / 'staging'
        staging.mkdir()
        backup_id = backup.new_backup_id()
        plan = test_backup_worker.worker.select_android(
            self.android.config, self.android.output, self.android.boot, mode='full'
        )
        result = test_backup_worker.worker.write_zip(
            plan, self.android.output, backup_id
        )
        archive = self.android.output / result['zip_name']
        destination = self.android.root / 'cli-full-restored'
        with unittest.mock.patch('sys.stdout'), self.subTest('explicit full'):
            self.assertEqual(0, backup_restore.main([
                '--zip', str(archive),
                '--destination', str(destination),
                '--side', 'Android',
                '--sha256', backup.digest(archive),
                '--mode', 'full',
            ]))
        self.assertEqual(backup_id, (destination/'RESTORE_VERIFIED').read_text())

    def test_wrong_id_or_incomplete_pair_rejected_without_writes(self):
        for changes in ({'backup_id':backup.new_backup_id()},{'pair_state':'incomplete'}):
            with self.assertRaises(backup.BackupError):
                backup_restore.stage(self.pzip,self.pc.root/'bad','PC',backup.digest(self.pzip),{**self.pair,**changes})
            self.assertFalse((self.pc.root/'bad').exists())

    def test_wrong_hash_and_wrong_side_rejected(self):
        for side,sha in [('PC','0'*64),('Android',backup.digest(self.pzip))]:
            with self.assertRaises(backup.BackupError):
                backup_restore.stage(self.pzip,self.pc.root/'bad',side,sha)

    def test_existing_destination_never_overwritten(self):
        with self.assertRaises(backup.BackupError):
            backup_restore.stage(self.pzip,self.pc.root,'PC',backup.digest(self.pzip))
        self.assertEqual('TOP-SECRET-DO-NOT-LOG',self.pc.token.read_text())

    def test_pid_entry_rejected_even_with_matching_manifest(self):
        altered=self.pc.root/'with-pid.zip'
        with zipfile.ZipFile(self.pzip) as source:
            manifest=json.loads(source.read('manifest.json'))
            name='payload/data/runtime.pid'
            data=b'123'
            manifest['files'].append(dict(path=name,size=3,sha256=hashlib.sha256(data).hexdigest(),mode=384))
            next(t for t in manifest['targets'] if t['label']=='data')['files'].append(name)
            with zipfile.ZipFile(altered,'w') as target:
                for entry in source.namelist():
                    if entry!='manifest.json':target.writestr(entry,source.read(entry))
                target.writestr(name,data)
                target.writestr('manifest.json',json.dumps(manifest))
        with self.assertRaisesRegex(backup.BackupError,'restore_path_invalid'):
            backup_restore.stage(altered,self.pc.root/'bad','PC',backup.digest(altered))
        self.assertFalse((self.pc.root/'bad').exists())
