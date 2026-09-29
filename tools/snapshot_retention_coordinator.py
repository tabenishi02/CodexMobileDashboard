"""Run reviewed Android Snapshot retention while the PC collector is paused."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
import tempfile
import uuid

from tools.backup import BackupError, CollectorMutex, read_config
from tools.collector import _load_settings, _runtime_settings
from tools.pending_snapshot_queue import PendingSnapshotQueue


class RetentionCoordinatorError(ValueError):
    pass


def _safe_remote_path(value):
    path = PurePosixPath(value)
    if not path.is_absolute() or any(
            part in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]+", part)
            for part in path.parts[1:]):
        raise RetentionCoordinatorError("remote_path_invalid")
    return str(path)


def _queue_status(config_path):
    settings = _load_settings(config_path)
    pending = PendingSnapshotQueue(settings["queue_dir"]).pending()
    return {
        "pending_snapshots": len(pending),
        "items": [
            {
                "sequence": item.sequence,
                "workspace_id": item.workspace_id,
                "snapshot_id": item.snapshot_id,
                "file_count": len(item.uploads),
                "last_error_kind": item.last_error_kind,
                "last_error_status": item.last_error_status,
            }
            for item in pending
        ],
    }


def _run(command, *, timeout):
    try:
        result = subprocess.run(
            command, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RetentionCoordinatorError("remote_unavailable") from error
    if result.returncode:
        raise RetentionCoordinatorError("remote_cleanup_failed")
    return result.stdout


def _remote_cleanup(config, queue_file, run_id):
    host = config.get("android", "ssh_host")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host):
        raise RetentionCoordinatorError("ssh_alias_invalid")
    repository = _safe_remote_path(config.get("android", "repository"))
    repository_path = PurePosixPath(repository)
    data = str(repository_path.parent / "data")
    cache = str(repository_path.parents[1] / ".cache" / "codex-mobile-dashboard")
    timeout = config.getint("android", "full_wait_seconds", fallback=14400)
    if not 1 <= timeout <= 86400:
        raise RetentionCoordinatorError("limits_invalid")
    options = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10"]
    remote_queue = f"{cache}/retention-{run_id}-queue.json"
    _run(["ssh", *options, host, f"mkdir -p {shlex.quote(cache)}"], timeout=30)
    _run(["scp", "-q", *options, str(queue_file), f"{host}:{remote_queue}"], timeout=30)
    command = "\n".join((
        "set -eu",
        f"cd {shlex.quote(repository)}",
        f"queue=\"{remote_queue}\"",
        f"report=\"{cache}/retention-{run_id}-report.json\"",
        f"partial=\"{cache}/retention-{run_id}-result.partial\"",
        f"result=\"{cache}/retention-latest-result.json\"",
        f"log=\"{cache}/retention-latest.log\"",
        "trap 'rm -f \"$queue\" \"$report\" \"$partial\"' EXIT",
        ": > \"$log\"",
        "python -m tools.snapshot_retention "
        f"--data {shlex.quote(data)} --queue-status \"$queue\" "
        f"--backup-lock {shlex.quote(cache + '/backup.lock')} "
        "--dry-run > \"$report\" 2>> \"$log\"",
        "python -m tools.snapshot_retention "
        f"--data {shlex.quote(data)} --queue-status \"$queue\" --approved-report \"$report\" "
        f"--backup-lock {shlex.quote(cache + '/backup.lock')} "
        "--apply > \"$partial\" 2>> \"$log\"",
        "mv \"$partial\" \"$result\"",
        "cat \"$result\"",
    ))
    raw = _run(["ssh", *options, host, command], timeout=timeout)
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RetentionCoordinatorError("remote_result_invalid") from error
    if result.get("state") != "completed" or result.get("policy") != "retention-v1":
        raise RetentionCoordinatorError("remote_result_invalid")
    return result


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with partial.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(partial, path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    result_path = None
    try:
        config = read_config(args.config)
        collector_config = Path(os.path.expandvars(config.get("backup", "collector_config")))
        runtime = _runtime_settings(collector_config, _load_settings(collector_config))
        result_path = runtime.state_file.parent / "snapshot-retention-result.json"
        wait = config.getint("backup", "mutex_wait_seconds", fallback=300)
        if not 0 <= wait <= 86400:
            raise RetentionCoordinatorError("limits_invalid")
        with CollectorMutex(wait):
            queue = _queue_status(collector_config)
            with tempfile.TemporaryDirectory(prefix="dashboard-retention-") as directory:
                queue_file = Path(directory) / "queue.json"
                queue_file.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
                remote = _remote_cleanup(config, queue_file, run_id)
        value = {
            "run_id": run_id,
            "state": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "pending_snapshots": queue["pending_snapshots"],
            **remote,
        }
        _save(result_path, value)
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, BackupError) as error:
        value = {
            "run_id": run_id,
            "state": "failed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(error) if isinstance(error, (RetentionCoordinatorError, BackupError)) else type(error).__name__,
        }
        if result_path is not None:
            try:
                _save(result_path, value)
            except OSError:
                pass
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())