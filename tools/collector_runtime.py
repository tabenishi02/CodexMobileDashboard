"""One-shot orchestration for the PC-side Codex dashboard collector."""
from __future__ import annotations

import hashlib
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Tuple

from tools.change_summary_generator import SummarySourceMessage, generate_change_summaries
from tools.chat_extractor import ChatExtractionResult, extract_chat_messages
from tools.collector_history import CollectorHistory, load_collector_history, save_collector_history
from tools.collector_state import load_collector_state, save_collector_state
from tools.decision_extractor import DecisionSourceMessage, extract_decisions
from tools.error_extractor import extract_development_errors
from tools.file_reference_extractor import extract_file_references
from tools.git_change_collector import collect_git_changes
from tools.https_sender import HttpsSnapshotSender, prepare_snapshot_uploads
from tools.inference_ledger import InferenceLedgerEntry, append as append_inference_ledger
from tools.incremental_collector import collect_incremental_records
from tools.json_converter import CollectorMetadata, JsonContext, ProjectPresentation, build_json_snapshot
from tools.json_writer import save_json_snapshot
from tools.next_task_extractor import InferenceMessage, NextTaskInferenceContext, extract_next_task
from tools.pending_snapshot_queue import PendingSnapshotQueue
from tools.session_reader import build_session_index, discover_session_files
from tools.work_status_extractor import extract_current_work_status


@dataclass(frozen=True)
class CollectorRuntimeSettings:
    allowed_roots: Tuple[Path, ...]
    sessions_dir: Path
    archived_sessions_dir: Path | None
    scan_archived_sessions: bool
    state_file: Path
    history_file: Path
    output_dir: Path
    queue_dir: Path
    tasks_path: Path
    ai_inference_mode: str
    inference_ledger_file: Path


def run_once(settings: CollectorRuntimeSettings, sender: HttpsSnapshotSender | None = None) -> int:
    """Build and persist one current Snapshot per permitted Git workspace.

    State and history advance only after a complete local JSON save. Sending is
    optional to keep local collection usable before Android is configured.
    """
    state = load_collector_state(settings.state_file)
    history = load_collector_history(settings.history_file)
    index = build_session_index(discover_session_files(settings.sessions_dir, settings.archived_sessions_dir, settings.scan_archived_sessions))
    workspaces: Dict[Path, list] = {}
    for entry in index.values():
        root = _workspace_root(entry.current_file.workspace_candidates, settings.allowed_roots)
        if root is not None:
            workspaces.setdefault(root, []).append(entry)
    next_state = state
    next_history = history
    completed = 0
    for root, entries in workspaces.items():
        workspace_id = _workspace_id(root)
        session_records = {}
        latest = max(entries, key=lambda entry: entry.current_file.last_timestamp or "")
        for entry in entries:
            result = collect_incremental_records(entry.session_id, entry.current_file.path, next_state)
            prior = next_history.records_for(entry.session_id)
            records = result.records if result.resume.replay_from_start else _merge_records(prior, result.records)
            next_history = next_history.replace(entry.session_id, records)
            next_state = result.next_state
            session_records[entry.session_id] = records
        _build_workspace_snapshot(root, workspace_id, latest.session_id, session_records, settings, sender)
        completed += 1
    save_collector_history(next_history, settings.history_file)
    save_collector_state(next_state, settings.state_file)
    return completed


def _build_workspace_snapshot(root: Path, workspace_id: str, latest_session_id: str, records_by_session: Dict[str, tuple], settings: CollectorRuntimeSettings, sender: HttpsSnapshotSender | None) -> None:
    chats_by_session = {session_id: extract_chat_messages(records, session_id) for session_id, records in records_by_session.items()}
    ordered_messages = tuple(sorted((message for value in chats_by_session.values() for message in value.messages), key=lambda message: (message.created_at or "", message.message_id)))
    chats = ChatExtractionResult(ordered_messages, tuple(issue for value in chats_by_session.values() for issue in value.issues), tuple(item for value in chats_by_session.values() for item in value.automatic_context_removals))
    all_records = tuple(record for records in records_by_session.values() for record in records)
    work = extract_current_work_status(all_records, ordered_messages, latest_session_id)
    decisions = extract_decisions(tuple(DecisionSourceMessage(session_id, message, _text(message)) for session_id, value in chats_by_session.items() for message in value.messages), workspace_id, allow_inference=settings.ai_inference_mode != "off")
    files = extract_file_references(tuple((session_id, message) for session_id, value in chats_by_session.items() for message in value.messages), root, workspace_id)
    errors = extract_development_errors(all_records, ordered_messages, work, workspace_id, latest_session_id)
    summaries = generate_change_summaries(latest_session_id, work, tuple(SummarySourceMessage(latest_session_id, message.message_id, message.turn_id, message.role, message.message_type, message.phase, _text(message), True) for message in ordered_messages), file_references=files.references, allow_inference=settings.ai_inference_mode != "off")
    for entry in summaries.cache_entries:
        if entry.summary is not None:
            append_inference_ledger(settings.inference_ledger_file, InferenceLedgerEntry(workspace_id, latest_session_id, entry.turn_id, entry.evidence_hash, {"origin": entry.summary.origin, "summary_id": entry.summary.summary_id}, now=datetime.now(timezone.utc).isoformat(timespec="seconds"), inference_kind="change_summary"))

    recent = tuple(ordered_messages[-2:])
    next_task = extract_next_task(recent, NextTaskInferenceContext(work.codex_status, tuple(InferenceMessage(message.message_id, message.role, _text(message)) for message in recent), tuple(item.title for item in decisions.decisions), tuple(), True), settings.tasks_path, allow_inference=settings.ai_inference_mode != "off")
    git = collect_git_changes(root, workspace_id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    context = JsonContext("snapshot-" + uuid.uuid4().hex, now, workspace_id, latest_session_id)
    snapshot = build_json_snapshot(context, ProjectPresentation(root.name, "Phase 3"), chats, work, next_task, errors, decisions, files, git, summaries, CollectorMetadata("ok", now, now, None, None), content_is_masked=True)
    saved = save_json_snapshot(snapshot, settings.output_dir)
    if sender is not None:
        queue = PendingSnapshotQueue(settings.queue_dir)
        uploads = prepare_snapshot_uploads(saved.workspace_directory, (item.path for item in saved.files))
        queue.send_or_enqueue(sender, workspace_id, context.snapshot_id, uploads, commit_delivery_id=str(uuid.uuid4()))


def _workspace_root(candidates: Iterable[str], allowed_roots: Tuple[Path, ...]) -> Path | None:
    for candidate in candidates:
        path = Path(candidate).resolve(strict=False)
        if not any(_within(path, root) and path != root for root in allowed_roots):
            continue
        completed = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False, shell=False, timeout=10)
        if completed.returncode == 0:
            root = Path(completed.stdout.strip()).resolve(strict=False)
            if any(_within(root, allowed) and root != allowed for allowed in allowed_roots):
                return root
    return None


def _within(path: Path, root: Path) -> bool:
    try: path.relative_to(root)
    except ValueError: return False
    return True


def _workspace_id(root: Path) -> str:
    return "workspace-" + hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]


def _merge_records(previous: tuple, appended: tuple) -> tuple:
    values = {(str(record.source_path).casefold(), record.start_offset): record for record in previous}
    values.update({(str(record.source_path).casefold(), record.start_offset): record for record in appended})
    return tuple(sorted(values.values(), key=lambda record: (record.timestamp or "", str(record.source_path), record.start_offset)))


def _text(message: object) -> str:
    return "\n".join(part.text or "" for part in message.content)


