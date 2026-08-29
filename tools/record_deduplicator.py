"""Remove replayed normalized records without collapsing intentional repeats."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence, Tuple

from tools.record_normalizer import NormalizedRecord


LOGGER = logging.getLogger("collector")


class UnsafeDeduplicationInputError(ValueError):
    """Raised when record fingerprints could contain unmasked secrets."""


@dataclass(frozen=True)
class RecordDeduplicationIssue:
    kind: str
    source_path: Path
    line_number: int
    offset: int


@dataclass(frozen=True)
class RecordDeduplicationState:
    """Serializable values needed to recognize records seen in earlier runs."""

    identities: Tuple[Tuple[str, str], ...] = tuple()
    physical_keys: Tuple[str, ...] = tuple()
    fallback_occurrences: Tuple[Tuple[str, int], ...] = tuple()


@dataclass(frozen=True)
class RecordDeduplicationResult:
    records: Tuple[NormalizedRecord, ...]
    state: RecordDeduplicationState
    duplicate_count: int
    issues: Tuple[RecordDeduplicationIssue, ...]


def deduplicate_records(
    records: Iterable[NormalizedRecord],
    session_id: str,
    *,
    content_is_masked: bool,
    previous_state: RecordDeduplicationState | None = None,
    replay_from_start: bool = False,
) -> RecordDeduplicationResult:
    """Keep each logical JSONL record once across normal and replay reads."""

    if not content_is_masked:
        LOGGER.error("レコード重複排除を中止: 未マスク入力")
        raise UnsafeDeduplicationInputError("content_not_masked")
    if not session_id:
        raise ValueError("session_id_missing")

    state = previous_state or RecordDeduplicationState()
    identities = dict(state.identities)
    physical_keys = set(state.physical_keys)
    prior_occurrences = dict(state.fallback_occurrences)
    occurrences = {} if replay_from_start else dict(prior_occurrences)
    kept = []
    issues = []
    duplicate_count = 0

    for record in records:
        fingerprint = _semantic_fingerprint(record)
        if record.source_id:
            identity = _digest_key(
                "native",
                session_id,
                record.source_record_type,
                record.source_payload_type or "",
                record.source_id,
            )
        else:
            physical_key = _digest_key(
                "physical",
                session_id,
                record.source_record_type,
                str(record.start_offset),
                str(record.end_offset),
                fingerprint,
            )
            if not replay_from_start and physical_key in physical_keys:
                duplicate_count += 1
                continue
            physical_keys.add(physical_key)
            occurrence = occurrences.get(fingerprint, 0) + 1
            occurrences[fingerprint] = occurrence
            identity = _digest_key(
                "fallback", session_id, fingerprint, str(occurrence)
            )

        previous_fingerprint = identities.get(identity)
        if previous_fingerprint is not None:
            duplicate_count += 1
            if previous_fingerprint != fingerprint:
                issue = RecordDeduplicationIssue(
                    "conflicting_native_id",
                    record.source_path,
                    record.line_number,
                    record.start_offset,
                )
                issues.append(issue)
                LOGGER.warning(
                    "Codexレコード重複排除警告: line=%d offset=%d kind=%s",
                    issue.line_number,
                    issue.offset,
                    issue.kind,
                )
            continue
        identities[identity] = fingerprint
        kept.append(record)

    if replay_from_start:
        for fingerprint, count in prior_occurrences.items():
            occurrences[fingerprint] = max(count, occurrences.get(fingerprint, 0))

    next_state = RecordDeduplicationState(
        tuple(sorted(identities.items())),
        tuple(sorted(physical_keys)),
        tuple(sorted(occurrences.items())),
    )
    LOGGER.info(
        "Codexレコードを重複排除: session_id=%s kept=%d duplicates=%d warnings=%d",
        session_id,
        len(kept),
        duplicate_count,
        len(issues),
    )
    return RecordDeduplicationResult(
        tuple(kept), next_state, duplicate_count, tuple(issues)
    )


def _semantic_fingerprint(record: NormalizedRecord) -> str:
    value = {
        "timestamp": record.timestamp,
        "category": record.category,
        "subtype": record.subtype,
        "source_record_type": record.source_record_type,
        "source_payload_type": record.source_payload_type,
        "source_id": record.source_id,
        "turn_id": record.turn_id,
        "role": record.role,
        "phase": record.phase,
        "content": [
            {
                "kind": part.kind,
                "text": part.text,
                "source_type": part.source_type,
                "redactions": [
                    {"type": item.type, "detector": item.detector}
                    for item in part.redactions
                ],
            }
            for part in record.content
        ],
        "attributes": _json_value(record.attributes),
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _digest_key(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
