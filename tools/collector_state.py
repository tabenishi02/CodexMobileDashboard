"""Persist and validate incremental Codex JSONL read positions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from tools.record_deduplicator import RecordDeduplicationState


STATE_VERSION = 1


class InvalidCollectorStateError(ValueError):
    """Raised when persisted collector state cannot be safely used."""


@dataclass(frozen=True)
class SessionReadCursor:
    session_id: str
    complete_offset: int
    complete_line_number: int
    prefix_sha256: str
    deduplication: RecordDeduplicationState


@dataclass(frozen=True)
class PendingInference:
    workspace_id: str
    session_id: str
    turn_id: str
    inference_kind: str


@dataclass(frozen=True)
class CollectorState:
    sessions: Tuple[SessionReadCursor, ...] = tuple()
    pending_inferences: Tuple[PendingInference, ...] = tuple()


@dataclass(frozen=True)
class ResumePosition:
    start_offset: int
    start_line_number: int
    replay_from_start: bool
    reset_reason: Optional[str]
    deduplication: RecordDeduplicationState


def load_collector_state(path: Path) -> CollectorState:
    """Load state, returning an empty state when it has not been created yet."""

    path = Path(path)
    if not path.exists():
        return CollectorState()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidCollectorStateError("state_unreadable") from error
    if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
        raise InvalidCollectorStateError("state_version_invalid")
    entries = value.get("sessions")
    if not isinstance(entries, list):
        raise InvalidCollectorStateError("state_sessions_invalid")
    cursors = tuple(_cursor_from_value(entry) for entry in entries)
    if len({cursor.session_id for cursor in cursors}) != len(cursors):
        raise InvalidCollectorStateError("state_session_duplicate")
    pending_value = value.get("pending_inferences", [])
    if not isinstance(pending_value, list):
        raise InvalidCollectorStateError("state_pending_invalid")
    pending = tuple(_pending_from_value(entry) for entry in pending_value)
    if len(set(pending)) != len(pending):
        raise InvalidCollectorStateError("state_pending_duplicate")
    return CollectorState(
        tuple(sorted(cursors, key=lambda cursor: cursor.session_id)),
        tuple(sorted(pending, key=_pending_key)),
    )


def save_collector_state(state: CollectorState, path: Path) -> None:
    """Atomically replace the local state file with deterministic UTF-8 JSON."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "version": STATE_VERSION,
        "sessions": [_cursor_value(cursor) for cursor in state.sessions],
        "pending_inferences": [
            _pending_value(entry) for entry in state.pending_inferences
        ],
    }
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def resume_position(
    state: CollectorState, session_id: str, path: Path
) -> ResumePosition:
    """Return a validated incremental position or a safe replay position."""

    cursor = next(
        (item for item in state.sessions if item.session_id == session_id), None
    )
    if cursor is None:
        return ResumePosition(0, 0, False, None, RecordDeduplicationState())
    path = Path(path)
    try:
        file_size = path.stat().st_size
    except OSError:
        return ResumePosition(0, 0, True, "file_unavailable", cursor.deduplication)
    if file_size < cursor.complete_offset:
        return ResumePosition(0, 0, True, "file_truncated", cursor.deduplication)
    if _prefix_sha256(path, cursor.complete_offset) != cursor.prefix_sha256:
        return ResumePosition(0, 0, True, "file_replaced", cursor.deduplication)
    return ResumePosition(
        cursor.complete_offset,
        cursor.complete_line_number,
        False,
        None,
        cursor.deduplication,
    )


def advance_session_cursor(
    state: CollectorState,
    session_id: str,
    path: Path,
    complete_offset: int,
    complete_line_number: int,
    deduplication: RecordDeduplicationState,
) -> CollectorState:
    """Return state advanced only to a complete, validated JSONL position."""

    if not session_id:
        raise ValueError("session_id_missing")
    if complete_offset < 0 or complete_line_number < 0:
        raise ValueError("cursor_position_invalid")
    path = Path(path)
    try:
        file_size = path.stat().st_size
    except OSError:
        raise InvalidCollectorStateError("source_file_unavailable")
    if complete_offset > file_size:
        raise ValueError("cursor_offset_beyond_file")
    cursor = SessionReadCursor(
        session_id,
        complete_offset,
        complete_line_number,
        _prefix_sha256(path, complete_offset),
        deduplication,
    )
    cursors: Dict[str, SessionReadCursor] = {
        value.session_id: value for value in state.sessions
    }
    cursors[session_id] = cursor
    return CollectorState(
        tuple(sorted(cursors.values(), key=lambda value: value.session_id)),
        state.pending_inferences,
    )


