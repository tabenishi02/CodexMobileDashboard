"""One-shot orchestration for the PC-side Codex dashboard collector."""
from __future__ import annotations

import hashlib
import logging
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Tuple

from tools.change_summary_generator import SummarySourceMessage, generate_change_summaries
from tools.chat_extractor import ChatExtractionResult, extract_chat_messages
from tools.collector_history import CollectorHistory, load_collector_history, save_collector_history
from tools.collector_state import (
    PendingInference,
    load_collector_state,
    replace_pending_inferences,
    save_collector_state,
)
from tools.decision_extractor import DecisionSourceMessage, decision_inference_payload, extract_decisions
from tools.error_extractor import extract_development_errors
from tools.file_reference_extractor import extract_file_references
from tools.git_change_collector import collect_git_changes
from tools.https_sender import HttpsSnapshotSender, prepare_snapshot_uploads
from tools.inference_ledger import InferenceLedgerEntry, append as append_inference_ledger, decision_inference_entries, latest_decision_history, load as load_inference_ledger, next_task_cache_entry, next_task_payload, summary_cache_entries, summary_payload
from tools.incremental_collector import collect_incremental_records
from tools.inference_metrics import record as record_inference_metric
from tools.json_converter import CollectorMetadata, JsonContext, ProjectPresentation, build_json_snapshot
from tools.json_writer import save_json_snapshot
from tools.next_task_extractor import InferenceMessage, NextTaskInferenceContext, extract_next_task
from tools.pending_snapshot_queue import PendingSnapshotQueue
from tools.session_reader import build_session_index, discover_session_files
from tools.work_status_extractor import extract_current_work_status

LOGGER = logging.getLogger("collector")


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
    max_calls_per_run: int = 3


class InferenceCallBudget:
    def __init__(self, limit: int = 3) -> None:
        if limit < 0:
            raise ValueError("inference_call_limit_invalid")
        self.limit = limit
        self.calls = 0
        self.deferred = 0

    def try_acquire(self) -> bool:
        if self.calls >= self.limit:
            self.deferred += 1
            record_inference_metric("collector", "limit_reached")
            return False
        self.calls += 1
        record_inference_metric("collector", "execution")
        return True


def run_once(settings: CollectorRuntimeSettings, sender: HttpsSnapshotSender | None = None) -> int:
    """Build and persist one current Snapshot per permitted Git workspace.

    State and history advance only after a complete local JSON save. Sending is
    optional to keep local collection usable before Android is configured.
    """
    state = load_collector_state(settings.state_file)
    history = load_collector_history(settings.history_file)
    known_session_ids = {cursor.session_id for cursor in state.sessions}
    index = build_session_index(discover_session_files(settings.sessions_dir, settings.archived_sessions_dir, settings.scan_archived_sessions))
    workspaces: Dict[Path, list] = {}
    for entry in index.values():
        root = _workspace_root(entry.current_file.workspace_candidates, settings.allowed_roots)
        if root is not None:
            workspaces.setdefault(root, []).append(entry)
    next_state = state
    next_history = history
    initial_pending = set(state.pending_inferences)
    next_pending = set(state.pending_inferences)
    inference_budget = InferenceCallBudget(
        getattr(settings, "max_calls_per_run", 3)
    )
    completed = 0
    for root, entries in workspaces.items():
        workspace_id = _workspace_id(root)
        session_records = {}
        inference_records = {}
        latest = max(entries, key=lambda entry: entry.current_file.last_timestamp or "")
        for entry in entries:
            result = collect_incremental_records(entry.session_id, entry.current_file.path, next_state)
            prior = next_history.records_for(entry.session_id)
            records = result.records if result.resume.replay_from_start else _merge_records(prior, result.records)
            next_history = next_history.replace(entry.session_id, records)
            next_state = result.next_state
            session_records[entry.session_id] = records
            inference_records[entry.session_id] = tuple() if settings.ai_inference_mode == "incremental" and entry.session_id not in known_session_ids else result.records
        workspace_pending = tuple(
            item for item in next_pending if item.workspace_id == workspace_id
        )
        updated_pending = _build_workspace_snapshot(
            root,
            workspace_id,
            latest.session_id,
            session_records,
            settings,
            sender,
            inference_records,
            inference_budget.try_acquire,
            workspace_pending,
        )
        if updated_pending is not None:
            next_pending.difference_update(workspace_pending)
            next_pending.update(updated_pending)
        completed += 1
    next_state = replace_pending_inferences(next_state, tuple(next_pending))
    save_collector_history(next_history, settings.history_file)
    save_collector_state(next_state, settings.state_file)
    LOGGER.info("inference_run_completed calls=%d deferred=%d limit=%d", inference_budget.calls, inference_budget.deferred, inference_budget.limit)
    LOGGER.info(
        "inference_pending_completed carried=%d added=%d remaining=%d",
        len(initial_pending),
        len(next_pending - initial_pending),
        len(next_pending),
    )
    return completed


