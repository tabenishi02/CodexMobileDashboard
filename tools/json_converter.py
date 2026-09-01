"""Convert safe collector results into dashboard JSON documents in memory."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tools.change_summary_generator import (
    ChangeSummary,
    ChangeSummaryGenerationResult,
    SummaryEvidenceItem,
)
from tools.chat_extractor import ChatExtractionResult, ExtractedChatMessage
from tools.decision_extractor import DecisionExtractionResult, ExtractedDecision
from tools.error_extractor import ErrorExtractionResult, ExtractedDevelopmentError
from tools.file_reference_extractor import (
    ExtractedFileReference,
    FileReferenceExtractionResult,
)
from tools.git_change_collector import (
    GitCollectionResult,
    GitFileChange,
    GitNumstat,
)
from tools.next_task_extractor import NextTask, NextTaskExtractionResult
from tools.work_status_extractor import CurrentWorkStatus, TurnWorkState


LOGGER = logging.getLogger("converter")
SCHEMA_VERSION = "1.0"
PAGE_MAX_ITEMS = 100
PAGE_MAX_BYTES = 512 * 1024
MESSAGE_CHUNK_MAX_BYTES = 256 * 1024
PREVIEW_MAX_CHARACTERS = 500

_FENCE = re.compile(
    r"(?ms)^([ \t]*)```([^\r\n`]*)\r?\n(.*?)(?:^\1```[ \t]*(?:\r?\n|\Z))"
)


class UnsafeJsonInputError(ValueError):
    """Raised when display JSON could receive content not certified as masked."""


@dataclass(frozen=True)
class JsonContext:
    snapshot_id: str
    generated_at: str
    workspace_id: str
    session_id: str
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class ProjectPresentation:
    name: str
    phase: Optional[str]


@dataclass(frozen=True)
class CollectorMetadata:
    status: str
    last_checked_at: str
    last_data_change_at: Optional[str]
    last_acknowledged_snapshot_id: Optional[str]
    last_send_succeeded_at: Optional[str]


@dataclass(frozen=True)
class JsonSnapshot:
    """A complete, not-yet-persisted snapshot keyed by relative POSIX path."""

    documents: Mapping[str, Mapping[str, object]]

    def document(self, path: str) -> Mapping[str, object]:
        return self.documents[path]

    def encoded(self, path: str) -> bytes:
        return encode_json(self.documents[path])


def build_json_snapshot(
    context: JsonContext,
    project: ProjectPresentation,
    chats: ChatExtractionResult,
    work_status: CurrentWorkStatus,
    next_task: NextTaskExtractionResult,
    errors: ErrorExtractionResult,
    decisions: DecisionExtractionResult,
    file_references: FileReferenceExtractionResult,
    git: GitCollectionResult,
    summaries: ChangeSummaryGenerationResult,
    collector: CollectorMetadata,
    *,
    content_is_masked: bool,
) -> JsonSnapshot:
    """Build all logical JSON files without writing them to disk."""

    if not content_is_masked:
        LOGGER.error("JSON変換を中止: 未マスク入力")
        raise UnsafeJsonInputError("content_not_masked")
    _validate_context(context, project, collector)

    message_warnings = _warnings("messages", chats.issues, context)
    summary_warnings = _warnings("change_summaries", summaries.issues, context)
    error_warnings = _warnings("errors", errors.issues, context)
    decision_warnings = _warnings("decisions", decisions.issues, context)
    file_warnings = _warnings("files", git.issues, context)
    next_warnings = _warnings("next_task", next_task.issues, context)
    work_warnings = _warnings("recent", work_status.issues, context)

    documents: Dict[str, Mapping[str, object]] = {}
    message_pages, message_chunks = _build_message_documents(
        context,
        chats.messages,
        file_references.references,
        message_warnings,
    )
    documents.update(message_chunks)
    documents.update(message_pages)
    summary_pages = _build_summary_pages(
        context, summaries.summaries, summary_warnings
    )
    documents.update(summary_pages)

    documents["messages.json"] = _messages_index(
        context,
        chats.messages,
        message_pages,
        summaries.summaries,
        summary_pages,
        message_warnings + summary_warnings,
    )
    documents["errors.json"] = _errors_document(
        context, errors.errors, error_warnings
    )
    documents["decisions.json"] = _decisions_document(
        context, decisions.decisions, decision_warnings
    )
    documents["files.json"] = _files_document(context, git, file_warnings)
    documents["recent.json"] = _recent_document(
        context, work_status, chats.messages, work_warnings
    )
    documents["dashboard.json"] = _dashboard_document(
        context,
        project,
        chats.messages,
        work_status,
        next_task.task,
        errors.errors,
        git,
        summaries.summaries,
        next_warnings + work_warnings,
    )
    documents["metadata.json"] = _metadata_document(
        context, collector, work_status, documents
    )
    LOGGER.info(
        "表示用JSONへ変換: workspace_id=%s session_id=%s files=%d messages=%d summaries=%d",
        context.workspace_id,
        context.session_id,
        len(documents),
        len(chats.messages),
        len(summaries.summaries),
    )
    return JsonSnapshot(dict(documents))


def encode_json(document: Mapping[str, object]) -> bytes:
    """Return the deterministic UTF-8 representation used for sizes and hashes."""

    text = json.dumps(
        document,
        ensure_ascii=False,
        indent=2,
        separators=(",", ": "),
    )
    return (text + "\n").encode("utf-8")


def _common(
    context: JsonContext,
    data_type: str,
    warnings: Sequence[Mapping[str, object]],
) -> Dict[str, object]:
    return {
        "schema_version": context.schema_version,
        "data_type": data_type,
        "snapshot_id": context.snapshot_id,
        "generated_at": context.generated_at,
        "workspace_id": context.workspace_id,
        "session_id": context.session_id,
        "warnings": list(warnings),
    }


def _build_message_documents(
    context: JsonContext,
    messages: Sequence[ExtractedChatMessage],
    references: Sequence[ExtractedFileReference],
    warnings: Sequence[Mapping[str, object]],
) -> Tuple[
    Dict[str, Mapping[str, object]],
    Dict[str, Mapping[str, object]],
]:
    references_by_message: Dict[str, List[ExtractedFileReference]] = {}
    for reference in references:
        for message_id in reference.source_message_ids:
            references_by_message.setdefault(message_id, []).append(reference)
    values: List[Mapping[str, object]] = []
    chunks: Dict[str, Mapping[str, object]] = {}
    for message in messages:
        value, message_chunks = _message_value(
            context,
            message,
            references_by_message.get(message.message_id, []),
        )
        values.append(value)
        for path, document in message_chunks.items():
            if path in chunks:
                raise ValueError("duplicate_message_chunk_path")
            chunks[path] = document
    pages = _paginate(
        context,
        values,
        "messages_page",
        "messages",
        "messages/pages/page-{page:06d}.json",
        warnings,
    )
    return pages, chunks


def _build_summary_pages(
    context: JsonContext,
    summaries: Sequence[ChangeSummary],
    warnings: Sequence[Mapping[str, object]],
) -> Dict[str, Mapping[str, object]]:
    values = [_summary_value(summary) for summary in summaries]
    return _paginate(
        context,
        values,
        "change_summaries_page",
        "summaries",
        "messages/summaries/summary-page-{page:06d}.json",
        warnings,
    )


def _paginate(
    context: JsonContext,
    values: Sequence[Mapping[str, object]],
    data_type: str,
    collection_key: str,
    path_pattern: str,
    warnings: Sequence[Mapping[str, object]],
) -> Dict[str, Mapping[str, object]]:
    documents: Dict[str, Mapping[str, object]] = {}
    current: List[Mapping[str, object]] = []
    page_number = 1
    for value in values:
        candidate = current + [value]
        document = _page_document(
            context, data_type, collection_key, page_number, candidate, warnings
        )
        if current and (
            len(candidate) > PAGE_MAX_ITEMS
            or len(encode_json(document)) > PAGE_MAX_BYTES
        ):
            path = path_pattern.format(page=page_number)
            documents[path] = _page_document(
                context, data_type, collection_key, page_number, current, warnings
            )
            page_number += 1
            current = [value]
        else:
            current = candidate
    if current:
        path = path_pattern.format(page=page_number)
        documents[path] = _page_document(
            context, data_type, collection_key, page_number, current, warnings
        )
    return documents


def _page_document(
    context: JsonContext,
    data_type: str,
    collection_key: str,
    page: int,
    values: Sequence[Mapping[str, object]],
    warnings: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    result = _common(context, data_type, warnings)
    result["page"] = page
    result[collection_key] = list(values)
    return result


def _messages_index(
    context: JsonContext,
    messages: Sequence[ExtractedChatMessage],
    message_pages: Mapping[str, Mapping[str, object]],
    summaries: Sequence[ChangeSummary],
    summary_pages: Mapping[str, Mapping[str, object]],
    warnings: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    result = _common(context, "messages", warnings)
    result.update(
        {
            "total_messages": len(messages),
            "latest_sequence": messages[-1].sequence if messages else None,
            "pages": [
                _message_page_index(path, document)
                for path, document in message_pages.items()
            ],
            "total_change_summaries": len(summaries),
            "summary_pages": [
                _summary_page_index(path, document)
                for path, document in summary_pages.items()
            ],
        }
    )
    return result


def _message_page_index(
    path: str, document: Mapping[str, object]
) -> Mapping[str, object]:
    messages = document["messages"]
    assert isinstance(messages, list) and messages
    encoded = encode_json(document)
    return {
        "page": document["page"],
        "path": path,
        "first_sequence": messages[0]["sequence"],
        "last_sequence": messages[-1]["sequence"],
        "message_count": len(messages),
        "byte_size": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _summary_page_index(
    path: str, document: Mapping[str, object]
) -> Mapping[str, object]:
    summaries = document["summaries"]
    assert isinstance(summaries, list) and summaries
    encoded = encode_json(document)
    return {
        "page": document["page"],
        "path": path,
        "summary_count": len(summaries),
        "byte_size": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def _message_value(
    context: JsonContext,
    message: ExtractedChatMessage,
    references: Sequence[ExtractedFileReference],
) -> Tuple[Mapping[str, object], Dict[str, Mapping[str, object]]]:
    blocks, redactions = _content_blocks(message)
    value = {
        "message_id": message.message_id,
        "source_message_id": message.source_message_id,
        "sequence": message.sequence,
        "created_at": message.created_at,
        "role": message.role,
        "message_type": message.message_type,
        "phase": message.phase,
        "turn_id": message.turn_id,
        "content": {
            "kind": "blocks",
            "blocks": blocks,
        },
        "redactions": redactions,
        "display_mode": message.display_mode,
        "duplicate_of": message.duplicate_of,
        "occurrence_count": message.occurrence_count,
        "removed_automatic_contexts": list(message.removed_automatic_contexts),
        "file_references": [_file_reference_value(item) for item in references],
    }
    if len(encode_json(value)) <= MESSAGE_CHUNK_MAX_BYTES:
        return value, {}

    chunk_documents = _chunk_message_blocks(context, message.message_id, blocks)
    original = "".join(str(block["text"]) for block in blocks).encode("utf-8")
    chunk_references = []
    for path, document in chunk_documents.items():
        encoded = encode_json(document)
        chunk_references.append(
            {
                "part": document["part"],
                "path": path,
                "byte_size": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    value["content"] = {
        "kind": "chunked_blocks",
        "original_byte_size": len(original),
        "chunk_count": len(chunk_documents),
        "chunks": chunk_references,
        "sha256": hashlib.sha256(original).hexdigest(),
    }
    return value, chunk_documents


def _chunk_message_blocks(
    context: JsonContext,
    message_id: str,
    blocks: Sequence[Mapping[str, object]],
) -> Dict[str, Mapping[str, object]]:
    total_parts = 1
    while True:
        parts = _split_blocks_for_chunks(context, message_id, blocks, total_parts)
        if len(parts) == total_parts:
            break
        total_parts = len(parts)

    stem = _safe_message_filename(message_id)
    documents: Dict[str, Mapping[str, object]] = {}
    for part, chunk_blocks in enumerate(parts, start=1):
        path = f"messages/chunks/{stem}-part-{part:06d}.json"
        document = _message_chunk_document(
            context, message_id, part, total_parts, chunk_blocks
        )
        if len(encode_json(document)) > MESSAGE_CHUNK_MAX_BYTES:
            raise ValueError("message_chunk_too_large")
        documents[path] = document
    return documents


def _split_blocks_for_chunks(
    context: JsonContext,
    message_id: str,
    blocks: Sequence[Mapping[str, object]],
    total_parts: int,
) -> List[List[Mapping[str, object]]]:
    chunks: List[List[Mapping[str, object]]] = []
    current: List[Mapping[str, object]] = []
    part = 1
    for block in blocks:
        remaining = str(block["text"])
        pending = True
        while pending:
            candidate_block = dict(block)
            candidate_block["text"] = remaining
            candidate = current + [candidate_block]
            document = _message_chunk_document(
                context, message_id, part, total_parts, candidate
            )
            if len(encode_json(document)) <= MESSAGE_CHUNK_MAX_BYTES:
                current = candidate
                pending = False
                continue
            if current:
                chunks.append(current)
                current = []
                part += 1
                continue
            piece, remaining = _largest_fitting_text(
                context,
                message_id,
                part,
                total_parts,
                block,
                remaining,
            )
            piece_block = dict(block)
            piece_block["text"] = piece
            chunks.append([piece_block])
            part += 1
            pending = bool(remaining)
    if current:
        chunks.append(current)
    return chunks


def _largest_fitting_text(
    context: JsonContext,
    message_id: str,
    part: int,
    total_parts: int,
    block: Mapping[str, object],
    text: str,
) -> Tuple[str, str]:
    low = 1
    high = len(text)
    fitting = 0
    while low <= high:
        middle = (low + high) // 2
        value = dict(block)
        value["text"] = text[:middle]
        document = _message_chunk_document(
            context, message_id, part, total_parts, [value]
        )
        if len(encode_json(document)) <= MESSAGE_CHUNK_MAX_BYTES:
            fitting = middle
            low = middle + 1
        else:
            high = middle - 1
    if fitting == 0:
        raise ValueError("message_chunk_envelope_too_large")
    return text[:fitting], text[fitting:]


def _message_chunk_document(
    context: JsonContext,
    message_id: str,
    part: int,
    total_parts: int,
    blocks: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    result = _common(context, "message_chunk", [])
    result.update(
        {
            "message_id": message_id,
            "part": part,
            "total_parts": total_parts,
            "blocks": list(blocks),
        }
    )
    return result


def _safe_message_filename(message_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]", "_", message_id)
    if normalized == message_id and len(normalized) <= 120:
        return normalized
    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:16]
    prefix = normalized[:100].rstrip("._-") or "message"
    return f"{prefix}-{digest}"


def _content_blocks(
    message: ExtractedChatMessage,
) -> Tuple[List[Mapping[str, object]], List[Mapping[str, object]]]:
    blocks: List[Mapping[str, object]] = []
    redaction_values: List[Mapping[str, object]] = []
    for part in message.content:
        text = part.text if part.kind == "text" else f"{part.kind}: {part.text}"
        parsed = _parse_fenced_text(text)
        if parsed is None:
            parsed = [("text", None, text)]
        remaining_redactions = list(part.redactions)
        for block_type, language, block_text in parsed:
            index = len(blocks) + 1
            block_id = _block_id(message.message_id, index)
            value: Dict[str, object] = {
                "block_id": block_id,
                "type": block_type,
                "text": block_text,
            }
            if block_type == "code":
                value["language"] = language
            blocks.append(value)
            for marker in re.finditer(
                r"\[REDACTED:(API_KEY|TOKEN|PASSWORD|PRIVATE_KEY|URL_CREDENTIAL)\]",
                block_text,
            ):
                redaction_type = marker.group(1).lower()
                redaction_index = next(
                    (
                        position
                        for position, redaction in enumerate(remaining_redactions)
                        if redaction.type == redaction_type
                    ),
                    None,
                )
                detector = "existing_marker"
                if redaction_index is not None:
                    detector = remaining_redactions.pop(redaction_index).detector
                ordinal = len(redaction_values) + 1
                identity = (
                    f"{message.message_id}\0{block_id}\0{redaction_type}\0"
                    f"{detector}\0{ordinal}"
                )
                redaction_values.append(
                    {
                        "redaction_id": "redaction_"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                        "type": redaction_type,
                        "block_id": block_id,
                        "detector": detector,
                    }
                )
    return blocks, redaction_values


def _parse_fenced_text(
    text: str,
) -> Optional[List[Tuple[str, Optional[str], str]]]:
    if "```" not in text:
        return [("text", None, text)]
    blocks: List[Tuple[str, Optional[str], str]] = []
    position = 0
    for match in _FENCE.finditer(text):
        prefix = text[position : match.start()]
        if prefix:
            blocks.append(("text", None, prefix))
        language = match.group(2).strip() or None
        blocks.append(("code", language, match.group(3)))
        position = match.end()
    if not blocks or "```" in text[position:]:
        return None
    suffix = text[position:]
    if suffix:
        blocks.append(("text", None, suffix))
    return blocks


def _block_id(message_id: str, index: int) -> str:
    source = f"{message_id}\0block\0{index}".encode("utf-8")
    return "block_" + hashlib.sha256(source).hexdigest()


def _file_reference_value(reference: ExtractedFileReference) -> Mapping[str, object]:
    return {
        "reference_id": reference.reference_id,
        "scope": reference.scope,
        "path": reference.path,
        "display_name": reference.display_name,
        "extension": reference.extension,
        "kind": reference.kind,
        "mention_count": reference.mention_count,
        "source_session_ids": list(reference.source_session_ids),
        "source_message_ids": list(reference.source_message_ids),
        "mentions": [
            {
                "session_id": mention.session_id,
                "message_id": mention.message_id,
                "line": mention.line,
                "column": mention.column,
                "origin": mention.origin,
            }
            for mention in reference.mentions
        ],
    }


def _summary_value(summary: ChangeSummary) -> Mapping[str, object]:
    return {
        "summary_id": summary.summary_id,
        "turn_id": summary.turn_id,
        "turn_id_source": summary.turn_id_source,
        "status": summary.status,
        "rolled_back": summary.rolled_back,
        "title": summary.title,
        "short_summary": summary.short_summary,
        "details": summary.details,
        "highlights": [_evidence_value(item) for item in summary.highlights],
        "verification": [_evidence_value(item) for item in summary.verification],
        "origin": summary.origin,
        "confidence": summary.confidence,
        "source_session_ids": list(summary.source_session_ids),
        "source_message_ids": list(summary.source_message_ids),
    }


def _evidence_value(item: SummaryEvidenceItem) -> Mapping[str, object]:
    return {"text": item.text, "source_message_ids": list(item.source_message_ids)}


def _recent_document(
    context: JsonContext,
    work_status: CurrentWorkStatus,
    messages: Sequence[ExtractedChatMessage],
    warnings: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    by_id = {message.message_id: message for message in messages}
    active: Optional[Mapping[str, object]] = None
    completed: List[Mapping[str, object]] = []
    for turn in reversed(work_status.turns):
        value = _turn_value(turn, by_id)
        if value is None:
            continue
        if turn.status == "in_progress" and active is None:
            active = value
        elif not turn.rolled_back and len(completed) < 2:
            completed.append(value)
    result = _common(context, "recent", warnings)
    result.update({"current_turn": active, "turns": completed})
    return result


def _turn_value(
    turn: TurnWorkState,
    messages: Mapping[str, ExtractedChatMessage],
) -> Optional[Mapping[str, object]]:
    if turn.started_at is None or turn.user_message_id is None:
        return None
    user = messages.get(turn.user_message_id)
    assistants = [
        messages[message_id]
        for message_id in turn.assistant_message_ids
        if message_id in messages
    ]
    return {
        "turn_id": turn.turn_id,
        "turn_id_source": turn.turn_id_source,
        "status": turn.status,
        "started_at": turn.started_at,
        "started_at_source": turn.started_at_source,
        "completed_at": turn.completed_at,
        "completed_at_source": turn.completed_at_source,
        "user_message_id": turn.user_message_id,
        "assistant_message_ids": list(turn.assistant_message_ids),
        "user_preview": _preview(user),
        "assistant_preview": _preview(assistants[-1]) if assistants else None,
        "rolled_back": turn.rolled_back,
    }


def _preview(message: Optional[ExtractedChatMessage]) -> str:
    if message is None:
        return ""
    text = "".join(part.text for part in message.content)
    return text[:PREVIEW_MAX_CHARACTERS]


def _counted_open_errors(
    errors: Sequence[ExtractedDevelopmentError],
) -> Tuple[ExtractedDevelopmentError, ...]:
    return tuple(
        error for error in errors if error.status == "open" and not error.rolled_back
    )


def _error_counts(
    errors: Sequence[ExtractedDevelopmentError],
) -> Mapping[str, int]:
    open_errors = _counted_open_errors(errors)
    return {
        "open": len(open_errors),
        "critical": sum(error.severity == "critical" for error in open_errors),
    }


def _dashboard_document(
    context: JsonContext,
    project: ProjectPresentation,
    messages: Sequence[ExtractedChatMessage],
    work_status: CurrentWorkStatus,
    task: Optional[NextTask],
    errors: Sequence[ExtractedDevelopmentError],
    git: GitCollectionResult,
    summaries: Sequence[ChangeSummary],
    warnings: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    user_messages = [message for message in messages if message.role == "user"]
    assistant_messages = [
        message
        for message in messages
        if message.role == "assistant" and message.message_type == "chat"
    ]
    effective_summaries = [summary for summary in summaries if not summary.rolled_back]
    open_errors = _counted_open_errors(errors)
    error_counts = _error_counts(errors)
    latest_error = max(
        open_errors,
        key=lambda value: value.last_occurred_at or "",
        default=None,
    )
    result = _common(context, "dashboard", warnings)
    result.update(
        {
            "project": {"name": project.name, "phase": project.phase},
            "codex": {
                "status": work_status.codex_status,
                "current_work": work_status.current_work,
            },
            "latest": {
                "user_message_id": (
                    user_messages[-1].message_id if user_messages else None
                ),
                "assistant_message_id": (
                    assistant_messages[-1].message_id
                    if assistant_messages
                    else None
                ),
                "summary": (
                    effective_summaries[-1].short_summary
                    if effective_summaries
                    else None
                ),
            },
            "next_actions": [_next_task_value(task)] if task is not None else [],
            "errors": {
                "open": error_counts["open"],
                "critical": error_counts["critical"],
                "latest_error_id": (
                    latest_error.error_id if latest_error is not None else None
                ),
            },
            "git": {
                "collection_status": git.repository.collection_status,
                "branch": git.repository.branch,
                "changed_files": len(git.files),
                "staged_files": sum("staged" in value.scopes for value in git.files),
                "untracked_files": sum(
                    value.status.worktree == "untracked" for value in git.files
                ),
            },
        }
    )
    return result


def _next_task_value(task: NextTask) -> Mapping[str, object]:
    return {
        "task_id": task.task_id,
        "text": task.text,
        "status": task.status,
        "origin": task.origin,
        "confidence": task.confidence,
        "reason": task.reason,
        "source_message_ids": list(task.source_message_ids),
    }


def _errors_document(
    context: JsonContext,
    errors: Sequence[ExtractedDevelopmentError],
    warnings: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    result = _common(context, "errors", warnings)
    result["counts"] = _error_counts(errors)
    result["errors"] = [_error_value(error) for error in errors]
    return result


def _error_value(error: ExtractedDevelopmentError) -> Mapping[str, object]:
    if error.details_complete and error.details_text is not None:
        storage = "inline"
        details = error.details_text
    elif error.details_complete and error.source_message_ids:
        storage = "message_reference"
        details = None
    else:
        storage = "preview_only"
        details = None
    return {
        "error_id": error.error_id,
        "fingerprint": error.fingerprint,
        "kind": error.kind,
        "first_occurred_at": error.first_occurred_at,
        "last_occurred_at": error.last_occurred_at,
        "occurred_at_source": error.occurred_at_source,
        "occurrence_count": error.occurrence_count,
        "severity": error.severity,
        "category": error.category,
        "summary": error.summary,
        "details_preview": error.details_preview,
        "details_complete": error.details_complete,
        "source_message_ids": list(error.source_message_ids),
        "source_line_number": error.source_line_number,
        "source_start_offset": error.source_start_offset,
        "detail_storage": storage,
        "details": details,
        "detail_chunks": [],
        "status": error.status,
        "resolved_at": error.resolved_at,
        "resolution": error.resolution,
        "rolled_back": error.rolled_back,
    }


def _decisions_document(
    context: JsonContext,
    decisions: Sequence[ExtractedDecision],
    warnings: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    result = _common(context, "decisions", warnings)
    result["decisions"] = [_decision_value(decision) for decision in decisions]
    return result


def _decision_value(decision: ExtractedDecision) -> Mapping[str, object]:
    return {
        "decision_id": decision.decision_id,
        "decided_at": decision.decided_at,
        "decided_at_source": decision.decided_at_source,
        "status": decision.status,
        "title": decision.title,
        "description": decision.description,
        "reason": decision.reason,
        "topic_key": decision.topic_key,
        "source_session_ids": list(decision.source_session_ids),
        "source_message_ids": list(decision.source_message_ids),
        "supersedes": decision.supersedes,
        "superseded_by": decision.superseded_by,
    }


def _files_document(
    context: JsonContext,
    git: GitCollectionResult,
    warnings: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    result = _common(context, "files", warnings)
    repository = git.repository
    result.update(
        {
            "repository": {
                "collection_status": repository.collection_status,
                "root_name": repository.root_name,
                "branch": repository.branch,
                "head": repository.head,
                "session_start_commit": repository.session_start_commit,
                "session_start_source": repository.session_start_source,
                "clean": repository.clean,
                "retry_required": git.retry_required,
            },
            "files": [_file_change_value(value) for value in git.files],
        }
    )
    return result


def _file_change_value(change: GitFileChange) -> Mapping[str, object]:
    return {
        "change_id": change.change_id,
        "path": change.path,
        "old_path": change.old_path,
        "status": {
            "index": change.status.index,
            "worktree": change.status.worktree,
            "committed_in_session": change.status.committed_in_session,
        },
        "numstat": {
            "staged": _numstat_value(change.staged),
            "unstaged": _numstat_value(change.unstaged),
            "committed_in_session": _numstat_value(change.committed_in_session),
        },
        "binary": change.binary,
        "scopes": list(change.scopes),
    }


def _numstat_value(value: GitNumstat) -> Mapping[str, object]:
    return {"state": value.state, "added": value.added, "deleted": value.deleted}


def _metadata_document(
    context: JsonContext,
    collector: CollectorMetadata,
    work_status: CurrentWorkStatus,
    documents: Mapping[str, Mapping[str, object]],
) -> Mapping[str, object]:
    result = _common(context, "metadata", [])
    files = []
    for path, document in documents.items():
        encoded = encode_json(document)
        files.append(
            {
                "path": path,
                "byte_size": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
        )
    result.update(
        {
            "collector": {
                "status": collector.status,
                "codex_status": work_status.codex_status,
                "last_checked_at": collector.last_checked_at,
                "last_data_change_at": collector.last_data_change_at,
                "last_acknowledged_snapshot_id": (
                    collector.last_acknowledged_snapshot_id
                ),
                "last_send_succeeded_at": collector.last_send_succeeded_at,
            },
            "snapshot": {"state": "complete", "files": files},
        }
    )
    return result


def _warnings(
    scope: str,
    issues: Iterable[object],
    context: JsonContext,
) -> List[Mapping[str, object]]:
    result: List[Mapping[str, object]] = []
    seen_ids: Dict[str, None] = {}
    for issue in issues:
        code = getattr(issue, "kind", "unknown_warning")
        source_id = getattr(issue, "message_id", None)
        source_ids = [source_id] if isinstance(source_id, str) else []
        occurred_at = getattr(issue, "occurred_at", None)
        line_number = getattr(issue, "line_number", None)
        offset = getattr(issue, "offset", None)
        turn_id = getattr(issue, "turn_id", None)
        command = getattr(issue, "command", None)
        identity = (
            f"{context.workspace_id}\0{context.session_id}\0{scope}\0{code}\0"
            f"{line_number}\0{offset}\0{turn_id}\0{command}\0"
            + "\0".join(source_ids)
        )
        warning_id = "warning_" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()
        if warning_id in seen_ids:
            continue
        seen_ids[warning_id] = None
        result.append(
            {
                "warning_id": warning_id,
                "severity": "warning",
                "code": code,
                "summary": f"{scope}の処理中に警告が発生しました（{code}）。",
                "occurred_at": occurred_at,
                "source_message_ids": source_ids,
            }
        )
    return result


def _validate_context(
    context: JsonContext,
    project: ProjectPresentation,
    collector: CollectorMetadata,
) -> None:
    required = (
        context.schema_version,
        context.snapshot_id,
        context.generated_at,
        context.workspace_id,
        context.session_id,
        project.name,
        collector.status,
        collector.last_checked_at,
    )
    if any(not value for value in required):
        raise ValueError("missing_required_json_context")
