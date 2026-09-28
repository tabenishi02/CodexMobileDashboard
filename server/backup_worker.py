"""Detached Termux backup worker primitives. Public commands remain recovery-only."""
import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import time
import urllib.request
import zipfile

from server.server import load_server_settings
from tools.backup import BackupError, ID_PATTERN, digest, linked, temporary, verify_zip, within


def atomic_json(path, value):
    temp = path.with_name(path.name + '.partial')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def select_android(config, output, boot, mode='recovery'):
    if mode not in ('recovery', 'full'):
        raise BackupError('mode_invalid')
    settings = load_server_settings(str(config))
    if linked(settings['public_directory'].absolute()):
        raise BackupError('linked_source')
    public = settings['public_directory'].resolve()
    targets = [('server_config', config, True), ('token', settings['token_file'], True),
               ('certificate', settings['certificate_file'], True),
               ('private_key', settings['private_key_file'], True), ('boot', boot, False)]
    workspaces = []
    if linked(public) or not public.is_dir():
        raise BackupError('public_invalid')
    for workspace in sorted(public.iterdir()):
        if within(workspace.resolve(), output.resolve()):
            continue
        if linked(workspace) or not workspace.is_dir() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', workspace.name):
            raise BackupError('workspace_invalid')
        current = workspace / 'current.json'
        if linked(current):
            raise BackupError('linked_source')
        value = json.loads(current.read_text(encoding='utf-8'))
        snapshot = value['snapshot_id']
        if not isinstance(snapshot, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', snapshot):
            raise BackupError('snapshot_invalid')
        path = workspace / 'snapshots' / snapshot
        if not path.is_dir():
            raise BackupError('snapshot_missing')
        if mode == 'recovery':
            targets += [(f'current_{workspace.name}', current, True),
                        (f'snapshot_{workspace.name}', path, True)]
        workspaces.append(dict(workspace_id=workspace.name, snapshot_id=snapshot))
    if mode == 'full':
        targets += [('public', public, True),
                    ('staging', settings['staging_directory'], True)]
    files, records = [], []
    for label, source, required in targets:
        source = Path(source).absolute()
        if linked(source) or within(source.resolve(), output.resolve()):
            raise BackupError('source_invalid')
        record = dict(label=label, source=str(source), required=required, present=source.exists(), files=[])
        if mode == 'full' and label in ('public', 'staging'):
            record['allow_empty'] = True
        records.append(record)
        if not source.exists():
            if required:
                raise BackupError('required_source_missing')
            continue
        def walk(item):
            if linked(item):
                raise BackupError('linked_source')
            if within(item.resolve(), output.resolve()):
                raise BackupError('backup_overlaps_snapshot')
            if temporary(item.relative_to(source.parent)):
                if required:
                    raise BackupError('incomplete_required_source')
                return
            if item.is_dir():
                for child in sorted(item.iterdir()):
                    walk(child)
            elif item.is_file():
                name = f'payload/{label}/' + (item.relative_to(source).as_posix() if source.is_dir() else item.name)
                if item.suffix == '.json':
                    json.loads(item.read_text(encoding='utf-8'))
                files.append((item, name))
                record['files'].append(name)
            else:
                raise BackupError('source_invalid')
        walk(source)
        if required and not record['files'] and not record.get('allow_empty'):
            raise BackupError('required_source_empty')
    return dict(mode=mode, files=files, targets=records, workspaces=workspaces, settings=settings)


def capacity(plan, output, reserve):
    size = sum(p.stat().st_size for p, _ in plan['files'])
    if shutil.disk_usage(output).free < size * 1.02 + 2048 * len(plan['files']) + 1048576 + reserve:
        raise BackupError('capacity_insufficient')


def source_metadata(root):
    def git(*args):
        return subprocess.check_output(
            ['git', '-C', str(root), *args], stderr=subprocess.DEVNULL
        ).decode().strip()

    fingerprint = hashlib.sha256()
    for relative in (
        'server/backup_worker.py', 'server/server.py', 'server/start_server.sh',
        'server/stop_server.sh', 'tools/backup.py',
    ):
        path = root / relative
        fingerprint.update(relative.encode('utf-8') + b'\0')
        fingerprint.update(digest(path).encode('ascii') + b'\n')
    try:
        return git('rev-parse', 'HEAD'), bool(git('status', '--porcelain')), fingerprint.hexdigest()
    except (OSError, subprocess.CalledProcessError):
        return None, True, fingerprint.hexdigest()


def write_zip(plan, output, backup_id):
    mode = plan.get('mode', 'recovery')
    if mode not in ('recovery', 'full'):
        raise BackupError('mode_invalid')
    final = output / f'CodexMobileDashboard-Android-{backup_id}.zip'
    partial = Path(str(final) + '.partial')
    if final.exists() or partial.exists():
        raise BackupError('backup_id_exists')
    root = Path(__file__).resolve().parent.parent
    git_commit, git_dirty, source_fingerprint = source_metadata(root)
    exclusions = (['old_snapshots', 'staging', 'receipts', 'logs', 'pid', 'lock']
                  if mode == 'recovery' else ['logs', 'pid', 'lock', 'temporary'])
    manifest = dict(schema_version=1, backup_id=backup_id, side='Android', mode=mode,
                    created_at=datetime.now(timezone.utc).isoformat(), git_commit=git_commit,
                    git_dirty=git_dirty, source_fingerprint=source_fingerprint, targets=plan['targets'],
                    workspaces=plan['workspaces'], queue='PC backup owns queued deliveries',
                    excluded=exclusions, files=[])
    with zipfile.ZipFile(partial, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, name in plan['files']:
            before = path.stat()
            sha = digest(path)
            archive.write(path, name)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise BackupError('source_changed')
            manifest['files'].append(dict(path=name, size=before.st_size, sha256=sha, mode=stat.S_IMODE(before.st_mode)))
        archive.writestr('manifest.json', json.dumps(manifest))
    verify_zip(partial, expected_side='Android', expected_mode=mode)
    sha = digest(partial)
    # Global flock and per-ID job reservation exclude another publisher.
    if final.exists():
        raise BackupError('backup_id_exists')
    partial.rename(final)
    return dict(zip_name=final.name, zip_sha256=sha)


def health_context(ca):
    context = ssl.create_default_context(cafile=str(ca))
    strict = getattr(ssl, 'VERIFY_X509_STRICT', 0)
    if strict:
        context.verify_flags &= ~strict
    return context


def restore_server_signals():
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGHUP, signal.SIG_DFL)


class ServerControl:
    def __init__(self, config, health_url, ca):
        self.config = Path(config).resolve()
        self.scripts = Path(__file__).resolve().parent
        self.pid_file = Path.home() / '.cache/codex-mobile-dashboard/server.pid'
        self.health_url, self.ca = health_url, ca
        health_context(ca)

    def running(self):
        if not self.pid_file.exists():
            return False
        raw = self.pid_file.read_text().strip()
        if not raw.isdigit() or int(raw) <= 1:
            raise BackupError('server_pid_invalid')
        pid = int(raw)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        args = Path(f'/proc/{pid}/cmdline').read_bytes().split(bytes([0]))
        decoded = [os.fsdecode(a) for a in args if a]
        if '--config' not in decoded or str(self.scripts / 'server.py') not in decoded:
            raise BackupError('server_process_mismatch')
        index = decoded.index('--config') + 1
        if index >= len(decoded) or Path(decoded[index]).resolve() != self.config:
            raise BackupError('server_config_mismatch')
        return True

    def stop(self):
        result = subprocess.run([str(self.scripts / 'stop_server.sh')], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=30)
        if result.returncode or self.running():
            raise BackupError('server_stop_failed')

    def resume(self):
        if not self.running():
            subprocess.Popen([str(self.scripts / 'start_server.sh'), str(self.config)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                             preexec_fn=restore_server_signals)
        context = health_context(self.ca)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))
        for _ in range(20):
            try:
                with opener.open(self.health_url, timeout=2) as response:
                    if response.status == 200 and json.load(response).get('status') == 'ok' and self.running():
                        return
            except Exception:
                pass
            time.sleep(1)
        raise BackupError('server_health_failed')


