"""Send complete dashboard snapshots to the Android server over verified HTTPS."""

from __future__ import annotations

import hashlib
import http.client
import time
import json
import logging
import socket
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Optional, Tuple
from urllib.parse import quote, urlsplit


LOGGER = logging.getLogger("sender")
DEFAULT_REQUEST_MAX_BYTES = 1024 * 1024
_RESPONSE_MAX_BYTES = 64 * 1024
_IDENTIFIER = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"


class SendErrorKind(str, Enum):
    TLS_CONNECTION = "tls_connection"
    CERTIFICATE_VERIFY = "certificate_verify"
    DNS_OR_CONNECTION = "dns_or_connection"
    CONNECT_TIMEOUT = "connect_timeout"
    READ_TIMEOUT = "read_timeout"
    HTTP_400 = "http_400"
    HTTP_401_403 = "http_401_403"
    HTTP_404 = "http_404"
    HTTP_408 = "http_408"
    HTTP_409 = "http_409"
    HTTP_413 = "http_413"
    HTTP_429 = "http_429"
    HTTP_5XX = "http_5xx"
    HTTP_OTHER = "http_other"
    INVALID_JSON_RESPONSE = "invalid_json_response"
    RESPONSE_TOO_LARGE = "response_too_large"
    DELIVERY_ID_MISMATCH = "delivery_id_mismatch"
    SNAPSHOT_ID_MISMATCH = "snapshot_id_mismatch"
    RESPONSE_STATUS_INVALID = "response_status_invalid"
    REQUEST_TOO_LARGE = "request_too_large"
    INVALID_INPUT = "invalid_input"


class SenderError(RuntimeError):
    """A safe, structured send failure suitable for future queue handling."""

    def __init__(
        self,
        kind: SendErrorKind,
        *,
        retryable: bool,
        operation: str,
        status_code: Optional[int] = None,
        relative_path: Optional[str] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(kind.value)
        self.kind = kind
        self.retryable = retryable
        self.operation = operation
        self.status_code = status_code
        self.relative_path = relative_path
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class SnapshotUpload:
    relative_json_path: str
    body: bytes
    delivery_id: str


@dataclass(frozen=True)
class StoredFile:
    relative_json_path: str
    delivery_id: str
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class SnapshotSendResult:
    workspace_id: str
    snapshot_id: str
    files: Tuple[StoredFile, ...]
    commit_delivery_id: str


@dataclass(frozen=True)
class SnapshotRetryResult:
    result: SnapshotSendResult
    attempts: int
    retry_delays_seconds: Tuple[float, ...]


def new_delivery_id() -> str:
    """Create a delivery UUID to persist with a future retry queue item."""

    return str(uuid.uuid4())


def read_bearer_token(token_file: Path) -> str:
    """Read one non-empty token line without exposing its value in errors."""

    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise SenderError(
            SendErrorKind.INVALID_INPUT, retryable=False, operation="token"
        ) from error
    if not token or "\n" in token or "\r" in token:
        raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="token")
    return token


def normalize_relative_json_path(value: str | Path) -> str:
    """Return a safe POSIX JSON path; accept Windows separators only as separators."""

    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or len(raw) >= 2 and raw[1] == ":"
        or path.is_absolute()
        or path.suffix != ".json"
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="path")
    return "/".join(path.parts)


def prepare_snapshot_uploads(
    workspace_directory: Path, relative_json_paths: Iterable[str | Path]
) -> Tuple[SnapshotUpload, ...]:
    """Read approved local JSON files in stable order and assign delivery IDs once."""

    root = Path(workspace_directory).resolve(strict=True)
    uploads = []
    paths = sorted({normalize_relative_json_path(value) for value in relative_json_paths})
    for relative_path in paths:
        target = root.joinpath(*PurePosixPath(relative_path).parts).resolve(strict=True)
        try:
            target.relative_to(root)
        except ValueError as error:
            raise SenderError(
                SendErrorKind.INVALID_INPUT, retryable=False, operation="path", relative_path=relative_path
            ) from error
        uploads.append(SnapshotUpload(relative_path, target.read_bytes(), new_delivery_id()))
    return tuple(uploads)


