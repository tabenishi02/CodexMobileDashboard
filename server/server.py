"""Minimal Python 3.10-compatible server foundation for Termux."""

from __future__ import annotations

import argparse
import configparser
import json
import logging
from logging.handlers import TimedRotatingFileHandler
import os
import hashlib
import hmac
import ssl
import shutil
import tempfile
import time
import threading
import uuid
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Sequence, Type


_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_JSON_PATH_COMPONENT_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

def configure_server_logging(log_directory: Path) -> tuple[logging.Logger, logging.Logger]:
    """Create separate UTF-8 access and error logs without sensitive request data."""
    log_directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s server %(message)s")
    result: list[logging.Logger] = []
    for name, filename in (("access", "server.access.log"), ("error", "server.error.log")):
        logger = logging.getLogger("codex_mobile_dashboard.server." + name + "." + str(log_directory.resolve()))
        logger.setLevel(logging.INFO)
        logger.propagate = False
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        handler = TimedRotatingFileHandler(
            log_directory / filename, when="midnight", interval=1, backupCount=7, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        result.append(logger)
    return result[0], result[1]


def _safe_log_path(path: str) -> str:
    path = path.split("?", 1)[0]
    if path.startswith("/api/v1/snapshots/"):
        return "/api/v1/snapshots"
    if path.startswith("/data/"):
        return "/data"
    return path if path in ("/", "/health") else "/unknown"


def parse_snapshot_json_request_path(request_path: str) -> tuple[str, str, str]:
    """Validate and split a Snapshot JSON POST path after URL decoding."""
    if "?" in request_path:
        raise ValueError("snapshot_request_path_invalid")
    parts = request_path.split("/")
    if len(parts) < 7 or parts[:4] != ["", "api", "v1", "snapshots"]:
        raise ValueError("snapshot_request_path_invalid")

    def decode_identifier(value: str) -> str:
        decoded = unquote(value, errors="strict")
        if not _IDENTIFIER_PATTERN.fullmatch(decoded) or decoded in (".", ".."):
            raise ValueError("snapshot_request_path_invalid")
        return decoded

    try:
        workspace_id = decode_identifier(parts[4])
        snapshot_id = decode_identifier(parts[5])
        relative_parts = [unquote(value, errors="strict") for value in parts[6:]]
    except UnicodeDecodeError as error:
        raise ValueError("snapshot_request_path_invalid") from error
    if (
        not relative_parts
        or any(
            not _JSON_PATH_COMPONENT_PATTERN.fullmatch(value)
            or value in (".", "..")
            for value in relative_parts
        )
        or not relative_parts[-1].endswith(".json")
    ):
        raise ValueError("snapshot_request_path_invalid")
    return workspace_id, snapshot_id, "/".join(relative_parts)

_SUPPORTED_SCHEMA_VERSION_PATTERN = re.compile(r"1\.[0-9]+")
_COMMON_JSON_STRING_FIELDS = (
    "schema_version",
    "data_type",
    "snapshot_id",
    "generated_at",
    "workspace_id",
    "session_id",
)


class SnapshotJsonValidationError(ValueError):
    """A safe validation error that does not include request content."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotJsonValidationError("snapshot_json_invalid")
        result[key] = value
    return result


def validate_snapshot_json_body(
    body: bytes, expected_workspace_id: str, expected_snapshot_id: str
) -> dict[str, object]:
    """Validate one UTF-8 display JSON document against its Snapshot URL."""
    try:
        text = body.decode("utf-8")
        document = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise SnapshotJsonValidationError("snapshot_json_invalid") from error
    if not isinstance(document, dict):
        raise SnapshotJsonValidationError("snapshot_json_invalid")
    for field in _COMMON_JSON_STRING_FIELDS:
        if not isinstance(document.get(field), str) or not document[field]:
            raise SnapshotJsonValidationError("snapshot_json_invalid")
    if not _SUPPORTED_SCHEMA_VERSION_PATTERN.fullmatch(document["schema_version"]):
        raise SnapshotJsonValidationError("snapshot_json_schema_unsupported")
    if not _IDENTIFIER_PATTERN.fullmatch(document["data_type"]):
        raise SnapshotJsonValidationError("snapshot_json_invalid")
    if not isinstance(document.get("warnings"), list):
        raise SnapshotJsonValidationError("snapshot_json_invalid")
    if document["workspace_id"] != expected_workspace_id:
        raise SnapshotJsonValidationError("snapshot_workspace_mismatch")
    if document["snapshot_id"] != expected_snapshot_id:
        raise SnapshotJsonValidationError("snapshot_id_mismatch")
    return document

MAX_REQUEST_BODY_BYTES = 1024 * 1024


class SnapshotCommitConflictError(ValueError):
    """A staged Snapshot cannot satisfy the commit manifest."""

class RequestBodyLengthError(ValueError):
    """A safe HTTP status for a body rejected before reading it."""

    def __init__(self, status: int) -> None:
        super().__init__("request_body_length_invalid")
        self.status = status


def validate_request_content_length(headers: object) -> int:
    """Require one decimal Content-Length and reject bodies over 1 MiB."""
    if getattr(headers, "get")("Transfer-Encoding"):
        raise RequestBodyLengthError(400)
    values = getattr(headers, "get_all")("Content-Length")
    if not values:
        raise RequestBodyLengthError(411)
    if len(values) != 1 or not re.fullmatch(r"[0-9]+", values[0]):
        raise RequestBodyLengthError(400)
    length = int(values[0])
    if length > MAX_REQUEST_BODY_BYTES:
        raise RequestBodyLengthError(413)
    return length

class ServerConfigurationError(ValueError):
    """Safe error whose message never contains a configured Token."""


def validate_delivery_id(headers: object) -> str:
    """Require one canonical UUID delivery identifier without logging it."""
    values = getattr(headers, "get_all")("X-Delivery-Id")
    if not values or len(values) != 1:
        raise ValueError("delivery_id_invalid")
    value = values[0]
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as error:
        raise ValueError("delivery_id_invalid") from error
    if str(parsed) != value:
        raise ValueError("delivery_id_invalid")
    return value

def parse_snapshot_commit_request_path(request_path: str) -> tuple[str, str]:
    """Validate and split an explicit Snapshot commit endpoint path."""
    if "?" in request_path:
        raise ValueError("snapshot_commit_path_invalid")
    parts = request_path.split("/")
    if len(parts) != 7 or parts[:4] != ["", "api", "v1", "snapshots"] or parts[6] != "commit":
        raise ValueError("snapshot_commit_path_invalid")
    try:
        workspace_id = unquote(parts[4], errors="strict")
        snapshot_id = unquote(parts[5], errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("snapshot_commit_path_invalid") from error
    if any(not _IDENTIFIER_PATTERN.fullmatch(value) or value in (".", "..") for value in (workspace_id, snapshot_id)):
        raise ValueError("snapshot_commit_path_invalid")
    return workspace_id, snapshot_id


def validate_snapshot_commit_body(body: bytes) -> tuple[dict[str, object], ...]:
    """Validate the sender's complete, path-sorted commit manifest."""
    try:
        document = json.loads(body.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise SnapshotJsonValidationError("snapshot_commit_invalid") from error
    if not isinstance(document, dict) or set(document) != {"files"} or not isinstance(document["files"], list) or not document["files"]:
        raise SnapshotJsonValidationError("snapshot_commit_invalid")
    files = document["files"]
    normalized_paths: list[str] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "byte_size"}:
            raise SnapshotJsonValidationError("snapshot_commit_invalid")
        path = entry["path"]
        digest = entry["sha256"]
        size = entry["byte_size"]
        if (not isinstance(path, str) or not isinstance(digest, str) or isinstance(size, bool) or not isinstance(size, int) or size < 0):
            raise SnapshotJsonValidationError("snapshot_commit_invalid")
        try:
            _, _, normalized_path = parse_snapshot_json_request_path("/api/v1/snapshots/a/b/" + path)
        except ValueError as error:
            raise SnapshotJsonValidationError("snapshot_commit_invalid") from error
        if normalized_path != path or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SnapshotJsonValidationError("snapshot_commit_invalid")
        normalized_paths.append(path)
    if normalized_paths != sorted(normalized_paths) or len(set(normalized_paths)) != len(normalized_paths):
        raise SnapshotJsonValidationError("snapshot_commit_invalid")
    return tuple(files)

class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Expose only a non-sensitive health endpoint until API tasks are added."""

    server_version = "CodexMobileDashboard/0.1"
    def handle_one_request(self) -> None:
        started = time.monotonic()
        self._response_status: Optional[int] = None
        self._authentication = "not_required"
        try:
            super().handle_one_request()
        finally:
            if getattr(self, "command", None):
                length = self.headers.get("Content-Length", "-") if hasattr(self, "headers") else "-"
                self.server.access_logger.info(
                    "http_access method=%s path=%s status=%s duration_ms=%d request_bytes=%s authentication=%s",
                    self.command, _safe_log_path(self.path), self._response_status or 0,
                    int((time.monotonic() - started) * 1000), length, self._authentication,
                )

    def send_response(self, code: int, message: Optional[str] = None) -> None:
        self._response_status = code
        super().send_response(code, message)


    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/data/"):
            self._serve_json()
            return
        if self.path == "/":
            self._serve_index()
            return
        if self.path != "/health":
            self.send_error(404)
            return
        body = b'{"status":"ok"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        try:
            body_length = validate_request_content_length(self.headers)
        except RequestBodyLengthError as error:
            self.send_response(error.status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if not self._api_token_is_valid():
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            delivery_id = validate_delivery_id(self.headers)
        except ValueError:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            workspace_id, snapshot_id = parse_snapshot_commit_request_path(self.path)
        except ValueError:
            workspace_id = snapshot_id = None
        if workspace_id is not None and snapshot_id is not None:
            self._handle_snapshot_commit(workspace_id, snapshot_id, delivery_id, body_length)
            return
        try:
            workspace_id, snapshot_id, relative_json_path = parse_snapshot_json_request_path(self.path)
        except ValueError:
            self.send_error(404)
            return
        body = self.rfile.read(body_length)
        if len(body) != body_length:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            validate_snapshot_json_body(body, workspace_id, snapshot_id)
        except SnapshotJsonValidationError:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        staging_directory = getattr(self.server, "staging_directory", None)
        if not isinstance(staging_directory, Path):
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            request_receipt = {
                "workspace_id": workspace_id,
                "snapshot_id": snapshot_id,
                "relative_json_path": relative_json_path,
                "body_sha256": hashlib.sha256(body).hexdigest(),
            }
            with self.server.delivery_lock:
                receipt = delivery_receipt(str(staging_directory), delivery_id)
                if receipt is None:
                    store_snapshot_json(
                        str(staging_directory), workspace_id, snapshot_id, relative_json_path, body
                    )
                    store_delivery_receipt(
                        str(staging_directory), delivery_id, request_receipt
                    )
                elif receipt != request_receipt:
                    self.send_response(409)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
        except (OSError, ValueError):
            self.server.error_logger.error("server_error event=snapshot_store_failed")
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        response_body = json.dumps(
            {"status": "stored", "delivery_id": delivery_id, "snapshot_id": snapshot_id},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)
    def _handle_snapshot_commit(
        self, workspace_id: str, snapshot_id: str, delivery_id: str, body_length: int
    ) -> None:
        body = self.rfile.read(body_length)
        if len(body) != body_length:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            manifest_files = validate_snapshot_commit_body(body)
        except SnapshotJsonValidationError:
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        staging_directory = getattr(self.server, "staging_directory", None)
        if not isinstance(staging_directory, Path):
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        request_receipt = {
            "workspace_id": workspace_id,
            "snapshot_id": snapshot_id,
            "body_sha256": hashlib.sha256(body).hexdigest(),
        }
        try:
            with self.server.delivery_lock:
                receipt = commit_receipt(str(staging_directory), delivery_id)
                if receipt is not None and receipt != request_receipt:
                    self.send_response(409)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                publish_snapshot(
                    self.server.public_directory, str(staging_directory), workspace_id,
                    snapshot_id, manifest_files,
                )
                if receipt is None:
                    store_commit_receipt(str(staging_directory), delivery_id, request_receipt)
        except SnapshotCommitConflictError:
            self.send_response(409)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        except (OSError, ValueError):
            self.server.error_logger.error("server_error event=snapshot_commit_failed")
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        response_body = json.dumps(
            {"status": "committed", "delivery_id": delivery_id, "snapshot_id": snapshot_id},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)
    def _api_token_is_valid(self) -> bool:
        expected = getattr(self.server, "bearer_token", None)
        authorization = self.headers.get("Authorization")
        if not isinstance(expected, str) or not expected:
            self._authentication = "failed"
            return False
        if not authorization or not authorization.startswith("Bearer "):
            self._authentication = "failed"
            return False
        supplied = authorization[len("Bearer "):]
        valid = hmac.compare_digest(supplied, expected)
        self._authentication = "success" if valid else "failed"
        return valid
    def _serve_json(self) -> None:
        parts = self.path.split("?", 1)[0].split("/")[2:]
        if len(parts) < 2 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", parts[0]):
            self.send_error(404)
            return
        try:
            current = load_current_snapshot_id(self.server.public_directory, parts[0])
            target = safe_static_path(
                self.server.public_directory / parts[0] / "snapshots" / current,
                "/".join(parts[1:]),
            )
        except (OSError, ValueError):
            self.send_error(404)
            return
        if target.suffix != ".json" or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        etag = "\"" + hashlib.sha256(body).hexdigest() + "\""
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_index(self) -> None:
        target = self.server.static_directory / "index.html"
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type_for(target))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Avoid logging request bodies; detailed logging is added later."""
        return


def load_server_settings(config_file: str) -> dict:
    """Read UTF-8 server settings and a Bearer Token kept outside the repository."""
    try:
        parser = configparser.ConfigParser(interpolation=None)
        with Path(config_file).open("r", encoding="utf-8") as stream:
            parser.read_file(stream)

        def read_path(section: str, option: str) -> Path:
            value = parser.get(section, option)
            return Path(os.path.expandvars(value)).expanduser()

        token_file = read_path("auth", "token_file")
        token = token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise ServerConfigurationError("token_file_invalid")
        port = parser.getint("server", "port")
        if not 1 <= port <= 65535:
            raise ServerConfigurationError("port_invalid")
        return {
            "host": parser.get("server", "host"),
            "port": port,
            "certificate_file": read_path("server", "certificate_file"),
            "private_key_file": read_path("server", "private_key_file"),
            "static_directory": read_path("server", "static_directory"),
            "public_directory": read_path("server", "public_directory"),
            "staging_directory": read_path("server", "staging_directory"),
            "log_directory": (read_path("logging", "directory") if parser.has_option("logging", "directory") else Path(config_file).parent / "logs"),
            "token": token,
            "token_file": token_file,
        }
    except ServerConfigurationError:
        raise
    except (OSError, UnicodeError, ValueError, configparser.Error) as error:
        raise ServerConfigurationError("configuration_invalid") from error
def _safe_staging_json_target(
    staging_directory: str, workspace_id: str, snapshot_id: str, relative_json_path: str
) -> Path:
    if (
        not _IDENTIFIER_PATTERN.fullmatch(workspace_id)
        or not _IDENTIFIER_PATTERN.fullmatch(snapshot_id)
        or workspace_id in (".", "..")
        or snapshot_id in (".", "..")
    ):
        raise ValueError("staging_target_invalid")
    relative_parts = relative_json_path.split("/")
    if (
        not relative_parts
        or any(
            not _JSON_PATH_COMPONENT_PATTERN.fullmatch(value)
            or value in (".", "..")
            for value in relative_parts
        )
        or not relative_parts[-1].endswith(".json")
    ):
        raise ValueError("staging_target_invalid")
    root = Path(staging_directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("staging_directory_invalid")
    target = (root / workspace_id / snapshot_id).joinpath(*relative_parts).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError("staging_target_invalid") from error
    return target


def store_snapshot_json(
    staging_directory: str, workspace_id: str, snapshot_id: str,
    relative_json_path: str, body: bytes,
) -> Path:
    """Atomically store validated JSON bytes below an unpublicized staging root."""
    target = _safe_staging_json_target(
        staging_directory, workspace_id, snapshot_id, relative_json_path
    )
    _atomic_write_bytes(target, body)
    return target


def _atomic_write_bytes(target: Path, body: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=target.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _delivery_receipt_target(staging_directory: str, delivery_id: str) -> Path:
    try:
        if str(uuid.UUID(delivery_id)) != delivery_id:
            raise ValueError("delivery_receipt_invalid")
    except (AttributeError, ValueError) as error:
        raise ValueError("delivery_receipt_invalid") from error
    root = Path(staging_directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("staging_directory_invalid")
    return root / ".deliveries" / (delivery_id + ".json")


def delivery_receipt(staging_directory: str, delivery_id: str) -> Optional[dict[str, str]]:
    """Return a persisted idempotency receipt, or None when it has not been seen."""
    target = _delivery_receipt_target(staging_directory, delivery_id)
    if not target.is_file():
        return None
    try:
        receipt = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("delivery_receipt_invalid") from error
    required = {"workspace_id", "snapshot_id", "relative_json_path", "body_sha256"}
    if (not isinstance(receipt, dict) or set(receipt) != required or
            any(not isinstance(receipt[key], str) for key in required)):
        raise ValueError("delivery_receipt_invalid")
    return receipt


def store_delivery_receipt(
    staging_directory: str, delivery_id: str, receipt: dict[str, str]
) -> Path:
    """Persist an opaque Delivery ID receipt after the matching JSON is stored."""
    target = _delivery_receipt_target(staging_directory, delivery_id)
    body = json.dumps(receipt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(target, body)
    return target

def _commit_receipt_target(staging_directory: str, delivery_id: str) -> Path:
    target = _delivery_receipt_target(staging_directory, delivery_id)
    return target.parent.parent / ".commits" / target.name


def commit_receipt(staging_directory: str, delivery_id: str) -> Optional[dict[str, str]]:
    target = _commit_receipt_target(staging_directory, delivery_id)
    if not target.is_file():
        return None
    try:
        receipt = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("commit_receipt_invalid") from error
    required = {"workspace_id", "snapshot_id", "body_sha256"}
    if (not isinstance(receipt, dict) or set(receipt) != required or
            any(not isinstance(receipt[key], str) for key in required)):
        raise ValueError("commit_receipt_invalid")
    return receipt


def store_commit_receipt(
    staging_directory: str, delivery_id: str, receipt: dict[str, str]
) -> Path:
    target = _commit_receipt_target(staging_directory, delivery_id)
    body = json.dumps(receipt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(target, body)
    return target

def _safe_snapshot_directory(root: Path, workspace_id: str, snapshot_id: str) -> Path:
    if any(not _IDENTIFIER_PATTERN.fullmatch(value) or value in (".", "..") for value in (workspace_id, snapshot_id)):
        raise ValueError("snapshot_directory_invalid")
    target = (root / workspace_id / snapshot_id).resolve(strict=False)
    try:
        target.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise ValueError("snapshot_directory_invalid") from error
    return target


def _validated_staging_files(staging_directory: str, workspace_id: str, snapshot_id: str, manifest_files: tuple[dict[str, object], ...]) -> list[tuple[Path, str]]:
    source_root = _safe_snapshot_directory(Path(staging_directory), workspace_id, snapshot_id)
    if not source_root.is_dir():
        raise SnapshotCommitConflictError("snapshot_staging_missing")
    manifest = {str(entry["path"]): entry for entry in manifest_files}
    actual: dict[str, Path] = {}
    for path in source_root.rglob("*"):
        if path.is_symlink() or (path.is_file() and path.suffix != ".json"):
            raise SnapshotCommitConflictError("snapshot_staging_invalid")
        if path.is_file():
            actual[path.relative_to(source_root).as_posix()] = path
    if set(actual) != set(manifest):
        raise SnapshotCommitConflictError("snapshot_manifest_mismatch")
    verified: list[tuple[Path, str]] = []
    for relative_path, entry in manifest.items():
        body = actual[relative_path].read_bytes()
        if len(body) != entry["byte_size"] or hashlib.sha256(body).hexdigest() != entry["sha256"]:
            raise SnapshotCommitConflictError("snapshot_manifest_mismatch")
        try:
            validate_snapshot_json_body(body, workspace_id, snapshot_id)
        except SnapshotJsonValidationError as error:
            raise SnapshotCommitConflictError("snapshot_staging_invalid") from error
        verified.append((actual[relative_path], relative_path))
    return verified

def load_current_snapshot_id(public_directory: Path, workspace_id: str) -> str:
    workspace_root = (public_directory / workspace_id).resolve(strict=False)
    try:
        workspace_root.relative_to(public_directory.resolve(strict=True))
        current = json.loads((workspace_root / "current.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("current_snapshot_invalid") from error
    if not isinstance(current, dict) or set(current) != {"snapshot_id"}:
        raise ValueError("current_snapshot_invalid")
    snapshot_id = current["snapshot_id"]
    if not isinstance(snapshot_id, str) or not _IDENTIFIER_PATTERN.fullmatch(snapshot_id) or snapshot_id in (".", ".."):
        raise ValueError("current_snapshot_invalid")
    return snapshot_id


def publish_snapshot(public_directory: Path, staging_directory: str, workspace_id: str, snapshot_id: str, manifest_files: tuple[dict[str, object], ...]) -> None:
    """Verify one staged Snapshot, then atomically switch its public current pointer."""
    verified = _validated_staging_files(staging_directory, workspace_id, snapshot_id, manifest_files)
    public_root = public_directory.resolve(strict=True)
    workspace_root = public_root / workspace_id
    snapshots_root = workspace_root / "snapshots"
    snapshots_root.mkdir(parents=True, exist_ok=True)
    destination = snapshots_root / snapshot_id
    if destination.exists():
        if not destination.is_dir():
            raise SnapshotCommitConflictError("public_snapshot_invalid")
        for source, relative_path in verified:
            target = destination.joinpath(*relative_path.split("/"))
            if not target.is_file() or target.read_bytes() != source.read_bytes():
                raise SnapshotCommitConflictError("public_snapshot_conflict")
    else:
        temporary = Path(tempfile.mkdtemp(prefix=snapshot_id + ".", dir=snapshots_root))
        try:
            for source, relative_path in verified:
                target = temporary.joinpath(*relative_path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    _atomic_write_bytes(workspace_root / "current.json", json.dumps({"snapshot_id": snapshot_id}, separators=(",", ":")).encode("utf-8"))

def safe_static_path(root: Path, request_path: str) -> Path:
    decoded = unquote(request_path)
    if not decoded or "\\" in decoded or ":" in decoded:
        raise ValueError("static_path_invalid")
    path = PurePosixPath(decoded.lstrip("/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("static_path_invalid")
    target = root.joinpath(*path.parts).resolve(strict=False)
    try:
        target.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise ValueError("static_path_invalid") from error
    return target

def content_type_for(path: Path) -> str:
    return {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(path.suffix.lower(), "application/octet-stream")

def create_server(host: str = "0.0.0.0", port: int = 8765, static_directory: str = ".", public_directory: str = ".", staging_directory: Optional[str] = None, log_directory: Optional[str] = None) -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("port_invalid")
    root = Path(static_directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("static_directory_invalid")
    public_root = Path(public_directory).resolve(strict=True)
    if not public_root.is_dir():
        raise ValueError("public_directory_invalid")
    staging_root: Optional[Path] = None
    if staging_directory is not None:
        staging_root = Path(staging_directory).resolve(strict=True)
        if not staging_root.is_dir():
            raise ValueError("staging_directory_invalid")
        try:
            staging_root.relative_to(public_root)
        except ValueError:
            try:
                public_root.relative_to(staging_root)
            except ValueError:
                pass
            else:
                raise ValueError("staging_directory_overlaps_public")
        else:
            raise ValueError("staging_directory_overlaps_public")
    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    server.static_directory = root
    server.public_directory = public_root
    server.staging_directory = staging_root
    server.delivery_lock = threading.Lock()
    if log_directory is None:
        server.access_logger = logging.getLogger("codex_mobile_dashboard.server.null.access")
        server.error_logger = logging.getLogger("codex_mobile_dashboard.server.null.error")
        server.access_logger.addHandler(logging.NullHandler())
        server.error_logger.addHandler(logging.NullHandler())
    else:
        server.access_logger, server.error_logger = configure_server_logging(Path(log_directory))
    return server
def create_https_server(certificate_file: str, private_key_file: str, host: str = "0.0.0.0", port: int = 8765, static_directory: str = ".", public_directory: str = ".", staging_directory: Optional[str] = None, log_directory: Optional[str] = None) -> ThreadingHTTPServer:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_file, private_key_file)
    server = create_server(host, port, static_directory, public_directory, staging_directory, log_directory)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Codex Mobile Dashboard server")
    parser.add_argument("--config", help="UTF-8 INI file outside the repository")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--static-dir", default=".")
    parser.add_argument("--public-dir", default=".")
    parser.add_argument("--staging-dir")
    arguments = parser.parse_args(argv)
    if arguments.config:
        try:
            settings = load_server_settings(arguments.config)
        except ServerConfigurationError as error:
            parser.error(str(error))
        server = create_https_server(
            str(settings["certificate_file"]), str(settings["private_key_file"]),
            settings["host"], settings["port"], str(settings["static_directory"]),
            str(settings["public_directory"]), str(settings["staging_directory"]), str(settings["log_directory"]),
        )
        server.bearer_token = settings["token"]
    else:
        if bool(arguments.cert) != bool(arguments.key):
            parser.error("--cert and --key must be specified together")
        server = create_https_server(arguments.cert, arguments.key, arguments.host, arguments.port, arguments.static_dir, arguments.public_dir, arguments.staging_dir) if arguments.cert else create_server(arguments.host, arguments.port, arguments.static_dir, arguments.public_dir, arguments.staging_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
