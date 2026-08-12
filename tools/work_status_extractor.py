"""Extract the current Codex work status and turn lifecycle."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from tools.chat_extractor import ExtractedChatMessage
from tools.record_normalizer import NormalizedRecord


LOGGER = logging.getLogger("converter")
CURRENT_WORK_MAX_CHARACTERS = 500


@dataclass(frozen=True)
class WorkStatusIssue:
    path: Path
    line_number: int
    offset: int
    kind: str


@dataclass(frozen=True)
class TurnWorkState:
    turn_id: str
    turn_id_source: str
    status: str
    started_at: Optional[str]
    started_at_source: str
    completed_at: Optional[str]
    completed_at_source: str
    duration_ms: Optional[int]
    reason: Optional[str]
    user_message_id: Optional[str]
    assistant_message_ids: Tuple[str, ...]
    rolled_back: bool


@dataclass(frozen=True)
class CurrentWorkStatus:
    codex_status: str
    current_work: Optional[str]
    current_work_message_id: Optional[str]
    active_turn_id: Optional[str]
    latest_turn_id: Optional[str]
    latest_turn_status: Optional[str]
    last_event_at: Optional[str]
    turns: Tuple[TurnWorkState, ...]
    issues: Tuple[WorkStatusIssue, ...]


@dataclass
class _TurnBuilder:
    turn_id: str
    turn_id_source: str
    status: str
    started_at: Optional[str]
    started_at_source: str
    completed_at: Optional[str]
    completed_at_source: str
    duration_ms: Optional[int]
    reason: Optional[str]
    start_offset: int
    terminal_offset: Optional[int]
    rolled_back: bool = False


def extract_current_work_status(
    records: Iterable[NormalizedRecord],
    messages: Iterable[ExtractedChatMessage],
    session_id: str,
) -> CurrentWorkStatus:
    """Determine working, idle, or unknown from the session turn events."""

    turns: Dict[str, _TurnBuilder] = {}
    ordered_turn_ids: List[str] = []
    active_turn_id: Optional[str] = None
    last_event_at: Optional[str] = None
    issues: List[WorkStatusIssue] = []

    for record in records:
        if record.category != "turn":
            continue
        if record.subtype == "turn_status":
            status = record.attributes.get("status")
            if status not in ("started", "completed", "aborted"):
                continue
            turn_id = record.turn_id or _fallback_turn_id(session_id, record)
            turn_id_source = "jsonl" if record.turn_id is not None else "generated"
            if record.turn_id is None:
                _add_issue(record, issues, "missing_turn_id")

            if status == "started":
                if active_turn_id is not None and active_turn_id != turn_id:
                    previous = turns[active_turn_id]
                    previous.status = "incomplete"
                    previous.completed_at = None
                    previous.completed_at_source = "missing"
                    previous.reason = "superseded_by_new_turn"
                    previous.terminal_offset = record.start_offset
                    _add_issue(record, issues, "unfinished_turn_superseded")
                builder = turns.get(turn_id)
                if builder is None:
                    started_at, started_at_source = _event_time(
                        record, "started_at_ms"
                    )
                    builder = _TurnBuilder(
                        turn_id=turn_id,
                        turn_id_source=turn_id_source,
                        status="in_progress",
                        started_at=started_at,
                        started_at_source=started_at_source,
                        completed_at=None,
                        completed_at_source="missing",
                        duration_ms=None,
                        reason=None,
                        start_offset=record.start_offset,
                        terminal_offset=None,
                    )
                    turns[turn_id] = builder
                    ordered_turn_ids.append(turn_id)
                else:
                    builder.status = "in_progress"
                    if builder.started_at is None:
                        (
                            builder.started_at,
                            builder.started_at_source,
                        ) = _event_time(record, "started_at_ms")
                    builder.completed_at = None
                    builder.completed_at_source = "missing"
                    builder.terminal_offset = None
                active_turn_id = turn_id
            else:
                builder = turns.get(turn_id)
                if builder is None:
                    started_at, started_at_source = _event_time(
                        record, "started_at_ms"
                    )
                    builder = _TurnBuilder(
                        turn_id=turn_id,
                        turn_id_source=turn_id_source,
                        status="in_progress",
                        started_at=started_at,
                        started_at_source=started_at_source,
                        completed_at=None,
                        completed_at_source="missing",
                        duration_ms=None,
                        reason=None,
                        start_offset=record.start_offset,
                        terminal_offset=None,
                    )
                    turns[turn_id] = builder
                    ordered_turn_ids.append(turn_id)
                    _add_issue(record, issues, "terminal_event_without_start")
                builder.status = "completed" if status == "completed" else "failed"
                (
                    builder.completed_at,
                    builder.completed_at_source,
                ) = _event_time(record, "completed_at_ms")
                builder.duration_ms = _attribute_int(record, "duration_ms")
                builder.reason = _attribute_str(record, "reason")
                builder.terminal_offset = record.start_offset
                if active_turn_id == turn_id:
                    active_turn_id = None
            last_event_at = record.timestamp or last_event_at
            continue

        if record.subtype == "thread_rolled_back":
            count = _attribute_int(record, "num_turns") or 0
            candidates = [
                turns[turn_id]
                for turn_id in ordered_turn_ids
                if turns[turn_id].terminal_offset is not None
                and turns[turn_id].terminal_offset < record.start_offset
                and not turns[turn_id].rolled_back
            ]
            for builder in candidates[-count:] if count > 0 else []:
                builder.rolled_back = True
            last_event_at = record.timestamp or last_event_at

    extracted_messages = tuple(messages)
    turn_states = tuple(
        _finish_turn(turns[turn_id], extracted_messages) for turn_id in ordered_turn_ids
    )
    active_state = turns.get(active_turn_id) if active_turn_id is not None else None
    current_message = (
        _current_user_message(active_turn_id, extracted_messages)
        if active_turn_id is not None
        else None
    )
    current_work = _message_preview(current_message) if current_message is not None else None

    effective_turns = [state for state in turn_states if not state.rolled_back]
    latest_turn = effective_turns[-1] if effective_turns else None
    if active_state is not None:
        codex_status = "working"
    elif ordered_turn_ids:
        codex_status = "idle"
    else:
        codex_status = "unknown"

    LOGGER.info(
        "現在の作業状況を抽出: session_id=%s status=%s turns=%d warnings=%d",
        session_id,
        codex_status,
        len(turn_states),
        len(issues),
    )
    return CurrentWorkStatus(
        codex_status=codex_status,
        current_work=current_work,
        current_work_message_id=(
            current_message.message_id if current_message is not None else None
        ),
        active_turn_id=active_turn_id,
        latest_turn_id=latest_turn.turn_id if latest_turn is not None else None,
        latest_turn_status=latest_turn.status if latest_turn is not None else None,
        last_event_at=last_event_at,
        turns=turn_states,
        issues=tuple(issues),
    )


def _finish_turn(
    builder: _TurnBuilder, messages: Tuple[ExtractedChatMessage, ...]
) -> TurnWorkState:
    user_message = _current_user_message(builder.turn_id, messages)
    assistant_ids = tuple(
        message.message_id
        for message in messages
        if message.turn_id == builder.turn_id
        and message.role == "assistant"
        and message.message_type == "chat"
    )
    return TurnWorkState(
        turn_id=builder.turn_id,
        turn_id_source=builder.turn_id_source,
        status=builder.status,
        started_at=builder.started_at,
        started_at_source=builder.started_at_source,
        completed_at=builder.completed_at,
        completed_at_source=builder.completed_at_source,
        duration_ms=builder.duration_ms,
        reason=builder.reason,
        user_message_id=user_message.message_id if user_message is not None else None,
        assistant_message_ids=assistant_ids,
        rolled_back=builder.rolled_back,
    )


def _current_user_message(
    turn_id: str, messages: Tuple[ExtractedChatMessage, ...]
) -> Optional[ExtractedChatMessage]:
    for message in messages:
        if (
            message.turn_id == turn_id
            and message.role == "user"
            and message.message_type == "chat"
        ):
            return message
    return None


def _message_preview(message: ExtractedChatMessage) -> Optional[str]:
    text = "\n".join(part.text for part in message.content if part.text).strip()
    if not text:
        return None
    if len(text) <= CURRENT_WORK_MAX_CHARACTERS:
        return text
    return text[: CURRENT_WORK_MAX_CHARACTERS - 1] + "…"


def _event_time(record: NormalizedRecord, field: str) -> Tuple[Optional[str], str]:
    milliseconds = _attribute_int(record, field)
    if milliseconds is None:
        if record.timestamp is None:
            return None, "missing"
        return record.timestamp, "record_timestamp"
    try:
        value = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()
        return value, "event_field"
    except (OSError, OverflowError, ValueError):
        if record.timestamp is None:
            return None, "missing"
        return record.timestamp, "record_timestamp"


def _fallback_turn_id(session_id: str, record: NormalizedRecord) -> str:
    source = f"{session_id}\0turn\0{record.start_offset}"
    return "turn_" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _attribute_str(record: NormalizedRecord, field: str) -> Optional[str]:
    value = record.attributes.get(field)
    return value if isinstance(value, str) else None


def _attribute_int(record: NormalizedRecord, field: str) -> Optional[int]:
    value = record.attributes.get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _add_issue(
    record: NormalizedRecord, issues: List[WorkStatusIssue], kind: str
) -> None:
    issue = WorkStatusIssue(
        record.source_path, record.line_number, record.start_offset, kind
    )
    issues.append(issue)
    LOGGER.warning(
        "作業状況抽出警告: path=%s line=%d offset=%d kind=%s",
        record.source_path,
        record.line_number,
        record.start_offset,
        kind,
    )
