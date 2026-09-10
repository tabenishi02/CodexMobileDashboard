"""PC recovery backup. No network access, deletion, or scheduled task mutation."""
import argparse
import configparser
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import uuid
import zipfile

from tools.collector import _load_settings, _runtime_settings, _path_value
from tools.collector_state import load_collector_state
from tools.collector_history import load_collector_history
from tools.inference_ledger import load as load_ledger
from tools.pending_snapshot_queue import PendingSnapshotQueue

MUTEX_NAME = 'Local\\CodexMobileDashboard-Collector'
ID_PATTERN = re.compile(r'[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}')


class BackupError(ValueError):
    pass


def new_backup_id():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]


class CollectorMutex:
    def __init__(self, seconds, name=MUTEX_NAME):
        self.seconds, self.name = seconds, name

    def __enter__(self):
        if os.name != 'nt':
            raise BackupError('windows_required')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.api.CreateMutexW.restype = wintypes.HANDLE
        self.api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.api.ReleaseMutex.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise BackupError('mutex_create_failed')
        result = self.api.WaitForSingleObject(self.handle, int(self.seconds * 1000))
        if result not in (0, 0x80):
            self.api.CloseHandle(self.handle)
            raise BackupError('mutex_timeout' if result == 0x102 else 'mutex_wait_failed')
        return self

    def __exit__(self, *args):
        try:
            self.api.ReleaseMutex(self.handle)
        finally:
            self.api.CloseHandle(self.handle)


def read_config(path):
    parser = configparser.ConfigParser(interpolation=None)
    with Path(path).open(encoding='utf-8') as stream:
        parser.read_file(stream)
    return parser


