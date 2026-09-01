"""Extract dashboard chat entries from normalized Codex records."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from tools.record_normalizer import NormalizedContentPart, NormalizedRecord


LOGGER = logging.getLogger("converter")
LONG_CHAT_COLLAPSE_CHARACTERS = 1_000

_AUTOMATIC_PREFIX_PATTERNS = (
    (
        "recommended_plugins",
        re.compile(
            r"\A\s*<recommended_plugins>.*?</recommended_plugins>\s*",
            re.DOTALL,
        ),
    ),
    (
        "agents_instructions",
        re.compile(
            r"\A\s*# AGENTS\.md instructions[^\r\n]*\r?\n\s*"
            r"<INSTRUCTIONS>.*?</INSTRUCTIONS>\s*",
            re.DOTALL,
        ),
    ),
    (
        "environment_context",
        re.compile(
            r"\A\s*<environment_context>.*?</environment_context>\s*",
            re.DOTALL,
        ),
    ),
)


@dataclass(frozen=True)
class ChatRedaction:
    type: str
    detector: str


@dataclass(frozen=True)
class ChatContentPart:
    kind: str
    text: str
    redactions: Tuple[ChatRedaction, ...] = tuple()


@dataclass(frozen=True)
class ChatExtractionIssue:
    path: Path
    line_number: int
    offset: int
    kind: str


@dataclass(frozen=True)
class AutomaticContextRemoval:
    path: Path
    line_number: int
    offset: int
    context_types: Tuple[str, ...]


@dataclass(frozen=True)
class ExtractedChatMessage:
    message_id: str
    source_message_id: Optional[str]
    sequence: int
    created_at: Optional[str]
    role: str
    message_type: str
    phase: Optional[str]
    turn_id: Optional[str]
    content: Tuple[ChatContentPart, ...]
    display_mode: str
    duplicate_of: Optional[str]
    occurrence_count: int
    removed_automatic_contexts: Tuple[str, ...]
    source_path: Path
    source_line_number: int
    source_start_offset: int


@dataclass(frozen=True)
class ChatExtractionResult:
    messages: Tuple[ExtractedChatMessage, ...]
    issues: Tuple[ChatExtractionIssue, ...]
    automatic_context_removals: Tuple[AutomaticContextRemoval, ...]


def extract_chat_messages(
    records: Iterable[NormalizedRecord], session_id: str
) -> ChatExtractionResult:
    """Build an ordered chat timeline without persisting raw intermediate data."""

    source_records = list(records)
    primary_counts = _primary_message_counts(source_records)
    consumed_primary: Dict[Tuple[str, Optional[str], str], int] = {}
    issues: List[ChatExtractionIssue] = []
    context_removals: List[AutomaticContextRemoval] = []
    messages: List[ExtractedChatMessage] = []
    developer_first_by_hash: Dict[str, int] = {}
    developer_occurrences: Dict[str, int] = {}
    tool_by_call_id: Dict[str, int] = {}

    for record in source_records:
        if record.category == "message":
            if record.subtype == "chat_message_event":
                fingerprint = _message_fingerprint(record)
                used = consumed_primary.get(fingerprint, 0)
                if used < primary_counts.get(fingerprint, 0):
                    consumed_primary[fingerprint] = used + 1
                    continue
            elif record.subtype != "chat_message":
                continue

            message = _extract_conversation_message(
                record, session_id, len(messages) + 1, issues
            )
            if message is None:
                continue
            if message.removed_automatic_contexts:
                context_removals.append(
                    AutomaticContextRemoval(
                        message.source_path,
                        message.source_line_number,
                        message.source_start_offset,
                        message.removed_automatic_contexts,
                    )
                )
                if message.role == "user" and not any(
                    part.text for part in message.content
                ):
                    continue
            if message.role == "developer":
                message = _deduplicate_developer_message(
                    message,
                    messages,
                    developer_first_by_hash,
                    developer_occurrences,
                )
            messages.append(message)
            continue

        if record.category == "tool" and record.subtype == "tool_call":
            message = _extract_tool_call(record, session_id, len(messages) + 1)
            messages.append(message)
            call_id = _attribute_str(record, "call_id")
            if call_id is not None:
                tool_by_call_id[call_id] = len(messages) - 1
            continue

        if record.category == "tool" and record.subtype == "tool_output":
            call_id = _attribute_str(record, "call_id")
            if call_id is not None and call_id in tool_by_call_id:
                index = tool_by_call_id[call_id]
                messages[index] = _complete_tool_message(messages[index], record)
            else:
                messages.append(
                    _extract_unmatched_tool_output(
                        record, session_id, len(messages) + 1
                    )
                )
            continue

        if record.category == "tool" and record.subtype == "mcp_tool_completed":
            call_id = _attribute_str(record, "call_id")
            if call_id is not None and call_id in tool_by_call_id:
                index = tool_by_call_id[call_id]
                messages[index] = _complete_tool_message(messages[index], record)

        if record.category == "file_change" and record.subtype == "patch_result":
            call_id = _attribute_str(record, "call_id")
            if call_id is not None and call_id in tool_by_call_id:
                index = tool_by_call_id[call_id]
                messages[index] = _complete_tool_message(messages[index], record)

    for digest, first_index in developer_first_by_hash.items():
        count = developer_occurrences[digest]
        messages[first_index] = replace(messages[first_index], occurrence_count=count)

    messages = [replace(message, sequence=index + 1) for index, message in enumerate(messages)]
    LOGGER.info(
        "チャットメッセージを抽出: session_id=%s count=%d warnings=%d",
        session_id,
        len(messages),
        len(issues),
    )
    return ChatExtractionResult(
        tuple(messages), tuple(issues), tuple(context_removals)
    )


def _extract_conversation_message(
    record: NormalizedRecord,
    session_id: str,
    sequence: int,
    issues: List[ChatExtractionIssue],
) -> Optional[ExtractedChatMessage]:
    if record.role not in ("user", "assistant", "developer"):
        _add_issue(record, issues, "unsupported_message_role")
        return None

    content = _text_content(record.content)
    removed_contexts: Tuple[str, ...] = tuple()
    if record.role == "user":
        content, removed_contexts = _remove_automatic_prefixes(content)

    content_characters = sum(len(part.text) for part in content)
    if record.role == "developer":
        message_type = "developer_instruction"
        display_mode = "collapsed"
    else:
        message_type = "chat"
        display_mode = (
            "collapsed"
            if (
                record.role == "assistant" and record.phase == "commentary"
            ) or content_characters >= LONG_CHAT_COLLAPSE_CHARACTERS
            else "expanded"
        )

    message_id = record.source_id or _fallback_message_id(
        session_id, message_type, record
    )
    return ExtractedChatMessage(
        message_id=message_id,
        source_message_id=record.source_id,
        sequence=sequence,
        created_at=record.timestamp,
        role=record.role,
        message_type=message_type,
        phase=record.phase,
        turn_id=record.turn_id,
        content=content,
        display_mode=display_mode,
        duplicate_of=None,
        occurrence_count=1,
        removed_automatic_contexts=removed_contexts,
        source_path=record.source_path,
        source_line_number=record.line_number,
        source_start_offset=record.start_offset,
    )


def _deduplicate_developer_message(
    message: ExtractedChatMessage,
    messages: List[ExtractedChatMessage],
    first_by_hash: Dict[str, int],
    occurrences: Dict[str, int],
) -> ExtractedChatMessage:
    digest = _content_digest(message.content)
    occurrences[digest] = occurrences.get(digest, 0) + 1
    if digest not in first_by_hash:
        first_by_hash[digest] = len(messages)
        return message

    original = messages[first_by_hash[digest]]
    return replace(
        message,
        message_type="developer_instruction_reference",
        content=tuple(),
        duplicate_of=original.message_id,
    )


def _extract_tool_call(
    record: NormalizedRecord, session_id: str, sequence: int
) -> ExtractedChatMessage:
    name = _attribute_str(record, "name") or "不明なツール"
    return _tool_message(
        record,
        session_id,
        sequence,
        f"ツールを実行: {name}",
        "started",
    )


def _extract_unmatched_tool_output(
    record: NormalizedRecord, session_id: str, sequence: int
) -> ExtractedChatMessage:
    return _tool_message(
        record,
        session_id,
        sequence,
        "ツール結果を受信",
        "result_received",
    )


def _tool_message(
    record: NormalizedRecord,
    session_id: str,
    sequence: int,
    summary: str,
    status: str,
) -> ExtractedChatMessage:
    message_id = record.source_id or _fallback_message_id(session_id, "tool_summary", record)
    call_id = _attribute_str(record, "call_id")
    content = (ChatContentPart("text", summary), ChatContentPart("status", status))
    if call_id is not None:
        content += (ChatContentPart("call_id", call_id),)
    return ExtractedChatMessage(
        message_id=message_id,
        source_message_id=record.source_id,
        sequence=sequence,
        created_at=record.timestamp,
        role="tool",
        message_type="tool_summary",
        phase=None,
        turn_id=record.turn_id,
        content=content,
        display_mode="collapsed",
        duplicate_of=None,
        occurrence_count=1,
        removed_automatic_contexts=tuple(),
        source_path=record.source_path,
        source_line_number=record.line_number,
        source_start_offset=record.start_offset,
    )


def _complete_tool_message(
    message: ExtractedChatMessage, completion: NormalizedRecord
) -> ExtractedChatMessage:
    status = "result_received"
    success = completion.attributes.get("success")
    if success is True:
        status = "succeeded"
    elif success is False:
        status = "failed"
    content = tuple(part for part in message.content if part.kind != "status")
    content += (ChatContentPart("status", status),)
    duration = completion.attributes.get("duration_seconds")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        content += (ChatContentPart("duration_seconds", str(duration)),)
    return replace(message, content=content)


def _primary_message_counts(
    records: Iterable[NormalizedRecord],
) -> Dict[Tuple[str, Optional[str], str], int]:
    counts: Dict[Tuple[str, Optional[str], str], int] = {}
    for record in records:
        if record.category != "message" or record.subtype != "chat_message":
            continue
        fingerprint = _message_fingerprint(record)
        counts[fingerprint] = counts.get(fingerprint, 0) + 1
    return counts


def _message_fingerprint(record: NormalizedRecord) -> Tuple[str, Optional[str], str]:
    role = record.role or "unknown"
    digest = hashlib.sha256()
    for part in record.content:
        digest.update((part.kind + "\0").encode("utf-8"))
        digest.update((part.text or "").encode("utf-8"))
        digest.update(b"\0")
    return role, record.phase, digest.hexdigest()


def _text_content(
    parts: Iterable[NormalizedContentPart],
) -> Tuple[ChatContentPart, ...]:
    return tuple(
        ChatContentPart(
            part.kind,
            part.text,
            tuple(
                ChatRedaction(redaction.type, redaction.detector)
                for redaction in part.redactions
            ),
        )
        for part in parts
        if part.text is not None
    )


def _remove_automatic_prefixes(
    content: Tuple[ChatContentPart, ...],
) -> Tuple[Tuple[ChatContentPart, ...], Tuple[str, ...]]:
    remaining = list(content)
    removed: List[str] = []
    while remaining and remaining[0].kind == "text":
        text = remaining[0].text
        matched_name: Optional[str] = None
        matched_end = 0
        for name, pattern in _AUTOMATIC_PREFIX_PATTERNS:
            match = pattern.match(text)
            if match is not None:
                matched_name = name
                matched_end = match.end()
                break
        if matched_name is None:
            break
        removed.append(matched_name)
        remainder = text[matched_end:]
        if remainder:
            remaining[0] = ChatContentPart(
                "text",
                remainder,
                _redactions_present_in_text(remaining[0].redactions, remainder),
            )
        else:
            remaining.pop(0)

    if not removed:
        return content, tuple()
    return tuple(remaining), tuple(removed)


def _redactions_present_in_text(
    redactions: Tuple[ChatRedaction, ...], text: str
) -> Tuple[ChatRedaction, ...]:
    remaining = list(redactions)
    selected: List[ChatRedaction] = []
    for redaction_type, marker in (
        ("api_key", "[REDACTED:API_KEY]"),
        ("token", "[REDACTED:TOKEN]"),
        ("password", "[REDACTED:PASSWORD]"),
        ("private_key", "[REDACTED:PRIVATE_KEY]"),
        ("url_credential", "[REDACTED:URL_CREDENTIAL]"),
        ("local_path", "[REDACTED:LOCAL_PATH]"),
        ("account", "[REDACTED:ACCOUNT]"),
    ):
        for _ in range(text.count(marker)):
            index = next(
                (
                    position
                    for position, value in enumerate(remaining)
                    if value.type == redaction_type
                ),
                None,
            )
            if index is not None:
                selected.append(remaining.pop(index))
    return tuple(selected)


def _content_digest(content: Iterable[ChatContentPart]) -> str:
    digest = hashlib.sha256()
    for part in content:
        digest.update((part.kind + "\0" + part.text + "\0").encode("utf-8"))
    return digest.hexdigest()


def _fallback_message_id(
    session_id: str, message_type: str, record: NormalizedRecord
) -> str:
    source = (
        f"{session_id}\0{message_type}\0{record.source_record_type}\0"
        f"{record.start_offset}"
    )
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return "msg_" + digest


def _attribute_str(record: NormalizedRecord, field: str) -> Optional[str]:
    value = record.attributes.get(field)
    return value if isinstance(value, str) else None


def _add_issue(
    record: NormalizedRecord,
    issues: List[ChatExtractionIssue],
    kind: str,
) -> None:
    issue = ChatExtractionIssue(
        record.source_path, record.line_number, record.start_offset, kind
    )
    issues.append(issue)
    LOGGER.warning(
        "チャット抽出警告: path=%s line=%d offset=%d kind=%s",
        record.source_path,
        record.line_number,
        record.start_offset,
        kind,
    )