def build_ssl_context(ca_file: Path) -> ssl.SSLContext:
    """Create the required CA-pinned, hostname-verifying client TLS context."""

    try:
        context = ssl.create_default_context(
            purpose=ssl.Purpose.SERVER_AUTH, cafile=str(Path(ca_file))
        )
    except (OSError, ssl.SSLError) as error:
        raise SenderError(
            SendErrorKind.INVALID_INPUT, retryable=False, operation="tls_setup"
        ) from error
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="tls_setup")
    return context


class HttpsSnapshotSender:
    """POST snapshot files then commit the snapshot only after every file is stored."""

    def __init__(
        self,
        base_url: str,
        token: str,
        ca_file: Path,
        *,
        timeout_seconds: float = 10,
        request_max_bytes: int = DEFAULT_REQUEST_MAX_BYTES,
        max_attempts: int = 5,
        backoff_initial_seconds: float = 1,
        backoff_max_seconds: float = 16,
        sleep: Callable[[float], None] = time.sleep,
        connection_factory: Callable[..., http.client.HTTPSConnection] = http.client.HTTPSConnection,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="configuration")
        if not token or "\r" in token or "\n" in token:
            raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="configuration")
        if (
            timeout_seconds <= 0
            or request_max_bytes <= 0
            or not 1 <= max_attempts <= 10
            or not 1 <= backoff_initial_seconds <= 60
            or not backoff_initial_seconds <= backoff_max_seconds <= 300
        ):
            raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="configuration")
        self._host = parsed.hostname
        self._port = parsed.port or 443
        self._prefix = parsed.path.rstrip("/")
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._request_max_bytes = request_max_bytes
        self._max_attempts = max_attempts
        self._backoff_initial_seconds = backoff_initial_seconds
        self._backoff_max_seconds = backoff_max_seconds
        self._sleep = sleep
        self._connection_factory = connection_factory
        self._ssl_context = ssl_context if ssl_context is not None else build_ssl_context(ca_file)

    def send_snapshot(
        self,
        workspace_id: str,
        snapshot_id: str,
        uploads: Iterable[SnapshotUpload],
        *,
        commit_delivery_id: str,
    ) -> SnapshotSendResult:
        """Store every file, then explicitly commit the complete staging snapshot."""

        _validate_identifier(workspace_id)
        _validate_identifier(snapshot_id)
        _validate_delivery_id(commit_delivery_id)
        ordered = tuple(sorted(uploads, key=lambda item: normalize_relative_json_path(item.relative_json_path)))
        if not ordered:
            raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="snapshot")
        seen = set()
        stored = []
        for upload in ordered:
            relative_path = normalize_relative_json_path(upload.relative_json_path)
            if relative_path in seen:
                raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="snapshot", relative_path=relative_path)
            seen.add(relative_path)
            _validate_delivery_id(upload.delivery_id)
            if not isinstance(upload.body, bytes) or len(upload.body) > self._request_max_bytes:
                raise SenderError(SendErrorKind.REQUEST_TOO_LARGE, retryable=False, operation="file", relative_path=relative_path)
            response = self._post(
                self._file_path(workspace_id, snapshot_id, relative_path),
                upload.body,
                upload.delivery_id,
                "file",
                relative_path,
            )
            self._validate_response(response, "stored", upload.delivery_id, snapshot_id, "file", relative_path)
            stored.append(StoredFile(relative_path, upload.delivery_id, len(upload.body), hashlib.sha256(upload.body).hexdigest()))
        commit_body = json.dumps(
            {"files": [{"path": item.relative_json_path, "sha256": item.sha256, "byte_size": item.byte_size} for item in stored]},
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        if len(commit_body) > self._request_max_bytes:
            raise SenderError(SendErrorKind.REQUEST_TOO_LARGE, retryable=False, operation="commit")
        response = self._post(
            self._commit_path(workspace_id, snapshot_id), commit_body, commit_delivery_id, "commit", None
        )
        self._validate_response(response, "committed", commit_delivery_id, snapshot_id, "commit", None)
        LOGGER.info("snapshot_committed workspace_id=%s snapshot_id=%s files=%d", workspace_id, snapshot_id, len(stored))
        return SnapshotSendResult(workspace_id, snapshot_id, tuple(stored), commit_delivery_id)

    def send_snapshot_with_retry(
        self,
        workspace_id: str,
        snapshot_id: str,
        uploads: Iterable[SnapshotUpload],
        *,
        commit_delivery_id: str,
    ) -> SnapshotRetryResult:
        """Retry only transient failures without changing any delivery identifier."""

        stable_uploads = tuple(uploads)
        delays = []
        for attempt in range(1, self._max_attempts + 1):
            try:
                result = self.send_snapshot(
                    workspace_id,
                    snapshot_id,
                    stable_uploads,
                    commit_delivery_id=commit_delivery_id,
                )
                return SnapshotRetryResult(result, attempt, tuple(delays))
            except SenderError as error:
                if not error.retryable or attempt == self._max_attempts:
                    LOGGER.warning(
                        "snapshot_send_failed workspace_id=%s snapshot_id=%s operation=%s kind=%s attempts=%d retryable=%s",
                        workspace_id,
                        snapshot_id,
                        error.operation,
                        error.kind.value,
                        attempt,
                        error.retryable,
                    )
                    raise
                delay = (
                    error.retry_after_seconds
                    if error.retry_after_seconds is not None
                    else min(
                        self._backoff_initial_seconds * (2 ** (attempt - 1)),
                        self._backoff_max_seconds,
                    )
                )
                delays.append(delay)
                LOGGER.warning(
                    "snapshot_send_retry workspace_id=%s snapshot_id=%s operation=%s kind=%s attempt=%d delay_seconds=%s",
                    workspace_id,
                    snapshot_id,
                    error.operation,
                    error.kind.value,
                    attempt,
                    delay,
                )
                self._sleep(delay)

    def _file_path(self, workspace_id: str, snapshot_id: str, relative_path: str) -> str:
        segments = ["api", "v1", "snapshots", workspace_id, snapshot_id, *relative_path.split("/")]
        return self._url_path(segments)

    def _commit_path(self, workspace_id: str, snapshot_id: str) -> str:
        return self._url_path(["api", "v1", "snapshots", workspace_id, snapshot_id, "commit"])

    def _url_path(self, segments: Iterable[str]) -> str:
        suffix = "/".join(quote(segment, safe="") for segment in segments)
        return (self._prefix if self._prefix else "") + "/" + suffix

    def _post(self, path: str, body: bytes, delivery_id: str, operation: str, relative_path: Optional[str]) -> Tuple[int, bytes]:
        headers = {
            "Authorization": "Bearer " + self._token,
            "X-Delivery-Id": delivery_id,
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        }
        connection = None
        try:
            connection = self._connection_factory(self._host, self._port, context=self._ssl_context, timeout=self._timeout_seconds)
            connection.request("POST", path, body=body, headers=headers)
        except ssl.SSLCertVerificationError as error:
            raise SenderError(SendErrorKind.CERTIFICATE_VERIFY, retryable=False, operation=operation, relative_path=relative_path) from error
        except ssl.SSLError as error:
            raise SenderError(SendErrorKind.TLS_CONNECTION, retryable=False, operation=operation, relative_path=relative_path) from error
        except socket.timeout as error:
            raise SenderError(SendErrorKind.CONNECT_TIMEOUT, retryable=True, operation=operation, relative_path=relative_path) from error
        except (socket.gaierror, OSError, http.client.HTTPException) as error:
            raise SenderError(SendErrorKind.DNS_OR_CONNECTION, retryable=True, operation=operation, relative_path=relative_path) from error
        try:
            response = connection.getresponse()
            payload = response.read(_RESPONSE_MAX_BYTES + 1)
        except socket.timeout as error:
            raise SenderError(SendErrorKind.READ_TIMEOUT, retryable=True, operation=operation, relative_path=relative_path) from error
        except (OSError, http.client.HTTPException) as error:
            raise SenderError(SendErrorKind.DNS_OR_CONNECTION, retryable=True, operation=operation, relative_path=relative_path) from error
        finally:
            if connection is not None:
                connection.close()
        if len(payload) > _RESPONSE_MAX_BYTES:
            raise SenderError(SendErrorKind.RESPONSE_TOO_LARGE, retryable=False, operation=operation, status_code=response.status, relative_path=relative_path)
        if not 200 <= response.status < 300:
            retry_after = _retry_after_seconds(response.getheader("Retry-After"))
            raise _http_error(response.status, operation, relative_path, retry_after)
        return response.status, payload

    def _validate_response(self, response: Tuple[int, bytes], expected_status: str, delivery_id: str, snapshot_id: str, operation: str, relative_path: Optional[str]) -> None:
        status_code, payload = response
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SenderError(SendErrorKind.INVALID_JSON_RESPONSE, retryable=False, operation=operation, status_code=status_code, relative_path=relative_path) from error
        if not isinstance(value, dict):
            raise SenderError(SendErrorKind.INVALID_JSON_RESPONSE, retryable=False, operation=operation, status_code=status_code, relative_path=relative_path)
        if value.get("delivery_id") != delivery_id:
            raise SenderError(SendErrorKind.DELIVERY_ID_MISMATCH, retryable=False, operation=operation, status_code=status_code, relative_path=relative_path)
        if value.get("snapshot_id") != snapshot_id:
            raise SenderError(SendErrorKind.SNAPSHOT_ID_MISMATCH, retryable=False, operation=operation, status_code=status_code, relative_path=relative_path)
        if value.get("status") != expected_status:
            raise SenderError(SendErrorKind.RESPONSE_STATUS_INVALID, retryable=False, operation=operation, status_code=status_code, relative_path=relative_path)


def _validate_identifier(value: str) -> None:
    if not value or len(value) > 128 or any(character not in _IDENTIFIER for character in value) or "/" in value or "\\" in value:
        raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="identifier")