def run_worker(request, job, control=None):
    result = dict(backup_id=request['backup_id'], mode='recovery', state='running',
                  android_result='failed', pc_result='not_run', restart_result='not_needed',
                  pair_state='incomplete', errors=[])
    path = job / 'result.json'
    def event(name):
        result['event'] = name
        atomic_json(path, result)
        with (job / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event=name, backup_id=result['backup_id'])) + '\n')
    was_running = False
    try:
        event('preflight')
        config, output, boot = (Path(request[k]) for k in ('config', 'output', 'boot'))
        plan = select_android(config, output, boot)
        capacity(plan, output, request['reserve'])
        control = control or ServerControl(config, request['health_url'], request['ca'])
        was_running = control.running()
        result['was_running'] = was_running
        # Persist intent before stop; a later failure must still attempt recovery.
        event('server_stop_start' if was_running else 'server_already_stopped')
        if was_running:
            control.stop()
        event('server_stopped')
        plan = select_android(config, output, boot)
        capacity(plan, output, request['reserve'])
        event('zip_start')
        result.update(write_zip(plan, output, request['backup_id']))
        result['android_result'] = 'success'
        event('zip_verified')
    except Exception as error:
        result['errors'].append(str(error) if isinstance(error, BackupError) else 'backup_failed')
    finally:
        # Ignore further termination signals while attempting service recovery.
        if os.name == 'posix' and __import__('threading').current_thread() is __import__('threading').main_thread():
            for sig in (signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, signal.SIG_IGN)
        if was_running:
            try:
                control.resume()
                result['restart_result'] = 'success'
            except Exception:
                result['restart_result'] = 'failed'
                result['errors'].append('restart_failed')
        result['state'] = 'finished'
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        event('finished')
    return result


