"""Extract explicit development failures from normalized Codex records."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tools.chat_extractor import ExtractedChatMessage
from tools.record_normalizer import NormalizedRecord
from tools.work_status_extractor import CurrentWorkStatus


LOGGER = logging.getLogger("converter")
ERROR_SUMMARY_MAX_CHARACTERS = 500
ERROR_PREVIEW_MAX_CHARACTERS = 4_000

_EXIT_CODE_PATTERNS = (
    re.compile(r"(?im)^\s*Exit code:\s*(-?\d+)\s*$"),
    re.compile(r"(?im)^\s*Process exited with code\s+(-?\d+)\s*$"),
)
_UNITTEST_FAILURE = re.compile(
    r"(?m)^(?:FAIL|ERROR):\s+(?P<test>[^\s(]+)(?:\s+\((?P<case>[^)]+)\))?"
)
_TRACEBACK_FILE = re.compile(
    r'(?m)^\s*File\s+["\'](?P<path>[^"\']+)["\'],\s+line\s+'
    r"(?P<line>\d+)(?:,\s+in\s+(?P<function>[^\r\n]+))?"
)
_TRACEBACK_EXCEPTION = re.compile(
    r"(?m)^(?P<name>[A-Za-z_][\w.]*(?:Error|Exception|Interrupt))"
    r"(?::\s*(?P<message>.*))?$"
)
_GIT_FATAL = re.compile(r"(?im)^fatal:\s*(?P<message>.+)$")


@dataclass(frozen=True)
class ExtractedDevelopmentError:
    error_id: str
    fingerprint: str
    kind: str
    first_occurred_at: Optional[str]
    last_occurred_at: Optional[str]
    occurred_at_source: str
    occurrence_count: int
    severity: str
    category: str
    summary: str
    details_preview: Optional[str]
    details_text: Optional[str]
    details_complete: bool
    source_message_ids: Tuple[str, ...]
    source_path: Path
    source_line_number: int
    source_start_offset: int
    status: str
    resolved_at: Optional[str]
    resolution: Optional[str]
    rolled_back: bool
    operation_key: Optional[str]


@dataclass(frozen=True)
class ErrorExtractionIssue:
    path: Path
    line_number: int
    offset: int
    kind: str


@dataclass(frozen=True)
class ErrorExtractionResult:
    errors: Tuple[ExtractedDevelopmentError, ...]
    issues: Tuple[ErrorExtractionIssue, ...]


@dataclass(frozen=True)
class _ToolCall:
    name: str
    operation_key: Optional[str]


@dataclass(frozen=True)
class _Candidate:
    kind: str
    severity: str
    summary: str
    details: Optional[str]
    operation_key: Optional[str]
    details_complete: bool = True
    location_path: Optional[str] = None
    location_line: Optional[int] = None


def extract_development_errors(
    records: Iterable[NormalizedRecord],
    messages: Iterable[ExtractedChatMessage],
    work_status: CurrentWorkStatus,
    workspace_id: str,
    session_id: str,
    *,
    safe_operation_identities: Optional[Mapping[str, str]] = None,
) -> ErrorExtractionResult:
    """Extract and aggregate failures without persisting unmasked details."""

    source_records = tuple(records)
    message_ids = _message_ids_by_call_id(messages)
    rolled_back_turns = {
        turn.turn_id for turn in work_status.turns if turn.rolled_back
    }
    identities = safe_operation_identities or {}
    tool_calls: Dict[str, _ToolCall] = {}
    aggregated: Dict[str, ExtractedDevelopmentError] = {}
    operation_errors: Dict[str, List[str]] = {}
    issues: List[ErrorExtractionIssue] = []

    for record in source_records:
        call_id = _attribute_str(record, "call_id")
        if record.category == "tool" and record.subtype == "tool_call":
            if call_id is not None:
                name = _attribute_str(record, "name") or "unknown_tool"
                safe_identity = identities.get(call_id)
                operation_key = (
                    _operation_key("tool", name, safe_identity)
                    if safe_identity is not None
                    else None
                )
                tool_calls[call_id] = _ToolCall(name, operation_key)
            continue

        candidates: Tuple[_Candidate, ...] = tuple()
        success_operation: Optional[str] = None
        if record.category == "tool" and record.subtype == "tool_output":
            text = _content_text(record)
            call = tool_calls.get(call_id or "")
            operation_key = call.operation_key if call is not None else None
            exit_code = _extract_exit_code(text)
            candidates = _tool_output_candidates(text, exit_code, operation_key)
            if exit_code == 0 and not candidates:
                success_operation = operation_key
        elif record.category == "file_change" and record.subtype == "patch_result":
            operation_key = _patch_operation_key(record)
            success = record.attributes.get("success")
            if success is False:
                details, details_complete = _error_content(record)
                candidates = (
                    _Candidate(
                        "patch_failure",
                        "error",
                        "パッチの適用に失敗しました。",
                        details or None,
                        operation_key,
                        details_complete=details_complete,
                    ),
                )
            elif success is True:
                success_operation = operation_key
        elif record.category == "tool" and record.subtype == "mcp_tool_completed":
            operation_key = _mcp_operation_key(record, identities.get(call_id or ""))
            success = record.attributes.get("success")
            if success is False:
                server = _attribute_str(record, "server") or "不明なサーバー"
                tool = _attribute_str(record, "tool") or "不明なツール"
                candidates = (
                    _Candidate(
                        "mcp_failure",
                        "error",
                        f"MCPツールの実行に失敗しました: {server}/{tool}",
                        None,
                        operation_key,
                    ),
                )
            elif success is True:
                success_operation = operation_key
        elif record.category == "turn" and record.subtype == "turn_status":
            if record.attributes.get("status") == "aborted":
                reason = _attribute_str(record, "reason") or "unknown"
                candidates = (
                    _Candidate(
                        "turn_interrupted",
                        "warning" if reason == "interrupted" else "error",
                        f"Codexのターンが中断されました: {reason}",
                        None,
                        None,
                    ),
                )

        if success_operation is not None:
            _resolve_operation(
                success_operation,
                record,
                aggregated,
                operation_errors,
            )

        for candidate in candidates:
            occurred_at, occurred_source = _event_time(record)
            source_ids = message_ids.get(call_id or "", tuple())
            fingerprint = _fingerprint(
                workspace_id,
                session_id,
                record,
                candidate,
            )
            existing = aggregated.get(fingerprint)
            rolled_back = record.turn_id in rolled_back_turns
            if existing is None:
                error = ExtractedDevelopmentError(
                    error_id="error_" + fingerprint,
                    fingerprint=fingerprint,
                    kind=candidate.kind,
                    first_occurred_at=occurred_at,
                    last_occurred_at=occurred_at,
                    occurred_at_source=occurred_source,
                    occurrence_count=1,
                    severity=candidate.severity,
                    category="command",
                    summary=_limit(candidate.summary, ERROR_SUMMARY_MAX_CHARACTERS),
                    details_preview=(
                        _limit(candidate.details, ERROR_PREVIEW_MAX_CHARACTERS)
                        if candidate.details
                        else None
                    ),
                    details_text=(candidate.details if candidate.details_complete else None),
                    details_complete=candidate.details_complete,
                    source_message_ids=source_ids,
                    source_path=record.source_path,
                    source_line_number=record.line_number,
                    source_start_offset=record.start_offset,
                    status="open",
                    resolved_at=None,
                    resolution=None,
                    rolled_back=rolled_back,
                    operation_key=candidate.operation_key,
                )
                aggregated[fingerprint] = error
                if candidate.operation_key is not None:
                    operation_errors.setdefault(candidate.operation_key, []).append(
                        fingerprint
                    )
            else:
                aggregated[fingerprint] = replace(
                    existing,
                    last_occurred_at=occurred_at or existing.last_occurred_at,
                    occurred_at_source=(
                        occurred_source
                        if occurred_at is not None
                        else existing.occurred_at_source
                    ),
                    occurrence_count=existing.occurrence_count + 1,
                    source_message_ids=_merge_ids(existing.source_message_ids, source_ids),
                    status="open",
                    resolved_at=None,
                    resolution=None,
                    rolled_back=existing.rolled_back and rolled_back,
                )

    result = tuple(
        sorted(
            aggregated.values(),
            key=lambda item: (
                item.status != "open" or item.rolled_back,
                -_sortable_time(item.last_occurred_at),
                -item.source_start_offset,
            ),
        )
    )
    LOGGER.info(
        "開発エラーを抽出: session_id=%s errors=%d open=%d warnings=%d",
        session_id,
        len(result),
        sum(1 for item in result if item.status == "open" and not item.rolled_back),
        len(issues),
    )
    return ErrorExtractionResult(result, tuple(issues))


def _tool_output_candidates(
    text: str, exit_code: Optional[int], operation_key: Optional[str]
) -> Tuple[_Candidate, ...]:
    test_matches = tuple(_UNITTEST_FAILURE.finditer(text))
    if test_matches:
        severity = "error" if exit_code not in (None, 0) else "warning"
        return tuple(
            _Candidate(
                "test_failure",
                severity,
                "テストが失敗しました: " + _test_name(match),
                _test_failure_detail(text, match),
                operation_key,
                location_path=_test_path(match),
            )
            for match in test_matches
        )

    exception = _last_match(_TRACEBACK_EXCEPTION, text)
    if "Traceback (most recent call last):" in text and exception is not None:
        location = _last_match(_TRACEBACK_FILE, text)
        name = exception.group("name")
        message = (exception.group("message") or "").strip()
        summary = f"Python例外が発生しました: {name}"
        if message:
            summary += f": {message}"
        severity = "error" if exit_code not in (None, 0) else "warning"
        return (
            _Candidate(
                "python_exception",
                severity,
                summary,
                text,
                operation_key,
                location_path=location.group("path") if location else None,
                location_line=int(location.group("line")) if location else None,
            ),
        )

    if exit_code is None or exit_code == 0:
        return tuple()
    fatal = _GIT_FATAL.search(text)
    if fatal is not None:
        return (
            _Candidate(
                "git_command_failure",
                "error",
                "Gitコマンドが失敗しました: " + fatal.group("message").strip(),
                fatal.group(0),
                operation_key,
            ),
        )
    return (
        _Candidate(
            "command_failure",
            "error",
            f"コマンドが終了コード{exit_code}で失敗しました。",
            _limit(_failure_detail(text) or "", ERROR_PREVIEW_MAX_CHARACTERS) or None,
            operation_key,
            details_complete=False,
        ),
    )


def _resolve_operation(
    operation_key: str,
    record: NormalizedRecord,
    errors: Dict[str, ExtractedDevelopmentError],
    operation_errors: Dict[str, List[str]],
) -> None:
    resolved_at, _ = _event_time(record)
    for fingerprint in operation_errors.get(operation_key, []):
        error = errors.get(fingerprint)
        if error is None or error.status != "open":
            continue
        errors[fingerprint] = replace(
            error,
            status="resolved",
            resolved_at=resolved_at,
            resolution="同じ処理が後続の実行で正常終了しました。",
        )


def _fingerprint(
    workspace_id: str,
    session_id: str,
    record: NormalizedRecord,
    candidate: _Candidate,
) -> str:
    operation = candidate.operation_key
    if operation is None:
        call_id = _attribute_str(record, "call_id")
        operation = "call:" + call_id if call_id else f"offset:{record.start_offset}"
    values = (
        workspace_id,
        session_id,
        record.source_payload_type or record.subtype,
        candidate.kind,
        operation,
        candidate.location_path or "",
        str(candidate.location_line or ""),
        _normalize_summary(candidate.summary),
    )
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


def _message_ids_by_call_id(
    messages: Iterable[ExtractedChatMessage],
) -> Dict[str, Tuple[str, ...]]:
    values: Dict[str, List[str]] = {}
    for message in messages:
        call_ids = [part.text for part in message.content if part.kind == "call_id"]
        for call_id in call_ids:
            values.setdefault(call_id, []).append(message.message_id)
    return {key: tuple(items) for key, items in values.items()}


def _patch_operation_key(record: NormalizedRecord) -> Optional[str]:
    changes = record.attributes.get("changes")
    if not isinstance(changes, tuple):
        return None
    paths = sorted(
        item.get("path")
        for item in changes
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    )
    return _operation_key("patch", *paths) if paths else None


def _mcp_operation_key(
    record: NormalizedRecord, safe_identity: Optional[str]
) -> Optional[str]:
    if safe_identity is None:
        return None
    return _operation_key(
        "mcp",
        _attribute_str(record, "server") or "",
        _attribute_str(record, "tool") or "",
        safe_identity,
    )


def _operation_key(kind: str, *values: str) -> str:
    return hashlib.sha256("\0".join((kind, *values)).encode("utf-8")).hexdigest()


def _event_time(record: NormalizedRecord) -> Tuple[Optional[str], str]:
    if record.timestamp is not None:
        return record.timestamp, "record_timestamp"
    try:
        modified = record.source_path.stat().st_mtime
        return datetime.fromtimestamp(modified, tz=timezone.utc).isoformat(), "file_mtime"
    except (OSError, OverflowError, ValueError):
        return None, "missing"


def _extract_exit_code(text: str) -> Optional[int]:
    for pattern in _EXIT_CODE_PATTERNS:
        matches = tuple(pattern.finditer(text))
        if matches:
            return int(matches[-1].group(1))
    return None


def _content_text(record: NormalizedRecord) -> str:
    return "\n".join(part.text for part in record.content if part.text)


def _error_content(record: NormalizedRecord) -> Tuple[Optional[str], bool]:
    stderr = [part.text for part in record.content if part.kind == "stderr" and part.text]
    if stderr:
        return "\n".join(stderr), True
    preview = _failure_detail(_content_text(record))
    if preview is None:
        return None, False
    return _limit(preview, ERROR_PREVIEW_MAX_CHARACTERS), False


def _failure_detail(text: str) -> Optional[str]:
    stripped = text.strip()
    if not stripped:
        return None
    traceback_at = stripped.find("Traceback (most recent call last):")
    if traceback_at >= 0:
        return stripped[traceback_at:]
    fatal = _GIT_FATAL.search(stripped)
    if fatal is not None:
        return fatal.group(0)
    lines = stripped.splitlines()
    return "\n".join(lines[-40:])


def _test_failure_detail(text: str, match: re.Match[str]) -> str:
    start = match.start()
    next_failure = _UNITTEST_FAILURE.search(text, match.end())
    separator = text.find("\n----------------------------------------------------------------------", start)
    ends = [
        value
        for value in (
            separator,
            next_failure.start() if next_failure is not None else -1,
        )
        if value >= 0
    ]
    end = min(ends) if ends else len(text)
    return text[start:end].strip()


def _test_name(match: re.Match[str]) -> str:
    case = match.group("case")
    return f"{case}.{match.group('test')}" if case else match.group("test")


def _test_path(match: re.Match[str]) -> Optional[str]:
    case = match.group("case")
    return case if case and ("/" in case or "\\" in case) else None


def _last_match(pattern: re.Pattern[str], text: str) -> Optional[re.Match[str]]:
    matches = tuple(pattern.finditer(text))
    return matches[-1] if matches else None


def _attribute_str(record: NormalizedRecord, name: str) -> Optional[str]:
    value = record.attributes.get(name)
    return value if isinstance(value, str) else None


def _limit(text: str, maximum: int) -> str:
    if len(text) <= maximum:
        return text
    return text[: maximum - 1] + "…"


def _normalize_summary(value: str) -> str:
    return " ".join(value.split()).casefold()


def _merge_ids(first: Sequence[str], second: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *second)))


def _sortable_time(value: Optional[str]) -> float:
    if value is None:
        return float("-inf")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return float("-inf")
