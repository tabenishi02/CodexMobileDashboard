"""Read-only Snapshot cleanup inventory. No deletion mode is provided."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import threading
import time


class Progress:
    """Keep progress on stderr so stdout remains valid JSON."""
    def __init__(self, interval=5):
        self.interval = interval
        self.stage = "starting"
        self.started = time.monotonic()
        self.stop = threading.Event()

    def update(self, stage):
        self.stage = stage

    def emit(self):
        print(f"[running {time.monotonic() - self.started:.0f}s] {self.stage}", file=sys.stderr, flush=True)

    def run(self):
        while not self.stop.wait(self.interval):
            self.emit()

    def __enter__(self):
        self.emit()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()



def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*', value):
        raise ValueError('invalid_identifier')
    return value


def tree(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError('unsafe_directory')
    result = {}
    for entry in path.iterdir():
        if entry.is_symlink():
            raise ValueError('linked_entry')
        if entry.is_dir():
            result.update({entry.name + '/' + k: v for k, v in tree(entry).items()})
        elif entry.is_file():
            result[entry.name] = entry
        else:
            raise ValueError('special_entry')
    return result


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else _digest(stream)


def _digest(stream):
    h = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        h.update(chunk)
    return h.hexdigest()


def tree_stats(files):
    """Return a deterministic content fingerprint for one candidate tree."""
    fingerprint = hashlib.sha256()
    entries = {}
    logical_bytes = 0
    for relative, path in sorted(files.items()):
        size = path.stat().st_size
        value = digest(path)
        entries[relative] = (size, value)
        logical_bytes += size
        fingerprint.update(json.dumps([relative, size, value], separators=(',', ':')).encode('utf-8'))
        fingerprint.update(b'\n')
    return dict(files=len(entries), logical_bytes=logical_bytes,
                tree_sha256=fingerprint.hexdigest(), entries=entries)


def inspect(data, queue, progress=lambda stage: None):
    if queue['pending_snapshots'] != len(queue['items']):
        raise ValueError('queue_count_mismatch')
    pending = {(identifier(x['workspace_id']), identifier(x['snapshot_id'])) for x in queue['items']}
    public, staging = data / 'public', data / 'staging'
    for path in (data, public, staging, staging / '.commits'):
        if path.is_symlink() or not path.is_dir():
            raise ValueError('unsafe_root')
    progress('reading commit receipts')
    committed = set()
    for count, receipt in enumerate((staging / '.commits').iterdir(), 1):
        progress(f'reading commit receipts: {count}')
        if receipt.is_symlink() or not receipt.is_file():
            raise ValueError('invalid_receipt')
        item = json.loads(receipt.read_text())
        if set(item) != {'workspace_id', 'snapshot_id', 'body_sha256'} or not re.fullmatch('[0-9a-f]{64}', item['body_sha256']):
            raise ValueError('invalid_receipt')
        committed.add((identifier(item['workspace_id']), identifier(item['snapshot_id'])))
    report = dict(dry_run=True, candidates=[], protected=[], deferred=[], logical_bytes=0)
    currents = {}
    for workspace in sorted(public.iterdir()):
        progress(f'checking current: {workspace.name}')
        wid = identifier(workspace.name)
        if workspace.is_symlink() or not workspace.is_dir():
            raise ValueError('unsafe_workspace')
        current = workspace / 'current.json'
        snapshots = workspace / 'snapshots'
        if current.is_symlink() or snapshots.is_symlink() or not snapshots.is_dir():
            raise ValueError('unsafe_current')
        raw = current.read_bytes()
        sid = identifier(json.loads(raw)['snapshot_id'])
        tree(snapshots / sid)
        currents[current] = raw
        report['protected'].append(dict(workspace=wid, snapshot_id=sid, reason='current', path=str(snapshots / sid)))
        stage_workspace = staging / wid
        if stage_workspace.is_symlink():
            raise ValueError('unsafe_staging_workspace')
        for area, base in [('public', snapshots), ('staging', stage_workspace)]:
            if not base.exists():
                continue
            for snapshot in sorted(base.iterdir()):
                progress(f'checking {area}/{wid}/{snapshot.name}; candidates={len(report["candidates"])}')
                key = (wid, snapshot.name)
                row = dict(area=area, workspace=wid, snapshot_id=snapshot.name, path=str(snapshot))
                if snapshot.name == sid or key in pending:
                    report['protected'].append(dict(row, reason='current_or_pending'))
                    continue
                if key not in committed:
                    report['deferred'].append(dict(row, reason='commit_not_confirmed'))
                    continue
                files = tree(snapshot)
                if not files:
                    report['deferred'].append(dict(row, reason='empty_snapshot'))
                    continue
                stats = tree_stats(files)
                if area == 'staging' or (stage_workspace / snapshot.name).exists():
                    published = (snapshots if area == 'staging' else stage_workspace) / snapshot.name
                    if not published.is_dir():
                        report['deferred'].append(dict(row, reason='public_missing'))
                        continue
                    other = tree_stats(tree(published))
                    if stats['entries'] != other['entries']:
                        report['deferred'].append(dict(row, reason='public_mismatch'))
                        continue
                report['candidates'].append(dict(
                    row, files=stats['files'], logical_bytes=stats['logical_bytes'],
                    tree_sha256=stats['tree_sha256'],
                ))
                report['logical_bytes'] += stats['logical_bytes']
    # Unknown staging workspaces are never candidates.
    for entry in staging.iterdir():
        if entry.name not in {p.parent.name for p in currents}:
            report['deferred'].append(dict(path=str(entry), reason='receipt_or_unknown_workspace'))
    progress('rechecking current pointers')
    if any(path.read_bytes() != raw for path, raw in currents.items()):
        raise ValueError('current_changed')
    report['candidate_count'] = len(report['candidates'])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--queue-status', type=Path, required=True)
    parser.add_argument('--writers-stopped', action='store_true', required=True,
                        help='Confirm all senders, server and backup workers are stopped')
    args = parser.parse_args()
    try:
        with Progress() as progress:
            progress.update('reading queue status')
            report = inspect(args.data, json.loads(args.queue_status.read_text(encoding='utf-8-sig')), progress.update)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'[failed] {type(error).__name__}: {error}', file=sys.stderr, flush=True)
        print(json.dumps(dict(dry_run=True, error=type(error).__name__, candidates=[])))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'[completed] candidates={report["candidate_count"]} logical_bytes={report["logical_bytes"]}', file=sys.stderr, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
