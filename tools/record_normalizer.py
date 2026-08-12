"""Normalize known Codex rollout record variants in memory."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.session_reader import SessionRecord


LOGGER = logging.getLogger("converter")


@dataclass(frozen=True)
class NormalizedRedaction:
    type: str
    detector: str


@dataclass(frozen=True)
class NormalizedContentPart:
    kind: str
    text: Optional[str]
    source_type: Optional[str]
    redactions: Tuple[NormalizedRedaction, ...] = tuple()


@dataclass(frozen=True)
class NormalizationIssue:
    path: Path
    line_number: int
    offset: int
    kind: str
    field: Optional[str]


@dataclass(frozen=True)
class NormalizedRecord:
    timestamp: Optional[str]
    category: str
    subtype: str
    source_record_type: str
    source_payload_type: Optional[str]
    source_id: Optional[str]
    turn_id: Optional[str]
    role: Optional[str]
    phase: Optional[str]
    content: Tuple[NormalizedContentPart, ...]
    attributes: Dict[str, Any]
    source_path: Path
    line_number: int
    start_offset: int
    end_offset: int


@dataclass(frozen=True)
class NormalizationResult:
    record: Optional[NormalizedRecord]
    issues: Tuple[NormalizationIssue, ...]


def normalize_record(source: SessionRecord) -> NormalizationResult:
    """Convert one known rollout record to a stable in-memory representation."""

    issues: List[NormalizationIssue] = []
    timestamp = _normalize_timestamp(source.timestamp)
    if timestamp is None:
        _add_issue(source, issues, "invalid_timestamp", "timestamp")

    if source.record_type == "session_meta":
        normalized = _normalize_session_meta(source, timestamp)
    elif source.record_type == "turn_context":
        normalized = _normalize_turn_context(source, timestamp)
    elif source.record_type == "response_item":
        normalized = _normalize_response_item(source, timestamp, issues)
    elif source.record_type == "event_msg":
        normalized = _normalize_event_message(source, timestamp, issues)
    elif source.record_type == "world_state":
        normalized = _base_record(
            source,
            timestamp,
            category="internal_context",
            subtype="world_state",
            attributes={"full": _optional_bool(source.payload.get("full"))},
        )
    elif source.record_type == "compacted":
        normalized = _base_record(
            source,
            timestamp,
            category="internal_context",
            subtype="compacted",
            attributes={
                "window_id": _optional_str(source.payload.get("window_id")),
                "window_number": _optional_int(source.payload.get("window_number")),
            },
        )
    else:
        normalized = None

    if normalized is None and not issues:
        _add_issue(source, issues, "unsupported_record", source.record_type)
    return NormalizationResult(normalized, tuple(issues))


def _normalize_session_meta(
    source: SessionRecord, timestamp: Optional[str]
) -> NormalizedRecord:
    payload = source.payload
    session_id = _optional_str(payload.get("session_id"))
    if session_id is None:
        session_id = _optional_str(payload.get("id"))
    git = payload.get("git")
    git_attributes: Optional[Dict[str, Optional[str]]] = None
    if isinstance(git, dict):
        git_attributes = {
            "branch": _optional_str(git.get("branch")),
            "commit_hash": _optional_str(git.get("commit_hash")),
        }
    return _base_record(
        source,
        timestamp,
        category="session",
        subtype="session_metadata",
        source_id=session_id,
        attributes={
            "session_id": session_id,
            "cwd": _optional_str(payload.get("cwd")),
            "source": _optional_str(payload.get("source")),
            "originator": _optional_str(payload.get("originator")),
            "cli_version": _optional_str(payload.get("cli_version")),
            "git": git_attributes,
        },
    )


def _normalize_turn_context(
    source: SessionRecord, timestamp: Optional[str]
) -> NormalizedRecord:
    payload = source.payload
    roots = payload.get("workspace_roots")
    workspace_roots = (
        tuple(item for item in roots if isinstance(item, str))
        if isinstance(roots, list)
        else tuple()
    )
    return _base_record(
        source,
        timestamp,
        category="turn",
        subtype="turn_context",
        turn_id=_optional_str(payload.get("turn_id")),
        attributes={
            "cwd": _optional_str(payload.get("cwd")),
            "workspace_roots": workspace_roots,
            "model": _optional_str(payload.get("model")),
            "effort": _optional_str(payload.get("effort")),
            "timezone": _optional_str(payload.get("timezone")),
        },
    )


def _normalize_response_item(
    source: SessionRecord,
    timestamp: Optional[str],
    issues: List[NormalizationIssue],
) -> Optional[NormalizedRecord]:
    payload = source.payload
    subtype = _optional_str(payload.get("type"))
    turn_id = _turn_id(payload)
    source_id = _optional_str(payload.get("id"))

    if subtype == "message":
        content = _normalize_content(source, payload.get("content"), issues)
        return _base_record(
            source,
            timestamp,
            category="message",
            subtype="chat_message",
            source_payload_type=subtype,
            source_id=source_id,
            turn_id=turn_id,
            role=_optional_str(payload.get("role")),
            phase=_optional_str(payload.get("phase")),
            content=content,
        )

    if subtype == "reasoning":
        return _base_record(
            source,
            timestamp,
            category="internal_context",
            subtype="reasoning",
            source_payload_type=subtype,
            source_id=source_id,
            turn_id=turn_id,
        )

    if subtype in ("function_call", "custom_tool_call"):
        input_field = "arguments" if subtype == "function_call" else "input"
        content = _normalize_text_value(
            source, payload.get(input_field), "tool_input", input_field, issues
        )
        return _base_record(
            source,
            timestamp,
            category="tool",
            subtype="tool_call",
            source_payload_type=subtype,
            source_id=source_id,
            turn_id=turn_id,
            role="tool",
            content=content,
            attributes={
                "call_id": _optional_str(payload.get("call_id")),
                "name": _optional_str(payload.get("name")),
                "namespace": _optional_str(payload.get("namespace")),
                "status": _optional_str(payload.get("status")),
            },
        )

    if subtype in ("function_call_output", "custom_tool_call_output"):
        content = _normalize_content(source, payload.get("output"), issues, "tool_output")
        return _base_record(
            source,
            timestamp,
            category="tool",
            subtype="tool_output",
            source_payload_type=subtype,
            source_id=source_id,
            turn_id=turn_id,
            role="tool",
            content=content,
            attributes={"call_id": _optional_str(payload.get("call_id"))},
        )

    _add_issue(source, issues, "unsupported_response_item", subtype)
    return None


def _normalize_event_message(
    source: SessionRecord,
    timestamp: Optional[str],
    issues: List[NormalizationIssue],
) -> Optional[NormalizedRecord]:
    payload = source.payload
    subtype = _optional_str(payload.get("type"))
    turn_id = _optional_str(payload.get("turn_id"))

    if subtype in ("user_message", "agent_message"):
        content = _normalize_text_value(
            source, payload.get("message"), "text", "message", issues
        )
        return _base_record(
            source,
            timestamp,
            category="message",
            subtype="chat_message_event",
            source_payload_type=subtype,
            turn_id=turn_id,
            role="user" if subtype == "user_message" else "assistant",
            phase=_optional_str(payload.get("phase")),
            content=content,
            attributes={"fallback_source": True},
        )

    if subtype in ("task_started", "task_complete", "turn_aborted"):
        status = {
            "task_started": "started",
            "task_complete": "completed",
            "turn_aborted": "aborted",
        }[subtype]
        return _base_record(
            source,
            timestamp,
            category="turn",
            subtype="turn_status",
            source_payload_type=subtype,
            turn_id=turn_id,
            attributes={
                "status": status,
                "started_at_ms": _optional_int(payload.get("started_at")),
                "completed_at_ms": _optional_int(payload.get("completed_at")),
                "duration_ms": _optional_int(payload.get("duration_ms")),
                "time_to_first_token_ms": _optional_int(
                    payload.get("time_to_first_token_ms")
                ),
                "reason": _optional_str(payload.get("reason")),
            },
        )

    if subtype == "thread_rolled_back":
        return _base_record(
            source,
            timestamp,
            category="turn",
            subtype="thread_rolled_back",
            source_payload_type=subtype,
            attributes={"num_turns": _optional_int(payload.get("num_turns"))},
        )

    if subtype == "thread_settings_applied":
        settings = payload.get("thread_settings")
        safe_settings: Dict[str, Optional[str]] = {}
        if isinstance(settings, dict):
            for field in ("cwd", "model", "reasoning_effort", "personality"):
                safe_settings[field] = _optional_str(settings.get(field))
        return _base_record(
            source,
            timestamp,
            category="settings",
            subtype="thread_settings",
            source_payload_type=subtype,
            attributes=safe_settings,
        )

    if subtype == "token_count":
        return _base_record(
            source,
            timestamp,
            category="usage",
            subtype="token_count",
            source_payload_type=subtype,
            attributes=_normalize_token_usage(payload.get("info")),
        )

    if subtype == "patch_apply_end":
        content: List[NormalizedContentPart] = []
        content.extend(
            _normalize_text_value(
                source, payload.get("stdout"), "stdout", "stdout", issues
            )
        )
        content.extend(
            _normalize_text_value(
                source, payload.get("stderr"), "stderr", "stderr", issues
            )
        )
        return _base_record(
            source,
            timestamp,
            category="file_change",
            subtype="patch_result",
            source_payload_type=subtype,
            turn_id=turn_id,
            content=tuple(content),
            attributes={
                "call_id": _optional_str(payload.get("call_id")),
                "status": _optional_str(payload.get("status")),
                "success": _optional_bool(payload.get("success")),
                "changes": _normalize_patch_changes(payload.get("changes")),
            },
        )

    if subtype == "web_search_end":
        action = payload.get("action")
        action_type = _optional_str(action.get("type")) if isinstance(action, dict) else None
        results = payload.get("results")
        return _base_record(
            source,
            timestamp,
            category="tool",
            subtype="web_search_completed",
            source_payload_type=subtype,
            attributes={
                "call_id": _optional_str(payload.get("call_id")),
                "action_type": action_type,
                "result_count": len(results) if isinstance(results, list) else None,
            },
        )

    if subtype == "mcp_tool_call_end":
        invocation = payload.get("invocation")
        result = payload.get("result")
        duration = payload.get("duration")
        success: Optional[bool] = None
        if isinstance(result, dict):
            if "Ok" in result:
                success = True
            elif "Err" in result:
                success = False
        return _base_record(
            source,
            timestamp,
            category="tool",
            subtype="mcp_tool_completed",
            source_payload_type=subtype,
            attributes={
                "call_id": _optional_str(payload.get("call_id")),
                "server": _dict_str(invocation, "server"),
                "tool": _dict_str(invocation, "tool"),
                "duration_seconds": _duration_seconds(duration),
                "success": success,
            },
        )

    if subtype == "context_compacted":
        return _base_record(
            source,
            timestamp,
            category="internal_context",
            subtype="context_compacted",
            source_payload_type=subtype,
        )

    _add_issue(source, issues, "unsupported_event_message", subtype)
    return None


def _base_record(
    source: SessionRecord,
    timestamp: Optional[str],
    category: str,
    subtype: str,
    source_payload_type: Optional[str] = None,
    source_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    role: Optional[str] = None,
    phase: Optional[str] = None,
    content: Tuple[NormalizedContentPart, ...] = tuple(),
    attributes: Optional[Dict[str, Any]] = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        timestamp=timestamp,
        category=category,
        subtype=subtype,
        source_record_type=source.record_type,
        source_payload_type=source_payload_type,
        source_id=source_id,
        turn_id=turn_id,
        role=role,
        phase=phase,
        content=content,
        attributes=attributes or {},
        source_path=source.path,
        line_number=source.line_number,
        start_offset=source.start_offset,
        end_offset=source.end_offset,
    )


def _normalize_content(
    source: SessionRecord,
    value: Any,
    issues: List[NormalizationIssue],
    default_kind: str = "text",
) -> Tuple[NormalizedContentPart, ...]:
    if isinstance(value, str):
        return (NormalizedContentPart(default_kind, value, None),)
    if not isinstance(value, list):
        _add_issue(source, issues, "invalid_content", "content")
        return tuple()

    parts: List[NormalizedContentPart] = []
    for item in value:
        if isinstance(item, str):
            parts.append(NormalizedContentPart(default_kind, item, None))
            continue
        if not isinstance(item, dict):
            _add_issue(source, issues, "invalid_content_item", "content")
            continue
        source_type = _optional_str(item.get("type"))
        text = item.get("text")
        if isinstance(text, str):
            parts.append(NormalizedContentPart(default_kind, text, source_type))
        elif source_type in ("input_image", "output_image"):
            parts.append(NormalizedContentPart("image_reference", None, source_type))
        else:
            _add_issue(source, issues, "unsupported_content_item", source_type)
    return tuple(parts)


def _normalize_text_value(
    source: SessionRecord,
    value: Any,
    kind: str,
    field: str,
    issues: List[NormalizationIssue],
) -> Tuple[NormalizedContentPart, ...]:
    if value is None:
        return tuple()
    if isinstance(value, str):
        return (NormalizedContentPart(kind, value, None),)
    _add_issue(source, issues, "invalid_text", field)
    return tuple()


def _normalize_patch_changes(value: Any) -> Tuple[Dict[str, Optional[str]], ...]:
    if not isinstance(value, dict):
        return tuple()
    changes: List[Dict[str, Optional[str]]] = []
    for path, details in value.items():
        if not isinstance(path, str) or not isinstance(details, dict):
            continue
        changes.append(
            {
                "path": path,
                "type": _optional_str(details.get("type")),
                "move_path": _optional_str(details.get("move_path")),
            }
        )
    return tuple(changes)


def _normalize_token_usage(value: Any) -> Dict[str, Optional[int]]:
    if not isinstance(value, dict):
        return {}
    usage = value.get("total_token_usage")
    if not isinstance(usage, dict):
        usage = value.get("last_token_usage")
    if not isinstance(usage, dict):
        return {}
    fields = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    )
    return {field: _optional_int(usage.get(field)) for field in fields}


def _turn_id(payload: Dict[str, Any]) -> Optional[str]:
    turn_id = _optional_str(payload.get("turn_id"))
    if turn_id is not None:
        return turn_id
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    return _dict_str(metadata, "turn_id")


def _normalize_timestamp(value: str) -> Optional[str]:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _duration_seconds(value: Any) -> Optional[float]:
    if not isinstance(value, dict):
        return None
    seconds = _optional_int(value.get("secs"))
    nanos = _optional_int(value.get("nanos"))
    if seconds is None:
        return None
    return float(seconds) + (float(nanos or 0) / 1_000_000_000)


def _dict_str(value: Any, key: str) -> Optional[str]:
    return _optional_str(value.get(key)) if isinstance(value, dict) else None


def _optional_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _optional_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _add_issue(
    source: SessionRecord,
    issues: List[NormalizationIssue],
    kind: str,
    field: Optional[str],
) -> None:
    issue = NormalizationIssue(
        source.path, source.line_number, source.start_offset, kind, field
    )
    issues.append(issue)
    LOGGER.warning(
        "Codexレコード正規化警告: path=%s line=%d offset=%d kind=%s field=%s",
        source.path,
        source.line_number,
        source.start_offset,
        kind,
        field,
    )
