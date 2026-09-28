"""Inspect candidates, or delete only those matching a reviewed dry-run report."""

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

if os.name == 'posix':
    import fcntl

try:
    from tools.snapshot_cleanup_inventory import Progress, inspect, tree, tree_stats
except ModuleNotFoundError as error:
    if error.name != "tools":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.snapshot_cleanup_inventory import Progress, inspect, tree, tree_stats


def acquire_exclusive_lock(handle, lock_api):
    """Acquire the shared maintenance file exclusively without waiting."""
    try:
        lock_api.flock(handle.fileno(), lock_api.LOCK_EX | lock_api.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("maintenance_busy") from None


def candidate_signature(report):
    candidates = report.get("candidates")
    if report.get("dry_run") is not True or not isinstance(candidates, list):
        raise ValueError("approved_report_invalid")
    return {
        (
            item["area"], item["workspace"], item["snapshot_id"],
            item["path"], item["files"], item["logical_bytes"], item["tree_sha256"],
        )
        for item in candidates
    }


def running_server(server_script):
    expected = str(server_script.resolve())
    proc = Path("/proc")
    if not proc.is_dir():
        raise RuntimeError("proc_unavailable")
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            args = entry.joinpath("cmdline").read_bytes().split(b"\0")
            decoded = [value.decode("utf-8") for value in args if value]
        except (OSError, UnicodeDecodeError):
            continue
        if expected in decoded:
            return True
    return False


def apply_cleanup(data, queue, approved, progress=lambda stage: None):
    fresh = inspect(data, queue, progress)
    if candidate_signature(fresh) != candidate_signature(approved):
        raise ValueError("candidate_set_changed")
    if fresh["logical_bytes"] != approved.get("logical_bytes"):
        raise ValueError("candidate_total_changed")

    current_bytes = {}
    for item in fresh["protected"]:
        if item.get("reason") == "current":
            current = data / "public" / item["workspace"] / "current.json"
            current_bytes[current] = current.read_bytes()

    removed = 0
    removed_bytes = 0
    candidates = fresh["candidates"]
    for item in candidates:
        if any(path.read_bytes() != body for path, body in current_bytes.items()):
            raise RuntimeError("current_changed_during_cleanup")
        target = Path(item["path"])
        expected = (
            data / "public" / item["workspace"] / "snapshots" / item["snapshot_id"]
            if item["area"] == "public"
            else data / "staging" / item["workspace"] / item["snapshot_id"]
        )
        if target != expected or target.is_symlink() or not target.is_dir():
            raise RuntimeError("candidate_path_changed")
        actual = tree_stats(tree(target))
        if (actual["files"], actual["logical_bytes"], actual["tree_sha256"]) != (
            item["files"], item["logical_bytes"], item["tree_sha256"],
        ):
            raise RuntimeError("candidate_content_changed")
        progress(f'deleting {removed + 1}/{len(candidates)}: {item["area"]}/{item["workspace"]}/{item["snapshot_id"]}')
        shutil.rmtree(target)
        removed += 1
        removed_bytes += item["logical_bytes"]

    if any(path.read_bytes() != body for path, body in current_bytes.items()):
        raise RuntimeError("current_changed_during_cleanup")
    return {
        "state": "completed",
        "removed_count": removed,
        "removed_logical_bytes": removed_bytes,
        "protected_current_count": len(current_bytes),
    }


def execute_cleanup(data, queue, approved=None, *, apply=False, progress=lambda stage: None):
    """Evaluate candidates without deletion, or apply one reviewed report."""
    if not apply:
        return inspect(data, queue, progress)
    if approved is None:
        raise ValueError("approved_report_required")
    return apply_cleanup(data, queue, approved, progress)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--queue-status", type=Path, required=True)
    parser.add_argument("--approved-report", type=Path)
    parser.add_argument("--server-pid-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--server-script", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--backup-lock", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if os.name != "posix":
        print("[failed] posix_required", file=sys.stderr)
        return 1
    args.backup_lock.parent.mkdir(parents=True, exist_ok=True)
    lock = args.backup_lock.open("a+")
    try:
        acquire_exclusive_lock(lock, fcntl)
        queue = json.loads(args.queue_status.read_text(encoding="utf-8-sig"))
        with Progress() as progress:
            approved = (json.loads(args.approved_report.read_text(encoding="utf-8-sig"))
                        if args.approved_report is not None else None)
            result = execute_cleanup(
                args.data, queue, approved, apply=args.apply, progress=progress.update,
            )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"[failed] {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        lock.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.dry_run:
        print(f'[completed] dry_run candidates={result["candidate_count"]}', file=sys.stderr, flush=True)
    else:
        print(f'[completed] removed={result["removed_count"]}', file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
