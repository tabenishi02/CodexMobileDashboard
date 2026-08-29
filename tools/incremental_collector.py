"""Collect only newly completed, safely normalized Codex JSONL records."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from tools.collector_state import (
    CollectorState,
    ResumePosition,
    advance_session_cursor,
    resume_position,
)
from tools.record_deduplicator import (
    RecordDeduplicationIssue,
    deduplicate_records,
)
from tools.record_normalizer import (
    NormalizationIssue,
    NormalizedRecord,
    normalize_record,
)
from tools.secret_redactor import RedactionCount, redact_normalized_records
from tools.session_reader import JsonlReadStream, ReadIssue


LOGGER = logging.getLogger("collector")


@dataclass(frozen=True)
class IncrementalCollectionResult:
    """Safe new records and the candidate state to persist after later stages succeed."""

    records: Tuple[NormalizedRecord, ...]
    next_state: CollectorState
    resume: ResumePosition
    read_issues: Tuple[ReadIssue, ...]
    normalization_issues: Tuple[NormalizationIssue, ...]
    deduplication_issues: Tuple[RecordDeduplicationIssue, ...]
    redaction_counts: Tuple[RedactionCount, ...]
    duplicate_count: int
    has_incomplete_tail: bool


def collect_incremental_records(
    session_id: str, path: Path, state: CollectorState
) -> IncrementalCollectionResult:
    """Read a session's appended complete lines through the safe collection stages.

    ``next_state`` is intentionally not written here. The caller saves it only after
    every downstream extraction, JSON conversion, and output step has succeeded.
    """

    if not session_id:
        raise ValueError("session_id_missing")

    path = Path(path)
    resume = resume_position(state, session_id, path)
    normalized_records = []
    normalization_issues = []
    with JsonlReadStream(
        path, resume.start_offset, resume.start_line_number
    ) as reader:
        for source in reader:
            result = normalize_record(source)
            normalization_issues.extend(result.issues)
            if result.record is not None:
                normalized_records.append(result.record)
        complete_offset = reader.next_offset
        has_incomplete_tail = reader.has_incomplete_tail
        complete_line_number = reader.line_number - int(has_incomplete_tail)
        read_issues = tuple(reader.issues)

    redacted = redact_normalized_records(tuple(normalized_records))
    deduplicated = deduplicate_records(
        redacted.records,
        session_id,
        content_is_masked=redacted.is_masked,
        previous_state=resume.deduplication,
        replay_from_start=resume.replay_from_start,
    )
    next_state = advance_session_cursor(
        state,
        session_id,
        path,
        complete_offset,
        complete_line_number,
        deduplicated.state,
    )
    collection = IncrementalCollectionResult(
        deduplicated.records,
        next_state,
        resume,
        read_issues,
        tuple(normalization_issues),
        deduplicated.issues,
        redacted.counts,
        deduplicated.duplicate_count,
        has_incomplete_tail,
    )
    _log_collection_result(session_id, collection)
    return collection


def _log_collection_result(
    session_id: str, result: IncrementalCollectionResult
) -> None:
    """Log only counts and stable identifiers; never record session content."""

    redacted_count = sum(item.count for item in result.redaction_counts)
    LOGGER.info(
        "incremental_collection_completed session_id=%s records=%d duplicates=%d "
        "redactions=%d read_issues=%d normalization_issues=%d "
        "deduplication_issues=%d",
        session_id,
        len(result.records),
        result.duplicate_count,
        redacted_count,
        len(result.read_issues),
        len(result.normalization_issues),
        len(result.deduplication_issues),
    )
    if result.resume.reset_reason is not None:
        LOGGER.warning(
            "incremental_collection_replayed session_id=%s reason=%s",
            session_id,
            result.resume.reset_reason,
        )
    if result.has_incomplete_tail:
        LOGGER.warning(
            "incremental_collection_incomplete_tail session_id=%s", session_id
        )
