"""Persist only secret-redacted normalized records needed for later snapshots."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

from tools.record_normalizer import (
    NormalizedContentPart,
    NormalizedRecord,
    NormalizedRedaction,
)


HISTORY_VERSION = 1


class InvalidCollectorHistoryError(ValueError):
    """Raised when redacted derived history cannot be safely loaded."""


@dataclass(frozen=True)
class CollectorHistory:
    """Records grouped by session; input must already have been redacted."""

    sessions: Tuple[Tuple[str, Tuple[NormalizedRecord, ...]], ...] = tuple()

    def records_for(self, session_id: str) -> Tuple[NormalizedRecord, ...]:
        return dict(self.sessions).get(session_id, tuple())

    def replace(self, session_id: str, records: Iterable[NormalizedRecord]) -> "CollectorHistory":
        values = {key: value for key, value in self.sessions}
        values[session_id] = tuple(records)
        return CollectorHistory(tuple(sorted(values.items())))


def load_collector_history(path: Path) -> CollectorHistory:
    path = Path(path)
    if not path.exists():
        return CollectorHistory()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidCollectorHistoryError("history_unreadable") from error
    if not isinstance(value, dict) or value.get("version") != HISTORY_VERSION:
        raise InvalidCollectorHistoryError("history_version_invalid")
    sessions = value.get("sessions")
    if not isinstance(sessions, dict):
        raise InvalidCollectorHistoryError("history_sessions_invalid")
    result = []
    for session_id, records in sessions.items():
        if not isinstance(session_id, str) or not session_id or not isinstance(records, list):
            raise InvalidCollectorHistoryError("history_session_invalid")
        result.append((session_id, tuple(_record(item) for item in records)))
    return CollectorHistory(tuple(sorted(result)))


def save_collector_history(history: CollectorHistory, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"version": HISTORY_VERSION, "sessions": {key: [_value(record) for record in records] for key, records in history.sessions}}
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _value(record: NormalizedRecord) -> Dict[str, Any]:
    return {
        "timestamp": record.timestamp, "category": record.category, "subtype": record.subtype,
        "source_record_type": record.source_record_type, "source_payload_type": record.source_payload_type,
        "source_id": record.source_id, "turn_id": record.turn_id, "role": record.role, "phase": record.phase,
        "content": [{"kind": part.kind, "text": part.text, "source_type": part.source_type,
                     "redactions": [{"type": item.type, "detector": item.detector} for item in part.redactions]} for part in record.content],
        "attributes": record.attributes, "source_path": str(record.source_path), "line_number": record.line_number,
        "start_offset": record.start_offset, "end_offset": record.end_offset,
    }


def _record(value: object) -> NormalizedRecord:
    if not isinstance(value, dict):
        raise InvalidCollectorHistoryError("history_record_invalid")
    required_text = ("category", "subtype", "source_record_type")
    if not all(isinstance(value.get(key), str) for key in required_text):
        raise InvalidCollectorHistoryError("history_record_invalid")
    content_value = value.get("content")
    attributes = value.get("attributes")
    if not isinstance(content_value, list) or not isinstance(attributes, dict):
        raise InvalidCollectorHistoryError("history_record_invalid")
    try:
        content = tuple(NormalizedContentPart(str(item["kind"]), item.get("text"), item.get("source_type"), tuple(NormalizedRedaction(str(redaction["type"]), str(redaction["detector"])) for redaction in item.get("redactions", []))) for item in content_value if isinstance(item, dict))
        if len(content) != len(content_value): raise ValueError
        return NormalizedRecord(value.get("timestamp"), value["category"], value["subtype"], value["source_record_type"], value.get("source_payload_type"), value.get("source_id"), value.get("turn_id"), value.get("role"), value.get("phase"), content, attributes, Path(str(value["source_path"])), int(value["line_number"]), int(value["start_offset"]), int(value["end_offset"]))
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidCollectorHistoryError("history_record_invalid") from error