def _validate_delivery_id(value: str) -> None:
    try:
        if str(uuid.UUID(value)) != value.lower():
            raise ValueError
    except (ValueError, AttributeError) as error:
        raise SenderError(SendErrorKind.INVALID_INPUT, retryable=False, operation="delivery") from error


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        return max(0.0, float(int(value)))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())


def _http_error(status_code: int, operation: str, relative_path: Optional[str], retry_after_seconds: Optional[float] = None) -> SenderError:
    if status_code in (401, 403):
        kind, retryable = SendErrorKind.HTTP_401_403, False
    elif status_code == 404:
        kind, retryable = SendErrorKind.HTTP_404, False
    elif status_code == 408:
        kind, retryable = SendErrorKind.HTTP_408, True
    elif status_code == 409:
        kind, retryable = SendErrorKind.HTTP_409, False
    elif status_code == 413:
        kind, retryable = SendErrorKind.HTTP_413, False
    elif status_code == 429:
        kind, retryable = SendErrorKind.HTTP_429, True
    elif 500 <= status_code <= 599:
        kind, retryable = SendErrorKind.HTTP_5XX, True
    elif status_code == 400:
        kind, retryable = SendErrorKind.HTTP_400, False
    else:
        kind, retryable = SendErrorKind.HTTP_OTHER, False
    return SenderError(kind, retryable=retryable, operation=operation, status_code=status_code, relative_path=relative_path, retry_after_seconds=retry_after_seconds)