def _build_workspace_snapshot(root: Path, workspace_id: str, latest_session_id: str, records_by_session: Dict[str, tuple], settings: CollectorRuntimeSettings, sender: HttpsSnapshotSender | None, inference_records_by_session: Dict[str, tuple] | None = None, can_infer=None, pending_inferences: Tuple[PendingInference, ...] = tuple()) -> Tuple[PendingInference, ...]:
    chats_by_session = {session_id: extract_chat_messages(records, session_id) for session_id, records in records_by_session.items()}
    ordered_messages = tuple(sorted((message for value in chats_by_session.values() for message in value.messages), key=lambda message: (message.created_at or "", message.message_id)))
    chats = ChatExtractionResult(ordered_messages, tuple(issue for value in chats_by_session.values() for issue in value.issues), tuple(item for value in chats_by_session.values() for item in value.automatic_context_removals))
    all_records = tuple(record for records in records_by_session.values() for record in records)
    work = extract_current_work_status(all_records, ordered_messages, latest_session_id)
    new_turn_ids = _incremental_completed_turn_ids(
        work, inference_records_by_session or {}
    ) if settings.ai_inference_mode == "incremental" else set()
    pending = set(pending_inferences)
    turn_sessions = {
        message.turn_id: session_id
        for session_id, value in chats_by_session.items()
        for message in value.messages
        if message.turn_id
    }
    if settings.ai_inference_mode == "incremental" and pending:
        active_turn_ids = {
            kind: {
                item.turn_id for item in pending if item.inference_kind == kind
            }
            for kind in ("decision", "change_summary", "next_task")
        }
        for turn_id in new_turn_ids:
            session_id = turn_sessions.get(turn_id, latest_session_id)
            for kind in ("decision", "change_summary", "next_task"):
                pending.add(PendingInference(workspace_id, session_id, turn_id, kind))
    else:
        active_turn_ids = {
            kind: set(new_turn_ids)
            for kind in ("decision", "change_summary", "next_task")
        }
    inference_turn_ids = (
        active_turn_ids["change_summary"]
        if settings.ai_inference_mode == "incremental"
        else None
    )
    ledger_entries = load_inference_ledger(settings.inference_ledger_file)
    decision_turn_ids = (
        active_turn_ids["decision"]
        if settings.ai_inference_mode == "incremental"
        else None
    )
    decision_sources = tuple(DecisionSourceMessage(session_id, message, _text(message)) for session_id, value in chats_by_session.items() for message in value.messages if decision_turn_ids is None or message.turn_id in decision_turn_ids)
    def save_decision_inference(entry):
        append_inference_ledger(settings.inference_ledger_file, InferenceLedgerEntry(workspace_id, entry.session_id, entry.turn_id, entry.input_sha256, decision_inference_payload(entry), datetime.now(timezone.utc).isoformat(timespec="seconds"), "decision"))
    decisions = extract_decisions(decision_sources, workspace_id, allow_inference=settings.ai_inference_mode != "off", inference_cache=decision_inference_entries(ledger_entries, workspace_id), restored_decisions=latest_decision_history(ledger_entries, workspace_id), on_inference_success=save_decision_inference, can_infer=can_infer)
    files = extract_file_references(tuple((session_id, message) for session_id, value in chats_by_session.items() for message in value.messages), root, workspace_id)
    errors = extract_development_errors(all_records, ordered_messages, work, workspace_id, latest_session_id)
    summaries = generate_change_summaries(latest_session_id, work, tuple(SummarySourceMessage(latest_session_id, message.message_id, message.turn_id, message.role, message.message_type, message.phase, _text(message), True) for message in ordered_messages), file_references=files.references, cache_entries=summary_cache_entries(ledger_entries, workspace_id, latest_session_id), inference_turn_ids=inference_turn_ids, allow_inference=settings.ai_inference_mode != "off", can_infer=can_infer)
    for entry in summaries.cache_entries:
        if entry.summary is not None:
            append_inference_ledger(settings.inference_ledger_file, InferenceLedgerEntry(workspace_id, latest_session_id, entry.turn_id, entry.evidence_hash, {"schema_version": 1, "payload": summary_payload(entry.summary)}, datetime.now(timezone.utc).isoformat(timespec="seconds"), "change_summary"))

    next_task_turn_ids = (
        active_turn_ids["next_task"]
        if settings.ai_inference_mode == "incremental"
        else None
    )
    inference_messages = tuple(
        message
        for message in ordered_messages
        if next_task_turn_ids is None or message.turn_id in next_task_turn_ids
    )
    recent = tuple(inference_messages[-2:])
    next_task = extract_next_task(recent, NextTaskInferenceContext(work.codex_status, tuple(InferenceMessage(message.message_id, message.role, _text(message)) for message in recent), tuple(item.title for item in decisions.decisions), tuple(), True), settings.tasks_path, cache_entry=next_task_cache_entry(ledger_entries, workspace_id, latest_session_id), allow_inference=settings.ai_inference_mode != "off" and (next_task_turn_ids is None or any(message.turn_id in next_task_turn_ids for message in recent)), can_infer=can_infer)
    if next_task.inference_attempted and next_task.cache_entry is not None and next_task.cache_entry.task is not None and next_task.cache_entry.task.origin == "codex_inferred":
        turn_id = recent[-1].turn_id or recent[-1].message_id if recent else latest_session_id
        append_inference_ledger(settings.inference_ledger_file, InferenceLedgerEntry(workspace_id, latest_session_id, turn_id, next_task.cache_entry.evidence_hash, next_task_payload(next_task.cache_entry), datetime.now(timezone.utc).isoformat(timespec="seconds"), "next_task"))
    git = collect_git_changes(root, workspace_id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    context = JsonContext("snapshot-" + uuid.uuid4().hex, now, workspace_id, latest_session_id)
    snapshot = build_json_snapshot(context, ProjectPresentation(root.name, "Phase 3"), chats, work, next_task, errors, decisions, files, git, summaries, CollectorMetadata("ok", now, now, None, None), content_is_masked=True)
    saved = save_json_snapshot(snapshot, settings.output_dir)
    if sender is not None:
        queue = PendingSnapshotQueue(settings.queue_dir)
        uploads = prepare_snapshot_uploads(saved.workspace_directory, (item.path for item in saved.files))
        queue.send_or_enqueue(sender, workspace_id, context.snapshot_id, uploads, commit_delivery_id=str(uuid.uuid4()))
    if settings.ai_inference_mode != "incremental":
        return tuple(sorted(pending, key=_pending_sort_key))
    decision_issue_turns = {
        source.message.turn_id
        for source in decision_sources
        for issue in getattr(decisions, "issues", ())
        if source.session_id == issue.session_id
        and source.message.message_id == issue.message_id
        and _is_retryable_inference_issue(issue.kind)
    }
    summary_issue_turns = {
        issue.turn_id
        for issue in getattr(summaries, "issues", ())
        if _is_retryable_inference_issue(issue.kind)
    }
    _replace_processed_pending(
        pending,
        workspace_id,
        latest_session_id,
        turn_sessions,
        "decision",
        active_turn_ids["decision"],
        decision_issue_turns,
    )
    _replace_processed_pending(
        pending,
        workspace_id,
        latest_session_id,
        turn_sessions,
        "change_summary",
        active_turn_ids["change_summary"],
        summary_issue_turns,
    )
    processed_next_turns = {recent[-1].turn_id} if recent and recent[-1].turn_id else set()
    next_issue_turns = processed_next_turns if any(
        _is_retryable_inference_issue(issue.kind)
        for issue in getattr(next_task, "issues", ())
    ) else set()
    _replace_processed_pending(
        pending,
        workspace_id,
        latest_session_id,
        turn_sessions,
        "next_task",
        processed_next_turns,
        next_issue_turns,
    )
    return tuple(sorted(pending, key=_pending_sort_key))


def _replace_processed_pending(
    pending,
    workspace_id,
    latest_session_id,
    turn_sessions,
    inference_kind,
    processed_turn_ids,
    failed_turn_ids,
):
    pending.difference_update(
        item
        for item in tuple(pending)
        if item.workspace_id == workspace_id
        and item.inference_kind == inference_kind
        and item.turn_id in processed_turn_ids
    )
    for turn_id in failed_turn_ids:
        pending.add(
            PendingInference(
                workspace_id,
                turn_sessions.get(turn_id, latest_session_id),
                turn_id,
                inference_kind,
            )
        )


def _is_retryable_inference_issue(kind: str) -> bool:
    return (
        kind == "inference_limit_reached"
        or kind.startswith("codex_")
        or kind.endswith("_not_masked")
    )


def _pending_sort_key(item: PendingInference):
    return (
        item.workspace_id,
        item.session_id,
        item.turn_id,
        item.inference_kind,
    )


def _incremental_completed_turn_ids(work_status, records_by_session):
    terminal_turn_ids = {
        record.turn_id
        for records in records_by_session.values()
        for record in records
        if (
            getattr(record, "category", None) == "turn"
            and getattr(record, "subtype", None) == "turn_status"
            and getattr(record, "turn_id", None)
            and getattr(record, "attributes", {}).get("status")
            in ("completed", "aborted")
        )
    }
    completed_turn_ids = {
        turn.turn_id
        for turn in getattr(work_status, "turns", ())
        if turn.status in ("completed", "failed") and not turn.rolled_back
    }
    return terminal_turn_ids & completed_turn_ids


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
