"""Read-only Snapshot cleanup inventory. No deletion mode is provided."""
import argparse
import hashlib
import json
from pathlib import Path
import re


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


def inspect(data, queue):
    if queue['pending_snapshots'] != len(queue['items']):
        raise ValueError('queue_count_mismatch')
    pending = {(identifier(x['workspace_id']), identifier(x['snapshot_id'])) for x in queue['items']}
    public, staging = data / 'public', data / 'staging'
    for path in (data, public, staging, staging / '.commits'):
        if path.is_symlink() or not path.is_dir():
            raise ValueError('unsafe_root')
    committed = set()
    for receipt in (staging / '.commits').iterdir():
        if receipt.is_symlink() or not receipt.is_file():
            raise ValueError('invalid_receipt')
        item = json.loads(receipt.read_text())
        if set(item) != {'workspace_id', 'snapshot_id', 'body_sha256'} or not re.fullmatch('[0-9a-f]{64}', item['body_sha256']):
            raise ValueError('invalid_receipt')
        committed.add((identifier(item['workspace_id']), identifier(item['snapshot_id'])))
    report = dict(dry_run=True, candidates=[], protected=[], deferred=[], logical_bytes=0)
    currents = {}
    for workspace in sorted(public.iterdir()):
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
                if area == 'staging' or (stage_workspace / snapshot.name).exists():
                    published = (snapshots if area == 'staging' else stage_workspace) / snapshot.name
                    if not published.is_dir():
                        report['deferred'].append(dict(row, reason='public_missing'))
                        continue
                    other = tree(published)
                    if files.keys() != other.keys() or any(digest(p) != digest(other[k]) for k, p in files.items()):
                        report['deferred'].append(dict(row, reason='public_mismatch'))
                        continue
                size = sum(p.stat().st_size for p in files.values())
                report['candidates'].append(dict(row, files=len(files), logical_bytes=size))
                report['logical_bytes'] += size
    # Unknown staging workspaces are never candidates.
    for entry in staging.iterdir():
        if entry.name not in {p.parent.name for p in currents}:
            report['deferred'].append(dict(path=str(entry), reason='receipt_or_unknown_workspace'))
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
        report = inspect(args.data, json.loads(args.queue_status.read_text(encoding='utf-8-sig')))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps(dict(dry_run=True, error=type(error).__name__, candidates=[])))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