def launch(request):
    import fcntl
    output = Path(request['output'])
    if linked(output):
        raise BackupError('linked_output')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    job = output / request['backup_id']
    if job.exists():
        return dict(backup_id=request['backup_id'], state='existing')
    runtime = Path.home() / '.cache/codex-mobile-dashboard'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (runtime / 'backup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError('backup_busy') from None
        job.mkdir(mode=0o700)
        atomic_json(job / 'request.json', request)
        atomic_json(job / 'result.json', dict(backup_id=request['backup_id'], state='starting', pair_state='incomplete'))
        try:
            subprocess.Popen([sys.executable, '-m', 'server.backup_worker', 'work', '--job', str(job), '--lock-fd', str(lock.fileno())],
                             cwd=Path(__file__).resolve().parent.parent, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, close_fds=True, pass_fds=(lock.fileno(),))
        except Exception:
            atomic_json(job / 'result.json', dict(backup_id=request['backup_id'], state='finished', android_result='failed', pair_state='incomplete', errors=['launch_failed']))
            raise BackupError('launch_failed') from None
    return dict(backup_id=request['backup_id'], state='started')


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    start = sub.add_parser('start', aliases=['preflight'])
    start.add_argument('--backup-id', required=True)
    start.add_argument('--config', type=Path, required=True)
    start.add_argument('--output', type=Path, required=True)
    start.add_argument('--ca', type=Path, required=True)
    start.add_argument('--health-url', required=True)
    start.add_argument('--reserve', type=int, default=1073741824)
    start.add_argument('--collector-paused', action='store_true', required=True)
    status = sub.add_parser('status')
    status.add_argument('--backup-id', required=True)
    status.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('zip', type=Path)
    work = sub.add_parser('work')
    work.add_argument('--job', type=Path, required=True)
    work.add_argument('--lock-fd', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify':
            manifest = verify_zip(args.zip, expected_side='Android')
            print(json.dumps(dict(backup_id=manifest['backup_id'], state='verified')))
            return 0
        if os.name != 'posix':
            raise BackupError('termux_required')
        os.umask(0o077)
        if args.command == 'work':
            os.fstat(args.lock_fd)
            request = json.loads((args.job / 'request.json').read_text())
            def interrupted(signum, frame):
                raise BackupError('worker_interrupted')
            for sig in (signal.SIGTERM, signal.SIGHUP):
                signal.signal(sig, interrupted)
            try:
                result = run_worker(request, args.job)
            finally:
                os.close(args.lock_fd)
            return 0 if result['android_result'] == 'success' and result['restart_result'] != 'failed' else 2
        if not ID_PATTERN.fullmatch(args.backup_id):
            raise BackupError('backup_id_invalid')
        if args.command == 'status':
            value = json.loads((args.output / args.backup_id / 'result.json').read_text())
        else:
            if args.reserve < 0 or not args.health_url.startswith('https://'):
                raise BackupError('configuration_invalid')
            if linked(args.output.absolute()):
                raise BackupError('linked_output')
            request = dict(backup_id=args.backup_id, config=str(args.config.resolve()), output=str(args.output.resolve()),
                           ca=str(args.ca.resolve()), health_url=args.health_url, reserve=args.reserve,
                           boot=str(Path.home() / '.termux/boot'))
            if args.command == 'preflight':
                output = Path(request['output'])
                output.mkdir(parents=True, exist_ok=True, mode=0o700)
                if (output / args.backup_id).exists():
                    raise BackupError('backup_id_exists')
                plan = select_android(Path(request['config']), output, Path(request['boot']))
                capacity(plan, output, request['reserve'])
                ServerControl(request['config'], request['health_url'], request['ca']).running()
                value = dict(backup_id=args.backup_id, state='ready')
            else:
                value = launch(request)
        print(json.dumps(value))
        return 0
    except Exception as error:
        print(json.dumps(dict(state='failed', error=str(error) if isinstance(error, BackupError) else 'worker_failed')))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