def replace_pending_inferences(
    state: CollectorState,
    pending_inferences: Tuple[PendingInference, ...],
) -> CollectorState:
    unique = set(pending_inferences)
    if len(unique) != len(pending_inferences):
        raise ValueError("pending_inference_duplicate")
    return CollectorState(
        state.sessions,
        tuple(sorted(pending_inferences, key=_pending_key)),
    )


def _prefix_sha256(path: Path, length: int) -> str:
    digest = hashlib.sha256()
    remaining = length
    with path.open("rb") as stream:
        while remaining:
            data = stream.read(min(1024 * 1024, remaining))
            if not data:
                raise InvalidCollectorStateError("source_file_short_read")
            digest.update(data)
            remaining -= len(data)
    return digest.hexdigest()


def _cursor_value(cursor: SessionReadCursor) -> Dict[str, object]:
    return {
        "session_id": cursor.session_id,
        "complete_offset": cursor.complete_offset,
        "complete_line_number": cursor.complete_line_number,
        "prefix_sha256": cursor.prefix_sha256,
        "deduplication": {
            "identities": [list(value) for value in cursor.deduplication.identities],
            "physical_keys": list(cursor.deduplication.physical_keys),
            "fallback_occurrences": [
                list(value) for value in cursor.deduplication.fallback_occurrences
            ],
        },
    }


def _cursor_from_value(value: object) -> SessionReadCursor:
    if not isinstance(value, dict):
        raise InvalidCollectorStateError("state_session_invalid")
    session_id = value.get("session_id")
    offset = value.get("complete_offset")
    line_number = value.get("complete_line_number")
    prefix_sha256 = value.get("prefix_sha256")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(offset, int)
        or offset < 0
        or not isinstance(line_number, int)
        or line_number < 0
        or not isinstance(prefix_sha256, str)
        or len(prefix_sha256) != 64
    ):
        raise InvalidCollectorStateError("state_session_invalid")
    deduplication = _deduplication_from_value(value.get("deduplication"))
    return SessionReadCursor(
        session_id, offset, line_number, prefix_sha256, deduplication
    )


def _pending_value(entry: PendingInference) -> Dict[str, str]:
    return {
        "workspace_id": entry.workspace_id,
        "session_id": entry.session_id,
        "turn_id": entry.turn_id,
        "inference_kind": entry.inference_kind,
    }


def _pending_from_value(value: object) -> PendingInference:
    if not isinstance(value, dict):
        raise InvalidCollectorStateError("state_pending_invalid")
    fields = ("workspace_id", "session_id", "turn_id", "inference_kind")
    if not all(isinstance(value.get(field), str) and value[field] for field in fields):
        raise InvalidCollectorStateError("state_pending_invalid")
    if value["inference_kind"] not in (
        "change_summary",
        "decision",
        "next_task",
        "combined_turn",
    ):
        raise InvalidCollectorStateError("state_pending_invalid")
    return PendingInference(
        value["workspace_id"],
        value["session_id"],
        value["turn_id"],
        value["inference_kind"],
    )


def _pending_key(entry: PendingInference) -> Tuple[str, str, str, str]:
    return (
        entry.workspace_id,
        entry.session_id,
        entry.turn_id,
        entry.inference_kind,
    )


def _deduplication_from_value(value: object) -> RecordDeduplicationState:
    if not isinstance(value, dict):
        raise InvalidCollectorStateError("state_deduplication_invalid")
    identities = _string_pairs(value.get("identities"))
    physical_keys = _strings(value.get("physical_keys"))
    occurrences = _string_integer_pairs(value.get("fallback_occurrences"))
    return RecordDeduplicationState(identities, physical_keys, occurrences)


def _string_pairs(value: object) -> Tuple[Tuple[str, str], ...]:
    if not isinstance(value, list):
        raise InvalidCollectorStateError("state_deduplication_invalid")
    pairs = []
    for item in value:
        if not (
            isinstance(item, list)
            and len(item) == 2
            and all(isinstance(part, str) and len(part) == 64 for part in item)
        ):
            raise InvalidCollectorStateError("state_deduplication_invalid")
        pairs.append((item[0], item[1]))
    return tuple(pairs)


def _strings(value: object) -> Tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and len(item) == 64 for item in value
    ):
        raise InvalidCollectorStateError("state_deduplication_invalid")
    return tuple(value)


def _string_integer_pairs(value: object) -> Tuple[Tuple[str, int], ...]:
    if not isinstance(value, list):
        raise InvalidCollectorStateError("state_deduplication_invalid")
    pairs = []
    for item in value:
        if not (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and len(item[0]) == 64
            and isinstance(item[1], int)
            and item[1] >= 0
        ):
            raise InvalidCollectorStateError("state_deduplication_invalid")
        pairs.append((item[0], item[1]))
    return tuple(pairs)
