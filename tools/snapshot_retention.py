"""Retention-based Android Snapshot cleanup with dry-run and reviewed apply."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time
import uuid

if os.name == "posix":
    import fcntl

try:
    from tools.snapshot_cleanup_apply import acquire_exclusive_lock
    from tools.snapshot_cleanup_inventory import Progress, identifier, tree, tree_stats
except ModuleNotFoundError as error:
    if error.name != "tools":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.snapshot_cleanup_apply import acquire_exclusive_lock
    from tools.snapshot_cleanup_inventory import Progress, identifier, tree, tree_stats

POLICY = "retention-v1"
PUBLIC_AGE_SECONDS = 3600
STAGING_AGE_SECONDS = 7 * 24 * 3600
RECEIPT_AGE_SECONDS = 7 * 24 * 3600
FUTURE_TOLERANCE_SECONDS = 300
KEEP_PREVIOUS = 2
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PATH_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_MODULUS = 1 << 256


def _queue_keys(queue):
    if not isinstance(queue, dict) or set(queue) != {"pending_snapshots", "items"}:
        raise ValueError("queue_invalid")
    items = queue["items"]
    if isinstance(queue["pending_snapshots"], bool) or not isinstance(queue["pending_snapshots"], int):
        raise ValueError("queue_invalid")
    if not isinstance(items, list) or queue["pending_snapshots"] != len(items):
        raise ValueError("queue_count_mismatch")
    return {(identifier(item["workspace_id"]), identifier(item["snapshot_id"])) for item in items}


def _safe_root(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError("unsafe_root")
    return path


def _snapshot(path, area, workspace, snapshot_id, now_ns):
    identifier(workspace)
    identifier(snapshot_id)
    files = tree(path)
    if not files:
        return dict(kind="snapshot", area=area, workspace=workspace, snapshot_id=snapshot_id,
                    path=str(path), empty=True)
    stats = tree_stats(files)
    activity_ns = max([path.stat().st_mtime_ns, *(item.stat().st_mtime_ns for item in files.values())])
    return dict(kind="snapshot", area=area, workspace=workspace, snapshot_id=snapshot_id,
                path=str(path), files=stats["files"], logical_bytes=stats["logical_bytes"],
                tree_sha256=stats["tree_sha256"], activity_ns=activity_ns,
                future=activity_ns > now_ns + FUTURE_TOLERANCE_SECONDS * 1_000_000_000)


def _receipt_identity(path, kind, body):
    if path.is_symlink() or not path.is_file():
        raise ValueError("invalid_receipt")
    try:
        if str(uuid.UUID(path.stem)) != path.stem or path.suffix != ".json":
            raise ValueError("invalid_receipt")
    except ValueError as error:
        raise ValueError("invalid_receipt") from error
    required = ({"workspace_id", "snapshot_id", "relative_json_path", "body_sha256"}
                if kind == "deliveries" else {"workspace_id", "snapshot_id", "body_sha256"})
    if not isinstance(body, dict) or set(body) != required:
        raise ValueError("invalid_receipt")
    workspace = identifier(body["workspace_id"])
    snapshot = identifier(body["snapshot_id"])
    if not isinstance(body["body_sha256"], str) or not _SHA256.fullmatch(body["body_sha256"]):
        raise ValueError("invalid_receipt")
    if kind == "deliveries":
        relative = body["relative_json_path"]
        if not isinstance(relative, str):
            raise ValueError("invalid_receipt")
        parts = PurePosixPath(relative).parts
        if (not parts or PurePosixPath(relative).is_absolute() or
                any(not _PATH_COMPONENT.fullmatch(part) or part in (".", "..") for part in parts) or
                not parts[-1].endswith(".json")):
            raise ValueError("invalid_receipt")
    return workspace, snapshot


def _new_group():
    return dict(count=0, logical_bytes=0, fingerprint=0, latest_ns=0,
                old_count=0, old_logical_bytes=0, old_fingerprint=0, paths=[], old_paths=[])


def _add_receipt(group, path, body, mtime_ns, cutoff_ns, include_paths):
    digest = hashlib.sha256(body).hexdigest()
    token = hashlib.sha256(
        json.dumps([path.name, len(body), digest], separators=(",", ":")).encode("utf-8")
    ).digest()
    value = int.from_bytes(token, "big")
    group["count"] += 1
    group["logical_bytes"] += len(body)
    group["fingerprint"] = (group["fingerprint"] + value) % _MODULUS
    group["latest_ns"] = max(group["latest_ns"], mtime_ns)
    if include_paths:
        group["paths"].append(path)
    if mtime_ns <= cutoff_ns:
        group["old_count"] += 1
        group["old_logical_bytes"] += len(body)
        group["old_fingerprint"] = (group["old_fingerprint"] + value) % _MODULUS
        if include_paths:
            group["old_paths"].append(path)


def _receipt_groups(directory, kind, now_ns, include_paths=False, progress=lambda stage: None):
    _safe_root(directory)
    cutoff_ns = now_ns - RECEIPT_AGE_SECONDS * 1_000_000_000
    groups = {}
    for count, path in enumerate(directory.iterdir(), 1):
        progress(f"reading {kind} receipts: {count}")
        if path.is_symlink() or not path.is_file():
            raise ValueError("invalid_receipt")
        raw = path.read_bytes()
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid_receipt") from error
        key = _receipt_identity(path, kind, body)
        mtime_ns = path.stat().st_mtime_ns
        if mtime_ns > now_ns + FUTURE_TOLERANCE_SECONDS * 1_000_000_000:
            raise ValueError("future_receipt")
        _add_receipt(groups.setdefault(key, _new_group()), path, raw, mtime_ns, cutoff_ns, include_paths)
    return groups


def _receipt_candidate(area, key, group, *, old_only, reason):
    prefix = "old_" if old_only else ""
    return dict(kind="receipt", area=area, workspace=key[0], snapshot_id=key[1],
                path=f"{area}:{key[0]}/{key[1]}", reason=reason,
                files=group[prefix + "count"], logical_bytes=group[prefix + "logical_bytes"],
                tree_sha256=f'{group[prefix + "fingerprint"]:064x}', old_only=old_only)


def _snapshot_signature(item):
    return (item["kind"], item["area"], item["workspace"], item["snapshot_id"], item["path"],
            item["files"], item["logical_bytes"], item["tree_sha256"])


def candidate_signature(report):
    if report.get("dry_run") is not True or report.get("policy") != POLICY:
        raise ValueError("approved_report_invalid")
    candidates = report.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("approved_report_invalid")
    return {_snapshot_signature(item) for item in candidates}


def build_plan(data, queue, *, now_ns=None, progress=lambda stage: None, include_receipt_paths=False):
    now_ns = time.time_ns() if now_ns is None else now_ns
    pending = _queue_keys(queue)
    public = _safe_root(data / "public")
    staging = _safe_root(data / "staging")
    deliveries = _safe_root(staging / ".deliveries")
    commits = _safe_root(staging / ".commits")
    report = dict(policy=POLICY, dry_run=True, evaluated_at_ns=now_ns,
                  evaluated_at=datetime.fromtimestamp(now_ns / 1_000_000_000, timezone.utc).isoformat(),
                  candidates=[], protected=[], deferred=[], logical_bytes=0, receipt_file_count=0)
    currents = {}
    public_records = {}
    staging_records = {}
    workspace_ids = set()
    unknown_workspaces = set()

    progress("reading public snapshots")
    for workspace_path in sorted(public.iterdir()):
        if workspace_path.is_symlink() or not workspace_path.is_dir():
            raise ValueError("unsafe_workspace")
        workspace = identifier(workspace_path.name)
        workspace_ids.add(workspace)
        current_path = workspace_path / "current.json"
        snapshots = _safe_root(workspace_path / "snapshots")
        raw = current_path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict) or set(value) not in ({"snapshot_id"}, {"snapshot_id", "received_at"}):
            raise ValueError("unsafe_current")
        current_id = identifier(value["snapshot_id"])
        if not (snapshots / current_id).is_dir():
            raise ValueError("unsafe_current")
        currents[(workspace, current_id)] = (current_path, raw)
        for path in sorted(snapshots.iterdir()):
            record = _snapshot(path, "public", workspace, identifier(path.name), now_ns)
            public_records[(workspace, path.name)] = record

    progress("reading staging snapshots")
    for entry in sorted(staging.iterdir()):
        if entry.name in (".deliveries", ".commits"):
            continue
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError("unsafe_staging_workspace")
        if entry.name not in workspace_ids:
            unknown_workspaces.add(entry.name)
            report["deferred"].append(dict(path=str(entry), reason="unknown_workspace"))
            continue
        workspace = identifier(entry.name)
        for path in sorted(entry.iterdir()):
            record = _snapshot(path, "staging", workspace, identifier(path.name), now_ns)
            staging_records[(workspace, path.name)] = record

    delivery_groups = _receipt_groups(deliveries, "deliveries", now_ns, include_receipt_paths, progress)
    commit_groups = _receipt_groups(commits, "commits", now_ns, include_receipt_paths, progress)
    report["receipt_file_count"] = sum(group["count"] for group in delivery_groups.values()) + sum(group["count"] for group in commit_groups.values())
    commit_keys = set(commit_groups)
    current_keys = set(currents)
    public_delete = set()
    staging_delete = set()
    mismatched = set()

    for key in set(public_records) & set(staging_records):
        left, right = public_records[key], staging_records[key]
        if left.get("empty") or right.get("empty") or any(
            left.get(name) != right.get(name) for name in ("files", "logical_bytes", "tree_sha256")
        ):
            mismatched.add(key)

    by_workspace = {}
    for key, item in public_records.items():
        by_workspace.setdefault(key[0], []).append((key, item))
    previous = set()
    for workspace, values in by_workspace.items():
        current_id = next(key[1] for key in current_keys if key[0] == workspace)
        ordered = sorted((value for value in values if value[0][1] != current_id),
                         key=lambda value: (value[1].get("activity_ns", -1), value[0][1]), reverse=True)
        previous.update(key for key, _ in ordered[:KEEP_PREVIOUS])

    public_cutoff = now_ns - PUBLIC_AGE_SECONDS * 1_000_000_000
    staging_cutoff = now_ns - STAGING_AGE_SECONDS * 1_000_000_000
    for key, item in public_records.items():
        if item.get("empty"):
            report["deferred"].append(dict(item, reason="empty_snapshot"))
        elif item["future"]:
            report["deferred"].append(dict(item, reason="future_activity"))
        elif key in mismatched:
            report["deferred"].append(dict(item, reason="public_mismatch"))
        elif key in current_keys:
            report["protected"].append(dict(item, reason="current"))
        elif key in pending:
            report["protected"].append(dict(item, reason="pending"))
        elif key in previous:
            report["protected"].append(dict(item, reason="previous_generation"))
        elif item["activity_ns"] > public_cutoff:
            report["protected"].append(dict(item, reason="retention_age"))
        elif key not in commit_keys:
            report["deferred"].append(dict(item, reason="commit_not_confirmed"))
        else:
            public_delete.add(key)
            report["candidates"].append(dict(item, reason="expired_public"))

    for key, item in staging_records.items():
        published = key in public_records
        cutoff = public_cutoff if published else staging_cutoff
        if item.get("empty"):
            report["deferred"].append(dict(item, reason="empty_snapshot"))
        elif item["future"]:
            report["deferred"].append(dict(item, reason="future_activity"))
        elif key in mismatched:
            report["deferred"].append(dict(item, reason="public_mismatch"))
        elif key in current_keys:
            report["protected"].append(dict(item, reason="current"))
        elif key in pending:
            report["protected"].append(dict(item, reason="pending"))
        elif item["activity_ns"] > cutoff:
            report["protected"].append(dict(item, reason="retention_age"))
        elif published:
            staging_delete.add(key)
            report["candidates"].append(dict(item, reason="expired_published_staging"))
        elif key in commit_keys:
            report["deferred"].append(dict(item, reason="committed_public_missing"))
        else:
            staging_delete.add(key)
            report["candidates"].append(dict(item, reason="expired_unfinished_staging"))

    data_keys = set(public_records) | set(staging_records)
    receipt_candidates = []
    for area, groups, related_delete in (
        ("deliveries", delivery_groups, staging_delete),
        ("commits", commit_groups, public_delete),
    ):
        for key, group in groups.items():
            if key[0] in unknown_workspaces:
                report["deferred"].append(dict(kind="receipt", area=area, workspace=key[0], snapshot_id=key[1], reason="unknown_workspace"))
            elif key in current_keys or key in pending:
                report["protected"].append(dict(kind="receipt", area=area, workspace=key[0], snapshot_id=key[1], reason="current_or_pending"))
            elif key in related_delete:
                receipt_candidates.append(_receipt_candidate(area, key, group, old_only=False, reason="related_snapshot_deleted"))
            elif key not in data_keys and group["old_count"]:
                receipt_candidates.append(_receipt_candidate(area, key, group, old_only=True, reason="expired_orphan_receipt"))
            else:
                report["protected"].append(dict(kind="receipt", area=area, workspace=key[0], snapshot_id=key[1], reason="retained_data_or_age"))

    snapshots = report["candidates"]
    report["candidates"] = receipt_candidates + [item for item in snapshots if item["area"] == "staging"] + [item for item in snapshots if item["area"] == "public"]
    report["logical_bytes"] = sum(item["logical_bytes"] for item in report["candidates"])
    report["candidate_count"] = len(report["candidates"])
    if any(path.read_bytes() != raw for path, raw in currents.values()):
        raise ValueError("current_changed")
    return report, {path: raw for path, raw in currents.values()}, {"deliveries": delivery_groups, "commits": commit_groups}


def _receipt_item_signature(group, old_only):
    prefix = "old_" if old_only else ""
    return (group[prefix + "count"], group[prefix + "logical_bytes"], f'{group[prefix + "fingerprint"]:064x}')


def apply_plan(data, queue, approved, progress=lambda stage: None):
    now_ns = approved.get("evaluated_at_ns")
    if isinstance(now_ns, bool) or not isinstance(now_ns, int):
        raise ValueError("approved_report_invalid")
    fresh, currents, groups = build_plan(data, queue, now_ns=now_ns, progress=progress, include_receipt_paths=True)
    if candidate_signature(fresh) != candidate_signature(approved):
        raise ValueError("candidate_set_changed")
    if fresh["logical_bytes"] != approved.get("logical_bytes"):
        raise ValueError("candidate_total_changed")
    removed = 0
    removed_files = 0
    removed_bytes = 0
    for item in fresh["candidates"]:
        if any(path.read_bytes() != raw for path, raw in currents.items()):
            raise RuntimeError("current_changed_during_cleanup")
        progress(f'deleting {removed + 1}/{len(fresh["candidates"])}: {item["area"]}/{item["workspace"]}/{item["snapshot_id"]}')
        if item["kind"] == "receipt":
            group = groups[item["area"]][(item["workspace"], item["snapshot_id"])]
            if _receipt_item_signature(group, item["old_only"]) != (item["files"], item["logical_bytes"], item["tree_sha256"]):
                raise RuntimeError("candidate_content_changed")
            paths = group["old_paths"] if item["old_only"] else group["paths"]
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    raise RuntimeError("candidate_path_changed")
                path.unlink()
            removed_files += len(paths)
        else:
            target = Path(item["path"])
            expected = data / item["area"] / item["workspace"]
            if item["area"] == "public":
                expected = expected / "snapshots" / item["snapshot_id"]
            else:
                expected = expected / item["snapshot_id"]
            if target != expected or target.is_symlink() or not target.is_dir():
                raise RuntimeError("candidate_path_changed")
            actual = _snapshot(target, item["area"], item["workspace"], item["snapshot_id"], now_ns)
            if _snapshot_signature(actual) != _snapshot_signature(item):
                raise RuntimeError("candidate_content_changed")
            if any(path.read_bytes() != raw for path, raw in currents.items()):
                raise RuntimeError("current_changed_during_cleanup")
            shutil.rmtree(target)
            removed_files += item["files"]
        removed += 1
        removed_bytes += item["logical_bytes"]
    return dict(state="completed", policy=POLICY, removed_count=removed,
                removed_file_count=removed_files, removed_logical_bytes=removed_bytes,
                protected_current_count=len(currents))


def _public_report(report):
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--queue-status", type=Path, required=True)
    parser.add_argument("--approved-report", type=Path)
    parser.add_argument("--backup-lock", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if os.name != "posix":
        print("[failed] posix_required", file=sys.stderr)
        return 1
    args.backup_lock.parent.mkdir(parents=True, exist_ok=True)
    lock = args.backup_lock.open("a+")
    try:
        acquire_exclusive_lock(lock, fcntl)
        queue = json.loads(args.queue_status.read_text(encoding="utf-8-sig"))
        with Progress() as progress:
            if args.dry_run:
                report, _, _ = build_plan(args.data, queue, progress=progress.update)
                result = _public_report(report)
            else:
                if args.approved_report is None:
                    raise ValueError("approved_report_required")
                approved = json.loads(args.approved_report.read_text(encoding="utf-8-sig"))
                result = apply_plan(args.data, queue, approved, progress.update)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f"[failed] {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        lock.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f'[completed] state={result.get("state", "dry_run")} count={result.get("removed_count", result.get("candidate_count", 0))}', file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())