def linked(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            return True
        if item.exists() and getattr(item.stat(), 'st_file_attributes', 0) & 0x400:
            return True
    return False


def temporary(path):
    return any(p.lower().endswith(('.pid', '.lock', '.partial', '.tmp')) or
               p.startswith('.pending-') for p in path.parts)


def within(path, root):
    return path == root or root in path.parents


def select_pc(config):
    config = Path(config).absolute()
    backup = read_config(config)
    collector = _path_value(backup, 'backup', 'collector_config').absolute()
    output = _path_value(backup, 'backup', 'output_dir').absolute()
    if linked(output):
        raise BackupError('linked_output')
    settings = _load_settings(collector)
    runtime = _runtime_settings(collector, settings)
    parser = read_config(collector)
    targets = [('backup_config', config, True), ('collector_config', collector, True),
               ('token', settings['token_file'], True), ('ca', settings['ca_file'], True),
               ('state', runtime.state_file, False), ('history', runtime.history_file, False),
               ('ledger', runtime.inference_ledger_file, False), ('queue', runtime.queue_dir, False),
               ('data', runtime.output_dir, False)]
    if parser.has_option('storage', 'workspace_registry'):
        targets.append(('registry', _path_value(parser, 'storage', 'workspace_registry'), False))
    # An explicit empty list means issuance materials are intentionally not held here.
    tls = backup.get('backup', 'tls_sources')
    for index, value in enumerate(tls.splitlines()):
        if value.strip():
            targets.append((f'tls_{index}', Path(os.path.expandvars(value.strip())), True))
    if backup.getboolean('backup', 'include_logs', fallback=False):
        targets.append(('logs', settings['log_directory'], False))
    reserve = backup.getint('backup', 'minimum_free_bytes', fallback=1073741824)
    wait = backup.getint('backup', 'mutex_wait_seconds', fallback=300)
    if reserve < 0 or not 0 <= wait <= 86400:
        raise BackupError('limits_invalid')
    files, records, excluded = [], [], []
    for label, path, required in targets:
        path = Path(path).absolute()
        if linked(path):
            raise BackupError('linked_source')
        path = path.resolve()
        if within(path, output.resolve()):
            raise BackupError('target_inside_backup')
        record = dict(label=label, source=str(path), required=required, present=path.exists(), files=[])
        records.append(record)
        if not path.exists():
            if required:
                raise BackupError('required_source_missing')
            continue
        def walk(item):
            forbidden = [runtime.sessions_dir.resolve(), runtime.archived_sessions_dir.resolve()]
            if item.name == '.git' or any(within(item, root) for root in forbidden):
                raise BackupError('source_scope_invalid')
            if within(item, output.resolve()):
                excluded.append(dict(label=label, reason='backup_directory'))
                return
            if linked(item):
                raise BackupError('linked_source')
            if temporary(item.relative_to(path.parent)):
                if item == path:
                    raise BackupError('temporary_target')
                excluded.append(dict(label=label, reason='temporary'))
                return
            if item.is_dir():
                for child in sorted(item.iterdir()):
                    walk(child)
            elif item.is_file():
                name = f'payload/{label}/' + (item.relative_to(path).as_posix() if path.is_dir() else item.name)
                files.append((item, name))
                record['files'].append(name)
            else:
                raise BackupError('unsupported_source')
        walk(path)
    load_collector_state(runtime.state_file)
    load_collector_history(runtime.history_file)
    load_ledger(runtime.inference_ledger_file)
    queued = PendingSnapshotQueue(runtime.queue_dir).pending()
    for path, name in files:
        if path.suffix == '.json':
            json.loads(path.read_text(encoding='utf-8'))
    return dict(output=output, reserve=reserve, wait=wait, files=files, targets=records,
                excluded=excluded, workspaces=sorted({p.name for p in runtime.output_dir.iterdir() if p.is_dir() and not within(p.resolve(), output.resolve())}) if runtime.output_dir.exists() else [],
                queue=[dict(workspace_id=q.workspace_id, snapshot_id=q.snapshot_id,
                            sequence=q.sequence, commit_delivery_id=q.commit_delivery_id) for q in queued])


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def verify_zip(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or archive.testzip():
                raise BackupError('zip_invalid')
            for info in archive.infolist():
                name = info.filename
                if '\\' in name or ':' in name or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/')):
                    raise BackupError('zip_path_invalid')
                if stat.S_ISLNK(info.external_attr >> 16):
                    raise BackupError('zip_path_invalid')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest['schema_version'] != 1 or not ID_PATTERN.fullmatch(manifest['backup_id']) or manifest['side'] != 'PC' or manifest['mode'] != 'recovery':
                raise BackupError('manifest_invalid')
            listed = [f['path'] for f in manifest['files']]
            if len(listed) != len(set(listed)) or set(names) != set(listed) | {'manifest.json'}:
                raise BackupError('manifest_invalid')
            mandatory = {'backup_config', 'collector_config', 'token', 'ca'}
            labels = {t['label'] for t in manifest['targets'] if t['present']}
            if not mandatory <= labels:
                raise BackupError('required_source_missing')
            target_files = [n for t in manifest['targets'] for n in t['files']]
            if len(target_files) != len(set(target_files)) or set(target_files) != set(listed):
                raise BackupError('manifest_invalid')
            for target in manifest['targets']:
                if target['required'] and (not target['present'] or not target['files']):
                    raise BackupError('required_source_missing')
            for entry in manifest['files']:
                value = hashlib.sha256()
                size = 0
                with archive.open(entry['path']) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        size += len(block)
                        value.update(block)
                if size != entry['size'] or value.hexdigest() != entry['sha256']:
                    raise BackupError('hash_mismatch')
            return manifest
    except BackupError:
        raise
    except Exception:
        raise BackupError('zip_invalid') from None


def task_definitions(names):
    results = []
    for name in names:
        # Task names pass as environment data, never interpolated PowerShell code.
        env = dict(os.environ, DASHBOARD_BACKUP_TASK=name)
        command = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; $t=Get-ScheduledTask -TaskPath '\\' | Where-Object TaskName -eq $env:DASHBOARD_BACKUP_TASK; if ($null -eq $t) { '{}' } else { [pscustomobject]@{name=$t.TaskName; xml=(Export-ScheduledTask -TaskName $t.TaskName -TaskPath '\\')} | ConvertTo-Json -Compress }"
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                env=env, capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise BackupError('task_export_failed')
        results.append(json.loads(result.stdout.decode('utf-8-sig')))
    return results


def create_pc_zip(plan, backup_id, tasks):
    if not ID_PATTERN.fullmatch(backup_id):
        raise BackupError('backup_id_invalid')
    output = plan['output']
    output.mkdir(parents=True, exist_ok=True)
    final = output / f'CodexMobileDashboard-PC-{backup_id}.zip'
    partial = Path(str(final) + '.partial')
    result_path = output / f'{backup_id}.PC.result.json'
    if final.exists() or partial.exists() or result_path.exists():
        raise BackupError('backup_id_exists')
    size = sum(p.stat().st_size for p, _ in plan['files'])
    if shutil.disk_usage(output).free < size * 1.02 + len(plan['files']) * 2048 + 1048576 + plan['reserve']:
        raise BackupError('capacity_insufficient')
    root = Path(__file__).resolve().parent.parent
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).decode().strip()
    manifest = dict(schema_version=1, backup_id=backup_id, created_at=datetime.now(timezone.utc).isoformat(),
                    side='PC', mode='recovery', git_commit=git('rev-parse', 'HEAD'),
                    git_dirty=bool(git('status', '--porcelain')), targets=plan['targets'],
                    excluded=plan['excluded'], workspaces=plan['workspaces'], queue=plan['queue'],
                    task_definitions=tasks, files=[])
    with zipfile.ZipFile(partial, 'x', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path, name in plan['files']:
            before = path.stat()
            sha = digest(path)
            archive.write(path, name)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise BackupError('source_changed')
            manifest['files'].append(dict(path=name, size=before.st_size, sha256=sha, mode=stat.S_IMODE(before.st_mode)))
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
    verify_zip(partial)
    checksum = digest(partial)
    # On Windows rename refuses an existing destination; mutex protects the normal path.
    if final.exists():
        raise BackupError('backup_id_exists')
    partial.rename(final)
    result = dict(schema_version=1, backup_id=backup_id, mode='recovery', pc_result='success',
                  android_result='not_run', restart_result='not_run', pair_state='incomplete',
                  zip_sha256=checksum, zip_name=final.name)
    temp_result = Path(str(result_path) + '.partial')
    with temp_result.open('x', encoding='utf-8') as stream:
        json.dump(result, stream)
    temp_result.rename(result_path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='PC-only recovery backup; Android is not contacted')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--backup-id')
    parser.add_argument('--mode', choices=['recovery', 'full'], default='recovery')
    parser.add_argument('--verify', type=Path)
    args = parser.parse_args(argv)
    backup_id = args.backup_id or new_backup_id()
    log_path = None
    def event(value):
        line = json.dumps(dict(event=value, backup_id=backup_id if ID_PATTERN.fullmatch(backup_id) else 'invalid', mode=args.mode))
        print(line, flush=True)
        if log_path is not None:
            with log_path.open('a', encoding='utf-8') as stream:
                stream.write(line + '\n')
    try:
        if args.verify:
            manifest = verify_zip(args.verify)
            backup_id = manifest['backup_id']
            event('zip_verified')
            return 0
        if args.mode != 'recovery':
            raise BackupError('full_not_implemented')
        if not args.config:
            raise BackupError('configuration_missing')
        if not ID_PATTERN.fullmatch(backup_id):
            raise BackupError('backup_id_invalid')
        config = read_config(args.config)
        wait = config.getint('backup', 'mutex_wait_seconds', fallback=300)
        if not 0 <= wait <= 86400:
            raise BackupError('limits_invalid')
        event('backup_start')
        with CollectorMutex(wait):
            event('mutex_acquired')
            plan = select_pc(args.config)
            plan['output'].mkdir(parents=True, exist_ok=True)
            log_path = plan['output'] / 'backup.log'
            event('backup_start_locked')
            names = config.get('backup', 'task_names').splitlines()
            tasks = task_definitions([n.strip() for n in names if n.strip()])
            event('pc_zip_start')
            result = create_pc_zip(plan, backup_id, tasks)
            event('pc_zip_verified')
            print(json.dumps(result), flush=True)
        event('mutex_released')
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, BackupError) else 'backup_failed'
        # Log storage failures must not escape the safe error boundary.
        try:
            event(code)
            if log_path is not None:
                failure = log_path.parent / f'{backup_id}.PC.failure.json'
                with failure.open('x', encoding='utf-8') as stream:
                    json.dump(dict(backup_id=backup_id, pc_result='failed', android_result='not_run',
                                   restart_result='not_run', pair_state='incomplete', error=code), stream)
        except OSError:
            print(json.dumps(dict(event=code, pair_state='incomplete')), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
