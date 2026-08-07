"""Codex rollout JSONL discovery and incremental reading."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


LOGGER = logging.getLogger("collector")

KNOWN_RECORD_TYPES = frozenset(
    {
        "session_meta",
        "turn_context",
        "response_item",
        "event_msg",
        "world_state",
        "compacted",
    }
)

_SESSION_ID_PATTERN = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?=\.jsonl$)"
)


@dataclass(frozen=True)
class DiscoveredSessionFile:
    path: Path
    location: str


@dataclass(frozen=True)
class SessionRecord:
    path: Path
    line_number: int
    start_offset: int
    end_offset: int
    timestamp: str
    record_type: str
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ReadIssue:
    path: Path
    line_number: int
    offset: int
    kind: str


@dataclass(frozen=True)
class SessionDescriptor:
    session_id: str
    path: Path
    location: str
    last_timestamp: Optional[str]
    modified_time_ns: int
    record_count: int
    workspace_candidates: Tuple[str, ...]
    complete_offset: int
    has_incomplete_tail: bool
    issues: Tuple[ReadIssue, ...]


@dataclass(frozen=True)
class SessionIndexEntry:
    session_id: str
    current_file: SessionDescriptor
    files: Tuple[SessionDescriptor, ...]


class JsonlReadStream(Iterator[SessionRecord]):
    """Read complete JSONL lines while retaining an incomplete trailing line."""

    def __init__(
        self, path: Path, start_offset: int = 0, start_line_number: int = 0
    ) -> None:
        if start_offset < 0:
            raise ValueError("start_offset must be zero or greater")
        if start_line_number < 0:
            raise ValueError("start_line_number must be zero or greater")
        self.path = Path(path)
        self.requested_offset = start_offset
        self.next_offset = start_offset
        self.line_number = start_line_number
        self.has_incomplete_tail = False
        self.issues: List[ReadIssue] = []
        self._stream = None
        self._finished = False

    def __enter__(self) -> "JsonlReadStream":
        self._open()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def __iter__(self) -> "JsonlReadStream":
        return self

    def __next__(self) -> SessionRecord:
        if self._finished:
            raise StopIteration
        self._open()
        assert self._stream is not None

        while True:
            start_offset = self._stream.tell()
            raw_line = self._stream.readline()
            if not raw_line:
                self._finished = True
                self.close()
                raise StopIteration

            self.line_number += 1
            if not raw_line.endswith(b"\n"):
                self.has_incomplete_tail = True
                self.next_offset = start_offset
                LOGGER.debug(
                    "JSONL末尾の未完成行を次回へ保留: path=%s line=%d offset=%d",
                    self.path,
                    self.line_number,
                    start_offset,
                )
                self._finished = True
                self.close()
                raise StopIteration

            end_offset = self._stream.tell()
            self.next_offset = end_offset
            record = self._parse_line(raw_line, start_offset, end_offset)
            if record is not None:
                return record

    def close(self) -> None:
        if self._stream is not None:
            self._stream.close()
            self._stream = None

    def _open(self) -> None:
        if self._stream is not None:
            return
        try:
            file_size = self.path.stat().st_size
            self._stream = self.path.open("rb")
        except OSError:
            LOGGER.exception("Codex JSONLを開けません: path=%s", self.path)
            raise

        actual_offset = self.requested_offset
        if file_size < actual_offset:
            self._add_issue(0, actual_offset, "file_truncated")
            actual_offset = 0
            self.next_offset = 0
            self.line_number = 0
        self._stream.seek(actual_offset)

    def _parse_line(
        self, raw_line: bytes, start_offset: int, end_offset: int
    ) -> Optional[SessionRecord]:
        try:
            encoding = "utf-8-sig" if start_offset == 0 else "utf-8"
            value = json.loads(raw_line.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._add_issue(self.line_number, start_offset, "malformed_json")
            return None

        if not isinstance(value, dict):
            self._add_issue(self.line_number, start_offset, "invalid_record")
            return None

        timestamp = value.get("timestamp")
        record_type = value.get("type")
        payload = value.get("payload")
        if (
            not isinstance(timestamp, str)
            or not isinstance(record_type, str)
            or not isinstance(payload, dict)
        ):
            self._add_issue(self.line_number, start_offset, "invalid_record")
            return None

        if record_type not in KNOWN_RECORD_TYPES:
            self._add_issue(self.line_number, start_offset, "unknown_record_type")
            return None

        return SessionRecord(
            path=self.path,
            line_number=self.line_number,
            start_offset=start_offset,
            end_offset=end_offset,
            timestamp=timestamp,
            record_type=record_type,
            payload=payload,
        )

    def _add_issue(self, line_number: int, offset: int, kind: str) -> None:
        issue = ReadIssue(self.path, line_number, offset, kind)
        self.issues.append(issue)
        LOGGER.warning(
            "Codex JSONL読み取り警告: path=%s line=%d offset=%d kind=%s",
            self.path,
            line_number,
            offset,
            kind,
        )


def discover_session_files(
    sessions_dir: Path,
    archived_sessions_dir: Optional[Path] = None,
    scan_archived_sessions: bool = True,
) -> Tuple[DiscoveredSessionFile, ...]:
    """Find every rollout JSONL in live and optionally archived storage."""

    locations = [(Path(sessions_dir), "sessions")]
    if scan_archived_sessions and archived_sessions_dir is not None:
        locations.append((Path(archived_sessions_dir), "archived_sessions"))

    found: Dict[str, DiscoveredSessionFile] = {}
    for directory, location in locations:
        if not directory.exists():
            if location == "archived_sessions":
                LOGGER.warning(
                    "アーカイブ済みCodexセッションの保存先がありません: path=%s",
                    directory,
                )
                continue
            raise FileNotFoundError(str(directory))
        if not directory.is_dir():
            raise NotADirectoryError(str(directory))

        for path in directory.rglob("rollout-*.jsonl"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            key = str(resolved).casefold()
            found.setdefault(key, DiscoveredSessionFile(resolved, location))

    result = tuple(sorted(found.values(), key=lambda item: str(item.path).casefold()))
    LOGGER.info("Codexセッションファイルを検出: count=%d", len(result))
    return result


def inspect_session_file(session_file: DiscoveredSessionFile) -> SessionDescriptor:
    """Read one complete file to collect session-level index metadata."""

    session_id = _session_id_from_filename(session_file.path)
    last_timestamp: Optional[str] = None
    last_timestamp_key = float("-inf")
    candidates: Dict[str, str] = {}
    record_count = 0

    with JsonlReadStream(session_file.path) as reader:
        for record in reader:
            record_count += 1
            timestamp_key = _timestamp_key(record.timestamp)
            if timestamp_key >= last_timestamp_key:
                last_timestamp = record.timestamp
                last_timestamp_key = timestamp_key

            if record.record_type == "session_meta":
                payload_session_id = record.payload.get("session_id")
                if not isinstance(payload_session_id, str):
                    payload_session_id = record.payload.get("id")
                if isinstance(payload_session_id, str) and payload_session_id:
                    session_id = payload_session_id
                _add_path_candidate(candidates, record.payload.get("cwd"))
            elif record.record_type == "turn_context":
                _add_path_candidate(candidates, record.payload.get("cwd"))
                roots = record.payload.get("workspace_roots")
                if isinstance(roots, list):
                    for root in roots:
                        _add_path_candidate(candidates, root)

        complete_offset = reader.next_offset
        has_incomplete_tail = reader.has_incomplete_tail
        issues = tuple(reader.issues)

    if session_id is None:
        digest = hashlib.sha256(str(session_file.path).encode("utf-8")).hexdigest()[:16]
        session_id = "unidentified-" + digest
        LOGGER.warning("セッションIDを特定できません: path=%s", session_file.path)

    stat_result = session_file.path.stat()
    return SessionDescriptor(
        session_id=session_id,
        path=session_file.path,
        location=session_file.location,
        last_timestamp=last_timestamp,
        modified_time_ns=stat_result.st_mtime_ns,
        record_count=record_count,
        workspace_candidates=tuple(candidates.values()),
        complete_offset=complete_offset,
        has_incomplete_tail=has_incomplete_tail,
        issues=issues,
    )


def build_session_index(
    session_files: Iterable[DiscoveredSessionFile],
) -> Dict[str, SessionIndexEntry]:
    """Group live and archived files by stable Codex session ID."""

    grouped: Dict[str, List[SessionDescriptor]] = {}
    for session_file in session_files:
        descriptor = inspect_session_file(session_file)
        grouped.setdefault(descriptor.session_id, []).append(descriptor)

    index: Dict[str, SessionIndexEntry] = {}
    for session_id, descriptors in grouped.items():
        ordered = sorted(descriptors, key=_descriptor_sort_key, reverse=True)
        index[session_id] = SessionIndexEntry(
            session_id=session_id,
            current_file=ordered[0],
            files=tuple(ordered),
        )

    LOGGER.info("Codexセッション索引を作成: count=%d", len(index))
    return index


def _descriptor_sort_key(descriptor: SessionDescriptor) -> Tuple[float, int, int, str]:
    return (
        _timestamp_key(descriptor.last_timestamp),
        descriptor.modified_time_ns,
        1 if descriptor.location == "sessions" else 0,
        str(descriptor.path).casefold(),
    )


def _timestamp_key(value: Optional[str]) -> float:
    if value is None:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _session_id_from_filename(path: Path) -> Optional[str]:
    match = _SESSION_ID_PATTERN.search(path.name)
    return match.group(1) if match else None


def _add_path_candidate(candidates: Dict[str, str], value: Any) -> None:
    if not isinstance(value, str) or not value:
        return
    candidates.setdefault(value.casefold(), value)
