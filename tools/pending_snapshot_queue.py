"""Persist failed snapshot deliveries until the Android server confirms commit."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional, Tuple

from tools.https_sender import (
    HttpsSnapshotSender,
    SenderError,
    SnapshotRetryResult,
    SnapshotUpload,
    normalize_relative_json_path,
)


LOGGER = logging.getLogger("sender")
_QUEUE_VERSION = 1
_SEQUENCE_FILE = "sequence.json"
_MANIFEST_FILE = "manifest.json"
_FILES_DIRECTORY = "files"


class InvalidPendingSnapshotError(ValueError):
    """Raised when queued data cannot be safely loaded or acknowledged."""


@dataclass(frozen=True)
class QueuedSnapshot:
    item_directory: Path
    sequence: int
    workspace_id: str
    snapshot_id: str
    commit_delivery_id: str
    uploads: Tuple[SnapshotUpload, ...]
    last_error_kind: Optional[str]
    last_error_status: Optional[int]


class PendingSnapshotQueue:
    """A durable, ordered queue of complete snapshots with fixed delivery IDs."""

    def __init__(self, queue_directory: Path) -> None:
        self._root = Path(queue_directory).resolve(strict=False)

    def enqueue(
        self,
        workspace_id: str,
        snapshot_id: str,
        uploads: Iterable[SnapshotUpload],
        *,
        commit_delivery_id: str,
        error: Optional[SenderError] = None,
    ) -> QueuedSnapshot:
        """Atomically preserve a failed snapshot before the caller returns."""

        safe_workspace = _identifier(workspace_id)
        safe_snapshot = _identifier(snapshot_id)
        stable_uploads = tuple(
            sorted(uploads, key=lambda item: normalize_relative_json_path(item.relative_json_path))
        )
        if not stable_uploads:
            raise InvalidPendingSnapshotError("queue_uploads_empty")
        self._root.mkdir(parents=True, exist_ok=True)
        sequence = self._next_sequence()
        workspace_directory = self._root / safe_workspace
        workspace_directory.mkdir(parents=True, exist_ok=True)
        target = workspace_directory / f"{sequence:020d}-{safe_snapshot}"
        if target.exists():
            raise InvalidPendingSnapshotError("queue_sequence_conflict")
        temporary = Path(tempfile.mkdtemp(prefix=".pending-", dir=str(workspace_directory)))
        try:
            files_directory = temporary / _FILES_DIRECTORY
            files_directory.mkdir()
            entries = []
            seen = set()
            for upload in stable_uploads:
                relative_path = normalize_relative_json_path(upload.relative_json_path)
                if relative_path in seen or not isinstance(upload.body, bytes):
                    raise InvalidPendingSnapshotError("queue_upload_invalid")
                seen.add(relative_path)
                destination = files_directory.joinpath(*PurePosixPath(relative_path).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                _write_atomic(destination, upload.body)
                entries.append(
                    {
                        "path": relative_path,
                        "delivery_id": upload.delivery_id,
                        "byte_size": len(upload.body),
                        "sha256": hashlib.sha256(upload.body).hexdigest(),
                    }
                )
            manifest = {
                "version": _QUEUE_VERSION,
                "sequence": sequence,
                "workspace_id": safe_workspace,
                "snapshot_id": safe_snapshot,
                "commit_delivery_id": commit_delivery_id,
                "files": entries,
                "last_error": _error_value(error),
            }
            _write_atomic(temporary / _MANIFEST_FILE, _json_bytes(manifest))
            os.replace(temporary, target)
            self._save_next_sequence(sequence + 1)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        queued = self._load_item(target)
        LOGGER.warning(
            "snapshot_queued workspace_id=%s snapshot_id=%s sequence=%d files=%d error_kind=%s",
            queued.workspace_id,
            queued.snapshot_id,
            queued.sequence,
            len(queued.uploads),
            queued.last_error_kind,
        )
        return queued

    def pending(self) -> Tuple[QueuedSnapshot, ...]:
        """Return all valid queued snapshots in their original global sequence."""

        if not self._root.exists():
            return tuple()
        items = []
        for manifest in self._root.glob(f"*/*/{_MANIFEST_FILE}"):
            items.append(self._load_item(manifest.parent))
        return tuple(sorted(items, key=lambda item: item.sequence))

    def send_or_enqueue(
        self,
        sender: HttpsSnapshotSender,
        workspace_id: str,
        snapshot_id: str,
        uploads: Iterable[SnapshotUpload],
        *,
        commit_delivery_id: str,
    ) -> SnapshotRetryResult:
        """Attempt delivery and durably queue the unchanged snapshot on final failure."""

        stable_uploads = tuple(uploads)
        try:
            return sender.send_snapshot_with_retry(
                workspace_id,
                snapshot_id,
                stable_uploads,
                commit_delivery_id=commit_delivery_id,
            )
        except SenderError as error:
            self.enqueue(
                workspace_id,
                snapshot_id,
                stable_uploads,
                commit_delivery_id=commit_delivery_id,
                error=error,
            )
            raise
    def send_next(self, sender: HttpsSnapshotSender) -> Optional[SnapshotRetryResult]:
        """Commit the oldest queued snapshot and remove it only after acknowledgement."""

        items = self.pending()
        if not items:
            return None
        queued = items[0]
        result = sender.send_snapshot_with_retry(
            queued.workspace_id,
            queued.snapshot_id,
            queued.uploads,
            commit_delivery_id=queued.commit_delivery_id,
        )
        self.acknowledge(queued)
        return result

    def acknowledge(self, queued: QueuedSnapshot) -> None:
        """Remove one acknowledged queue item, never an arbitrary caller path."""

        target = queued.item_directory.resolve(strict=False)
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise InvalidPendingSnapshotError("queue_item_outside_root") from error
        manifest = target / _MANIFEST_FILE
        if not manifest.is_file():
            raise InvalidPendingSnapshotError("queue_item_missing")
        shutil.rmtree(target)
        LOGGER.info(
            "snapshot_queue_acknowledged workspace_id=%s snapshot_id=%s sequence=%d",
            queued.workspace_id,
            queued.snapshot_id,
            queued.sequence,
        )

    def _next_sequence(self) -> int:
        highest = 0
        for item in self.pending():
            highest = max(highest, item.sequence)
        path = self._root / _SEQUENCE_FILE
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            saved = value.get("next_sequence") if isinstance(value, dict) else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            saved = None
        if isinstance(saved, int) and saved > highest and saved > 0:
            return saved
        return highest + 1

    def _save_next_sequence(self, value: int) -> None:
        _write_atomic(self._root / _SEQUENCE_FILE, _json_bytes({"version": _QUEUE_VERSION, "next_sequence": value}))

    def _load_item(self, directory: Path) -> QueuedSnapshot:
        try:
            value = json.loads((directory / _MANIFEST_FILE).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidPendingSnapshotError("queue_manifest_unreadable") from error
        if not isinstance(value, dict) or value.get("version") != _QUEUE_VERSION:
            raise InvalidPendingSnapshotError("queue_manifest_invalid")
        sequence = value.get("sequence")
        workspace_id = value.get("workspace_id")
        snapshot_id = value.get("snapshot_id")
        commit_delivery_id = value.get("commit_delivery_id")
        entries = value.get("files")
        if not isinstance(sequence, int) or sequence < 1 or not isinstance(entries, list):
            raise InvalidPendingSnapshotError("queue_manifest_invalid")
        uploads = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise InvalidPendingSnapshotError("queue_manifest_invalid")
            path = normalize_relative_json_path(entry.get("path", ""))
            delivery_id = entry.get("delivery_id")
            size = entry.get("byte_size")
            digest = entry.get("sha256")
            if not isinstance(delivery_id, str) or not isinstance(size, int) or not isinstance(digest, str):
                raise InvalidPendingSnapshotError("queue_manifest_invalid")
            source = directory / _FILES_DIRECTORY / Path(*PurePosixPath(path).parts)
            try:
                body = source.read_bytes()
            except OSError as error:
                raise InvalidPendingSnapshotError("queue_file_unreadable") from error
            if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
                raise InvalidPendingSnapshotError("queue_file_integrity_invalid")
            uploads.append(SnapshotUpload(path, body, delivery_id))
        error_value = value.get("last_error")
        error_kind = error_value.get("kind") if isinstance(error_value, dict) else None
        error_status = error_value.get("status_code") if isinstance(error_value, dict) else None
        if error_kind is not None and not isinstance(error_kind, str):
            raise InvalidPendingSnapshotError("queue_manifest_invalid")
        if error_status is not None and not isinstance(error_status, int):
            raise InvalidPendingSnapshotError("queue_manifest_invalid")
        return QueuedSnapshot(
            directory,
            sequence,
            _identifier(workspace_id),
            _identifier(snapshot_id),
            _delivery_id(commit_delivery_id),
            tuple(uploads),
            error_kind,
            error_status,
        )


def _identifier(value: object) -> str:
    if not isinstance(value, str) or value in (".", "..") or not value or len(value) > 128 or any(not (character.isalnum() or character in "._-") for character in value):
        raise InvalidPendingSnapshotError("queue_identifier_invalid")
    return value


def _delivery_id(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise InvalidPendingSnapshotError("queue_delivery_invalid")
    return value


def _error_value(error: Optional[SenderError]) -> Optional[dict]:
    if error is None:
        return None
    return {
        "kind": error.kind.value,
        "retryable": error.retryable,
        "operation": error.operation,
        "status_code": error.status_code,
        "relative_path": error.relative_path,
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
