"""Coordinate PC and detached Android backups while holding the collector mutex."""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time

from tools.backup import (BackupError, CollectorMutex, ID_PATTERN, create_pc_zip,
                          new_backup_id, read_config, select_pc, task_definitions)


def save(path, value):
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Remote:
    def __init__(self, config):
        self.host = config.get('android', 'ssh_host')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', self.host):
            raise BackupError('ssh_alias_invalid')
        self.repository = config.get('android', 'repository')
        self.options = {key: config.get('android', key) for key in ('config', 'output', 'ca', 'health_url')}
        if not self.repository.startswith('/') or any(not self.options[k].startswith('/') for k in ('config','output','ca')):
            raise BackupError('remote_absolute_paths_required')
        self.reserve = config.getint('android', 'minimum_free_bytes', fallback=1073741824)
        if self.reserve < 0:
            raise BackupError('limits_invalid')

    def call(self, arguments):
        command = 'cd ' + shlex.quote(self.repository) + ' && ' + shlex.join(['python', '-m', 'server.backup_worker', *arguments])
        try:
            response = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                       '-o', 'ConnectTimeout=10', self.host, command],
                                      stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if response.returncode:
                raise BackupError('ssh_request_failed')
            return json.loads(response.stdout.decode('utf-8'))
        except BackupError:
            raise
        except Exception:
            raise BackupError('ssh_unavailable') from None

    def start_args(self, backup_id):
        args = ['--backup-id', backup_id, '--collector-paused', '--reserve', str(self.reserve)]
        for key, value in self.options.items():
            args += ['--' + key.replace('_', '-'), value]
        return args

    def preflight(self, backup_id):
        return self.call(['preflight', *self.start_args(backup_id)])

    def start(self, backup_id):
        return self.call(['start', *self.start_args(backup_id)])

    def status(self, backup_id):
        return self.call(['status', '--backup-id', backup_id, '--output', self.options['output']])


def coordinate(plan, backup_id, remote, tasks, timeout, poll=3):
    output = plan['output']
    output.mkdir(parents=True, exist_ok=True)
    path = output / f'{backup_id}.pair.json'
    if path.exists() or list(output.glob(f'*{backup_id}*.zip*')):
        raise BackupError('backup_id_exists')
    pair = dict(backup_id=backup_id, mode='recovery', pair_state='incomplete', recovery_state='unknown',
                pc_result='not_run', android_result='not_run', restart_result='unknown', errors=[])
    save(path, pair)
    launched = False
    def record(event):
        try:
            save(path, pair)
            with (output/'backup-pair.log').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(backup_id=backup_id, event=event))+'\n')
        except OSError:
            if 'result_write_failed' not in pair['errors']:
                pair['errors'].append('result_write_failed')
    try:
        size = sum(p.stat().st_size for p, _ in plan['files'])
        if shutil.disk_usage(output).free < size*1.02 + len(plan['files'])*2048 + 1048576 + plan['reserve']:
            raise BackupError('capacity_insufficient')
        if remote.preflight(backup_id).get('state') != 'ready':
            raise BackupError('remote_preflight_failed')
        record('preflight_ready')
        # Start only once. A lost acknowledgement is resolved exclusively through status.
        launched = True
        try:
            reply = remote.start(backup_id)
            if reply.get('backup_id') != backup_id or reply.get('state') != 'started':
                raise BackupError('remote_start_unconfirmed')
        except BackupError:
            pair['errors'].append('remote_start_unconfirmed')
        record('remote_requested')
        try:
            pc = create_pc_zip(plan, backup_id, tasks)
            pair['pc_result'] = 'success'
            pair['pc'] = pc
        except Exception:
            pair['pc_result'] = 'failed'
            pair['errors'].append('pc_backup_failed')
        # Always await Android recovery, including when PC creation fails.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = remote.status(backup_id)
                if status.get('backup_id') != backup_id:
                    raise BackupError('remote_id_mismatch')
                if status.get('state') == 'finished':
                    android = status.get('android_result')
                    restart = status.get('restart_result')
                    if android not in ('success','failed') or restart not in ('success','failed','not_needed'):
                        raise BackupError('remote_result_invalid')
                    if android == 'success' and (not re.fullmatch(r'[a-f0-9]{64}', status.get('zip_sha256','')) or status.get('zip_name') != f'CodexMobileDashboard-Android-{backup_id}.zip'):
                        raise BackupError('remote_result_invalid')
                    pair['android_result'], pair['restart_result'] = android, restart
                    pair['android'] = {k:status[k] for k in ('zip_name','zip_sha256') if k in status}
                    pair['recovery_state'] = 'failed' if restart == 'failed' else 'restored'
                    pair['pair_state'] = 'complete' if pair['pc_result'] == android == 'success' else 'incomplete'
                    if android != 'success': pair['errors'].append('android_backup_failed')
                    if restart == 'failed': pair['errors'].append('restart_failed')
                    break
            except Exception:
                pass
            time.sleep(poll)
        else:
            pair['android_result'] = 'unknown'
            pair['errors'].append('remote_timeout')
    except Exception as error:
        pair['errors'].append(str(error) if isinstance(error, BackupError) else 'coordination_failed')
        if not launched:
            pair['recovery_state'] = 'unchanged'
    finally:
        record('finished')
    return pair


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--backup-id')
    args = parser.parse_args(argv)
    backup_id = args.backup_id or new_backup_id()
    try:
        if not ID_PATTERN.fullmatch(backup_id): raise BackupError('backup_id_invalid')
        config = read_config(args.config)
        wait = config.getint('backup','mutex_wait_seconds',fallback=300)
        timeout = config.getint('android','wait_seconds',fallback=1800)
        if not 0 <= wait <= 86400 or not 1 <= timeout <= 86400: raise BackupError('limits_invalid')
        remote = Remote(config)
        with CollectorMutex(wait):
            plan = select_pc(args.config)
            tasks = task_definitions([n.strip() for n in config.get('backup','task_names').splitlines() if n.strip()])
            pair = coordinate(plan,backup_id,remote,tasks,timeout)
        print(json.dumps({k:pair[k] for k in ('backup_id','pair_state','recovery_state','pc_result','android_result','restart_result','errors')}))
        return 0 if pair['pair_state']=='complete' and pair['recovery_state']=='restored' and 'result_write_failed' not in pair['errors'] else 2
    except Exception as error:
        print(json.dumps(dict(event=str(error) if isinstance(error,BackupError) else 'backup_pair_failed')))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
