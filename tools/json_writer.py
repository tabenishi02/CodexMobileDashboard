"""Persist generated dashboard JSON as deterministic UTF-8 files."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, List, Tuple

from tools.json_converter import JsonSnapshot, encode_json


LOGGER = logging.getLogger("converter")
_WORKSPACE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class UnsafeJsonPathError(ValueError):
    """Raised when a snapshot path could escape its workspace directory."""


@dataclass(frozen=True)
class SavedJsonFile:
    path: str
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class JsonSaveResult:
    workspace_directory: Path
    files: Tuple[SavedJsonFile, ...]


def save_json_snapshot(snapshot: JsonSnapshot, output_directory: Path) -> JsonSaveResult:
    """Write one workspace snapshot as UTF-8 without performing atomic replacement."""

    workspace_id = _workspace_id(snapshot)
    _validate_document_identity(snapshot)
    root = output_directory.expanduser().resolve(strict=False)
    workspace_directory = (root / workspace_id).resolve(strict=False)
    _require_within_root(root, workspace_directory)
    targets = _validated_targets(snapshot, workspace_directory)
    workspace_directory.mkdir(parents=True, exist_ok=True)
    saved: List[SavedJsonFile] = []
    for relative_path, target in targets:
        data = encode_json(snapshot.documents[relative_path])
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as stream:
            stream.write(data)
        saved.append(
            SavedJsonFile(
                path=relative_path,
                byte_size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )

    LOGGER.info(
        "表示用JSONをUTF-8で保存: workspace_id=%s files=%d",
        workspace_id,
        len(saved),
    )
    return JsonSaveResult(workspace_directory, tuple(saved))


def _workspace_id(snapshot: JsonSnapshot) -> str:
    metadata = snapshot.documents.get("metadata.json")
    if metadata is None:
        raise ValueError("metadata_document_missing")
    workspace_id = metadata.get("workspace_id")
    if not isinstance(workspace_id, str) or not _WORKSPACE_ID.fullmatch(workspace_id):
        raise UnsafeJsonPathError("unsafe_workspace_id")
    if workspace_id in (".", ".."):
        raise UnsafeJsonPathError("unsafe_workspace_id")
    return workspace_id


def _validate_document_identity(snapshot: JsonSnapshot) -> None:
    metadata = snapshot.documents["metadata.json"]
    fields = (
        "schema_version",
        "snapshot_id",
        "generated_at",
        "workspace_id",
        "session_id",
    )
    expected = {field: metadata.get(field) for field in fields}
    if any(not isinstance(value, str) or not value for value in expected.values()):
        raise ValueError("metadata_identity_missing")
    for document in snapshot.documents.values():
        if any(document.get(field) != expected[field] for field in fields):
            raise ValueError("snapshot_identity_mismatch")


def _validated_targets(
    snapshot: JsonSnapshot, workspace_directory: Path
) -> List[Tuple[str, Path]]:
    targets: List[Tuple[str, Path]] = []
    normalized_targets: Dict[str, str] = {}
    for relative_path in snapshot.documents:
        path = PurePosixPath(relative_path)
        if (
            not relative_path
            or "\\" in relative_path
            or path.is_absolute()
            or any(part in ("", ".", "..") for part in path.parts)
            or path.suffix != ".json"
        ):
            raise UnsafeJsonPathError("unsafe_document_path")
        target = workspace_directory.joinpath(*path.parts).resolve(strict=False)
        _require_within_root(workspace_directory, target)
        normalized = os.path.normcase(str(target))
        if normalized in normalized_targets:
            raise UnsafeJsonPathError("duplicate_document_path")
        normalized_targets[normalized] = relative_path
        targets.append((relative_path, target))
    return targets


def _require_within_root(root: Path, target: Path) -> None:
    try:
        target.relative_to(root)
    except ValueError as error:
        raise UnsafeJsonPathError("path_outside_output_directory") from error
