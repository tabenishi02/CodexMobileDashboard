"""Remove authentication secrets from normalized Codex text in memory."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Iterable, List, Match, Optional, Pattern, Tuple

from tools.record_normalizer import (
    NormalizedContentPart,
    NormalizedRecord,
    NormalizedRedaction,
)


LOGGER = logging.getLogger("converter")

_MARKERS = {
    "api_key": "[REDACTED:API_KEY]",
    "token": "[REDACTED:TOKEN]",
    "password": "[REDACTED:PASSWORD]",
    "private_key": "[REDACTED:PRIVATE_KEY]",
    "url_credential": "[REDACTED:URL_CREDENTIAL]",
    "local_path": "[REDACTED:LOCAL_PATH]",
    "account": "[REDACTED:ACCOUNT]",
}
_EXISTING_MARKER = re.compile(
    r"\[REDACTED:(API_KEY|TOKEN|PASSWORD|PRIVATE_KEY|URL_CREDENTIAL|LOCAL_PATH|ACCOUNT)\]"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
    r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    re.DOTALL,
)
_URL_CREDENTIAL = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)"
    r"(?P<credential>[^\s/@:]+:[^\s/@]+)@",
    re.IGNORECASE,
)
_AUTHORIZATION = re.compile(
    r"(?P<prefix>\b(?:Authorization|Proxy-Authorization)\s*:\s*"
    r"(?:Bearer|Basic|Token)\s+)(?P<secret>[^\s,;]+)",
    re.IGNORECASE,
)
_KNOWN_API_KEY = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}|"
    r"sk_(?:live|test)_[A-Za-z0-9]{16,}|"
    r"AKIA[0-9A-Z]{16}|"
    r"AIza[0-9A-Za-z_-]{35}"
    r")(?![A-Za-z0-9])"
)
_KNOWN_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r")(?![A-Za-z0-9])"
)
_WINDOWS_USER_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]+Users[\\/]+)"
    r"[^\\/\s\"'<>|]+(?:[\\/]+[^\s\"'<>|]*)?"
)
_UNIX_HOME_PATH = re.compile(
    r"(?<![A-Za-z0-9])(?:/(?:home|Users)/[^/\s\"'<>]+|"
    r"/data/data/com\.termux/files/home)(?:/[^\s\"'<>]*)?"
)
_SSH_ACCOUNT = re.compile(
    r"(?P<account>(?<![A-Za-z0-9._-])[A-Za-z_][A-Za-z0-9._-]*)@"
    r"(?=(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9_-]+)(?::|\s))"
)

_SENSITIVE_NAME = (
    r"(?:(?:[A-Za-z][A-Za-z0-9]*_)+)?"
    r"(?:api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token|token|"
    r"password|passwd|pwd|private[_-]?key|secret)"
)
_ASSIGNMENT_QUOTED = re.compile(
    rf"(?P<prefix>(?<![A-Za-z0-9_])(?:\$env:|export\s+)?"
    rf"(?P<name>{_SENSITIVE_NAME})\s*[:=]\s*)"
    r"(?P<quote>[\"'])(?P<secret>.*?)(?P=quote)",
    re.IGNORECASE,
)
_ASSIGNMENT = re.compile(
    rf"(?P<prefix>(?<![A-Za-z0-9_])(?:\$env:|export\s+)?"
    rf"(?P<name>{_SENSITIVE_NAME})\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<secret>[^\s\"',;\]}]+)(?P=quote)",
    re.IGNORECASE,
)
_JSON_ASSIGNMENT = re.compile(
    rf'(?P<prefix>[\"\'](?P<name>{_SENSITIVE_NAME})[\"\']\s*:\s*)'
    r'(?P<quote>[\"\'])(?P<secret>.*?)(?P=quote)',
    re.IGNORECASE,
)
_COMMAND_OPTION_QUOTED = re.compile(
    rf"(?P<prefix>--(?P<name>{_SENSITIVE_NAME})(?:=|\s+))"
    r"(?P<quote>[\"'])(?P<secret>.*?)(?P=quote)",
    re.IGNORECASE,
)
_COMMAND_OPTION = re.compile(
    rf"(?P<prefix>--(?P<name>{_SENSITIVE_NAME})(?:=|\s+))"
    r"(?P<quote>[\"']?)(?P<secret>[^\s\"']+)(?P=quote)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RedactionCount:
    type: str
    count: int


@dataclass(frozen=True)
class SecretRedactionResult:
    records: Tuple[NormalizedRecord, ...]
    counts: Tuple[RedactionCount, ...]
    is_masked: bool


@dataclass(frozen=True)
class _Rule:
    pattern: Pattern[str]
    detector: str
    default_type: str
    secret_group: Optional[str] = None


_RULES = (
    _Rule(_PRIVATE_KEY, "pem_private_key", "private_key"),
    _Rule(_URL_CREDENTIAL, "url_userinfo", "url_credential", "credential"),
    _Rule(_WINDOWS_USER_PATH, "windows_user_profile", "local_path"),
    _Rule(_UNIX_HOME_PATH, "unix_home_directory", "local_path"),
    _Rule(_SSH_ACCOUNT, "ssh_account", "account", "account"),
    _Rule(_AUTHORIZATION, "authorization_header", "token", "secret"),
    _Rule(_JSON_ASSIGNMENT, "json_sensitive_field", "token", "secret"),
    _Rule(_ASSIGNMENT_QUOTED, "sensitive_assignment", "token", "secret"),
    _Rule(_ASSIGNMENT, "sensitive_assignment", "token", "secret"),
    _Rule(_COMMAND_OPTION_QUOTED, "sensitive_command_option", "token", "secret"),
    _Rule(_COMMAND_OPTION, "sensitive_command_option", "token", "secret"),
    _Rule(_KNOWN_API_KEY, "known_api_key_format", "api_key"),
    _Rule(_KNOWN_TOKEN, "known_token_format", "token"),
)


def redact_normalized_records(
    records: Iterable[NormalizedRecord],
) -> SecretRedactionResult:
    """Return new records whose text parts contain no recognized secrets."""

    redacted_records: List[NormalizedRecord] = []
    counts = {redaction_type: 0 for redaction_type in _MARKERS}
    for record in records:
        parts: List[NormalizedContentPart] = []
        for part in record.content:
            if part.text is None:
                parts.append(part)
                continue
            text, redactions = redact_text(part.text)
            for redaction in redactions:
                counts[redaction.type] += 1
            new_redactions = tuple(
                redaction
                for redaction in redactions
                if not (
                    redaction.detector == "existing_marker"
                    and any(item.type == redaction.type for item in part.redactions)
                )
            )
            parts.append(
                replace(
                    part,
                    text=text,
                    redactions=part.redactions + new_redactions,
                )
            )
        redacted_records.append(replace(record, content=tuple(parts)))

    count_values = tuple(
        RedactionCount(redaction_type, count)
        for redaction_type, count in counts.items()
        if count > 0
    )
    LOGGER.info(
        "秘密情報を除外: records=%d redactions=%d types=%s",
        len(redacted_records),
        sum(value.count for value in count_values),
        ",".join(value.type for value in count_values) or "none",
    )
    return SecretRedactionResult(tuple(redacted_records), count_values, True)


def redact_text(text: str) -> Tuple[str, Tuple[NormalizedRedaction, ...]]:
    """Redact one string without retaining secret values or their hashes."""

    redactions: List[NormalizedRedaction] = []
    protected, existing = _protect_existing_markers(text)
    redactions.extend(existing)
    for rule in _RULES:
        protected = rule.pattern.sub(
            lambda match, value=rule: _replacement(match, value, redactions),
            protected,
        )
    return _restore_existing_markers(protected), tuple(redactions)


def _replacement(
    match: Match[str],
    rule: _Rule,
    redactions: List[NormalizedRedaction],
) -> str:
    secret = match.group(rule.secret_group) if rule.secret_group else match.group(0)
    if "\x00REDACTED_" in secret or _EXISTING_MARKER.fullmatch(secret):
        return match.group(0)
    redaction_type = _type_for_match(match, rule.default_type)
    marker = _MARKERS[redaction_type]
    redactions.append(NormalizedRedaction(redaction_type, rule.detector))
    if rule.secret_group is None:
        return marker
    start, end = match.span(rule.secret_group)
    relative_start = start - match.start()
    relative_end = end - match.start()
    value = match.group(0)
    return value[:relative_start] + marker + value[relative_end:]


def _type_for_match(match: Match[str], default: str) -> str:
    name = match.groupdict().get("name")
    if not name:
        return default
    normalized = name.lower().replace("-", "_")
    if "password" in normalized or normalized in ("passwd", "pwd"):
        return "password"
    if "private" in normalized and "key" in normalized:
        return "private_key"
    if "api" in normalized and "key" in normalized:
        return "api_key"
    if "access_key" in normalized or "secret_key" in normalized:
        return "api_key"
    return "token"


def _protect_existing_markers(
    text: str,
) -> Tuple[str, Tuple[NormalizedRedaction, ...]]:
    redactions: List[NormalizedRedaction] = []

    def replace_marker(match: Match[str]) -> str:
        redaction_type = match.group(1).lower()
        redactions.append(NormalizedRedaction(redaction_type, "existing_marker"))
        return f"\x00REDACTED_{redaction_type.upper()}\x00"

    return _EXISTING_MARKER.sub(replace_marker, text), tuple(redactions)


def _restore_existing_markers(text: str) -> str:
    for redaction_type, marker in _MARKERS.items():
        text = text.replace(f"\x00REDACTED_{redaction_type.upper()}\x00", marker)
    return text
