"""Verify and stage a backup in a NEW directory; never write original paths."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import zipfile

from tools.backup import BackupError, digest, linked, temporary, verify_zip


def stage(archive, destination, side, expected_sha256, pair=None):
    archive, destination = Path(archive), Path(destination).absolute()
    if not re.fullmatch(r'[a-fA-F0-9]{64}', expected_sha256) or digest(archive) != expected_sha256.lower():
        raise BackupError('archive_hash_mismatch')
    manifest = verify_zip(archive, expected_side=side)
    if pair is not None:
        if pair.get('pair_state') != 'complete' or pair.get('backup_id') != manifest['backup_id']:
            raise BackupError('pair_mismatch')
        result = pair.get('pc' if side == 'PC' else 'android', {})
        if result.get('zip_sha256') != expected_sha256.lower() or result.get('zip_name') != archive.name:
            raise BackupError('pair_mismatch')
    if destination.exists() or linked(destination):
        raise BackupError('destination_exists_or_linked')
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            parts = info.filename.split('/')
            if temporary(Path(info.filename)) or any(p.endswith((' ', '.')) or p.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]} for p in parts):
                raise BackupError('restore_path_invalid')
        destination.mkdir(parents=True, mode=0o700)
        for entry in manifest['files']:
            target = destination / entry['path']
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with source.open(entry['path']) as incoming, target.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if digest(target) != entry['sha256']:
                raise BackupError('restored_hash_mismatch')
            if os.name == 'posix':
                os.chmod(target, int(entry['mode']) & 0o700)
        with (destination/'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream)
        (destination/'RESTORE_VERIFIED').write_text(manifest['backup_id'], encoding='ascii')
    return manifest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip',required=True,type=Path)
    parser.add_argument('--destination',required=True,type=Path)
    parser.add_argument('--side',required=True,choices=['PC','Android'])
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--pair',type=Path)
    args=parser.parse_args(argv)
    try:
        pair=json.loads(args.pair.read_text()) if args.pair else None
        manifest=stage(args.zip,args.destination,args.side,args.sha256,pair)
        print(json.dumps(dict(backup_id=manifest['backup_id'],side=args.side,state='staged_verified')))
        return 0
    except Exception as error:
        print(json.dumps(dict(error=str(error) if isinstance(error,BackupError) else 'restore_failed')))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
