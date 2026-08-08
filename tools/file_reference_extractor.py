"""Extract safe file references from visible chat messages."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tools.chat_extractor import ExtractedChatMessage


LOGGER = logging.getLogger("converter")

_KNOWN_EXTENSIONS = {
    ".bat", ".c", ".cfg", ".cmd", ".cpp", ".cs", ".css", ".csv", ".doc",
    ".docx", ".gif", ".go", ".h", ".hpp", ".htm", ".html", ".ini", ".java",
    ".cjs", ".jpeg", ".jpg", ".js", ".json", ".jsonl", ".jsx", ".kt", ".kts", ".log",
    ".markdown", ".md", ".mjs", ".pdf", ".php", ".png", ".ppt", ".pptx",
    ".ps1", ".py", ".pyi", ".rb", ".rs", ".scss", ".sh", ".sql", ".svg",
    ".toml", ".ts", ".tsv", ".tsx", ".txt", ".webp", ".xls", ".xlsx", ".xml",
    ".yaml", ".yml",
}

_ATTACHMENT_BLOCK = re.compile(
    r"(?ms)^# Files mentioned by the user:\s*\n(?P<body>.*?)(?=^## My request for Codex:|\Z)"
)
_ATTACHMENT_LINE = re.compile(r"(?m)^##\s+[^:\r\n]+:\s*(?P<path>[^\r\n]+?)\s*$")
_FENCED_CODE = re.compile(
    r"(?ms)^[ \t]*```[^\r\n]*\r?\n.*?(?:^[ \t]*```[ \t]*$|\Z)"
)
_MARKDOWN_LINK = re.compile(r"\[[^\]\r\n]+\]\((?P<target>[^)\r\n]+)\)")
_INLINE_CODE = re.compile(r"(?<!`)`(?P<value>[^`\r\n]+)`(?!`)")
_QUOTED_PATH = re.compile(
    r"(?P<quote>[\"'])(?P<path>(?:[A-Za-z]:\\|\\\\)[^\r\n]+?)(?P=quote)"
)
_WINDOWS_ABSOLUTE = re.compile(r"(?<![\w])(?P<path>[A-Za-z]:\\[^\s\"<>|?*`]+)")
_UNC_PATH = re.compile(r"(?<![\w])(?P<path>\\\\[^\s\"<>|?*`]+)")
_RELATIVE_PATH = re.compile(
    r"(?<![A-Za-z0-9_./\\-])(?P<path>(?:[^\s/\\:]+[\\/])+[^\s/\\:]+\."
    r"[A-Za-z0-9]{1,12}(?::\d+(?::\d+)?)?)"
)
_RELATIVE_DIRECTORY = re.compile(
    r"(?<![A-Za-z0-9_./\\-])(?P<path>(?:[A-Za-z0-9_.-]+[\\/])+)(?=$|[\s、。）」』】をはにでとが])"
)
_BARE_FILE = re.compile(
    r"(?<![A-Za-z0-9_./\\-])(?P<path>[\w.-]+\."
    r"(?:md|markdown|txt|py|pyi|json|jsonl|ini|cfg|toml|yaml|yml|js|mjs|cjs|"
    r"ts|tsx|jsx|html|htm|css|scss|java|kt|kts|c|h|cpp|hpp|cs|go|rs|rb|php|"
    r"sh|ps1|bat|cmd|sql|xml|csv|tsv|pdf|docx?|xlsx?|pptx?|svg|png|jpe?g|gif|webp)"
    r"(?::\d+(?::\d+)?)?)",
    re.IGNORECASE,
)
_LOCATION = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?::(?P<column>\d+))?$")
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_JAPANESE_PATH_SUFFIX = re.compile(
    r"^(?P<path>.+?\.[A-Za-z0-9]{1,12}(?::\d+(?::\d+)?)?)"
    r"(?:(?:を|は|に|で|と|が)(?:確認|参照|表示|修正|使用|作成|更新|削除|開|読)|です|でした).*$"
)


@dataclass(frozen=True)
class FileReferenceMention:
    session_id: str
    message_id: str
    line: Optional[int]
    column: Optional[int]
    origin: str


@dataclass(frozen=True)
class ExtractedFileReference:
    reference_id: str
    scope: str
    path: Optional[str]
    display_name: str
    extension: str
    kind: str
    mention_count: int
    source_session_ids: Tuple[str, ...]
    source_message_ids: Tuple[str, ...]
    mentions: Tuple[FileReferenceMention, ...]


@dataclass(frozen=True)
class FileReferenceIssue:
    session_id: str
    message_id: str
    kind: str


@dataclass(frozen=True)
class FileReferenceExtractionResult:
    references: Tuple[ExtractedFileReference, ...]
    issues: Tuple[FileReferenceIssue, ...]


@dataclass(frozen=True)
class _Candidate:
    value: str
    origin: str


def extract_file_references(
    messages: Iterable[Tuple[str, ExtractedChatMessage]],
    workspace_root: Path,
    workspace_id: str,
) -> FileReferenceExtractionResult:
    """Extract references without reading file contents or retaining external paths."""

    root = workspace_root.resolve(strict=False)
    references: List[ExtractedFileReference] = []
    workspace_index: Dict[str, int] = {}
    issues: List[FileReferenceIssue] = []

    for session_id, message in messages:
        if message.role not in ("user", "assistant") or message.message_type != "chat":
            continue
        text = _message_text(message)
        candidates = _candidates(text)
        external_seen: set = set()
        external_ordinal = 0
        for candidate in candidates:
            raw_path, line, column = _split_location(candidate.value)
            classified = _classify_path(raw_path, root, candidate.origin)
            if classified is None:
                continue
            scope, safe_path, display_name, extension, kind, transient_key = classified
            mention = FileReferenceMention(
                session_id,
                message.message_id,
                line,
                column,
                candidate.origin,
            )

            if scope == "workspace":
                assert safe_path is not None
                key = safe_path.casefold()
                existing_index = workspace_index.get(key)
                if existing_index is not None:
                    existing = references[existing_index]
                    if mention in existing.mentions:
                        continue
                    references[existing_index] = replace(
                        existing,
                        kind=_prefer_kind(existing.kind, kind),
                        mention_count=existing.mention_count + 1,
                        source_session_ids=_merge(
                            existing.source_session_ids, (session_id,)
                        ),
                        source_message_ids=_merge(
                            existing.source_message_ids, (message.message_id,)
                        ),
                        mentions=existing.mentions + (mention,),
                    )
                    continue
                reference_id = _reference_id(workspace_id, "workspace", safe_path)
                workspace_index[key] = len(references)
            else:
                if transient_key in external_seen:
                    continue
                external_seen.add(transient_key)
                external_ordinal += 1
                reference_id = _reference_id(
                    workspace_id,
                    "external",
                    message.message_id,
                    str(external_ordinal),
                    display_name,
                )

            references.append(
                ExtractedFileReference(
                    reference_id=reference_id,
                    scope=scope,
                    path=safe_path,
                    display_name=display_name,
                    extension=extension,
                    kind=kind,
                    mention_count=1,
                    source_session_ids=(session_id,),
                    source_message_ids=(message.message_id,),
                    mentions=(mention,),
                )
            )

    LOGGER.info(
        "ファイル参照を抽出: workspace_id=%s references=%d warnings=%d",
        workspace_id,
        len(references),
        len(issues),
    )
    return FileReferenceExtractionResult(tuple(references), tuple(issues))


def _candidates(text: str) -> Tuple[_Candidate, ...]:
    values: List[_Candidate] = []
    attachment_spans: List[Tuple[int, int]] = []
    for block in _ATTACHMENT_BLOCK.finditer(text):
        attachment_spans.append(block.span())
        for match in _ATTACHMENT_LINE.finditer(block.group("body")):
            values.append(_Candidate(_strip_wrapper(match.group("path")), "attachment"))

    visible = _blank_spans(text, attachment_spans)
    visible = _FENCED_CODE.sub(lambda match: " " * len(match.group(0)), visible)
    consumed: List[Tuple[int, int]] = []

    for match in _MARKDOWN_LINK.finditer(visible):
        target = _strip_wrapper(match.group("target"))
        if _looks_like_local_path(target):
            values.append(_Candidate(target, "markdown_link"))
        consumed.append(match.span())
    for match in _INLINE_CODE.finditer(visible):
        value = _strip_wrapper(match.group("value"))
        if _looks_like_local_path(value):
            values.append(_Candidate(value, "inline_code"))
        consumed.append(match.span())
    for match in _QUOTED_PATH.finditer(visible):
        value = _strip_wrapper(match.group("path"))
        if _looks_like_local_path(value):
            values.append(_Candidate(value, "plain_text"))
        consumed.append(match.span())

    plain = _blank_spans(visible, consumed)
    for pattern in (
        _UNC_PATH,
        _WINDOWS_ABSOLUTE,
        _RELATIVE_PATH,
        _RELATIVE_DIRECTORY,
        _BARE_FILE,
    ):
        for match in pattern.finditer(plain):
            value = _strip_wrapper(match.group("path"))
            if _looks_like_local_path(value):
                values.append(_Candidate(value, "plain_text"))
    return tuple(values)


def _classify_path(
    value: str, workspace_root: Path, origin: str
) -> Optional[Tuple[str, Optional[str], str, str, str, str]]:
    path_value = value.strip()
    if not path_value or _URL_SCHEME.match(path_value) or path_value.startswith("#"):
        return None
    if path_value.lower().startswith("file://"):
        return None

    windows_absolute = _is_windows_absolute(path_value)
    unc = path_value.startswith("\\\\")
    posix_absolute = path_value.startswith("/")
    if unc:
        display_name = _display_name(path_value)
        if not display_name:
            return None
        return (
            "external",
            None,
            display_name,
            _extension(display_name),
            "unknown",
            os.path.normcase(path_value),
        )
    if windows_absolute or posix_absolute:
        candidate = Path(path_value)
    else:
        candidate = workspace_root / Path(path_value.replace("\\", os.sep))

    resolved = candidate.resolve(strict=False)
    if _is_within(resolved, workspace_root):
        relative = resolved.relative_to(workspace_root).as_posix()
        if relative == ".":
            return None
        display_name = _display_name(relative)
        extension = _extension(display_name)
        kind = _local_kind(resolved)
        if (
            kind == "missing"
            and extension not in _KNOWN_EXTENSIONS
            and "/" not in path_value
            and "\\" not in path_value
            and origin in ("inline_code", "plain_text")
        ):
            return None
        return (
            "workspace",
            relative,
            display_name,
            extension,
            kind,
            relative.casefold(),
        )

    display_name = _display_name(path_value)
    if not display_name:
        return None
    kind = _local_kind(resolved)
    return (
        "external",
        None,
        display_name,
        _extension(display_name),
        kind,
        os.path.normcase(str(resolved)),
    )


def _split_location(value: str) -> Tuple[str, Optional[int], Optional[int]]:
    match = _LOCATION.match(value)
    if match is None:
        return value, None, None
    path = match.group("path")
    if len(path) == 1 and path.isalpha():
        return value, None, None
    return path, int(match.group("line")), (
        int(match.group("column")) if match.group("column") else None
    )


def _looks_like_local_path(value: str) -> bool:
    stripped, _, _ = _split_location(_strip_wrapper(value))
    if not stripped or _URL_SCHEME.match(stripped):
        return False
    if _is_windows_absolute(stripped) or stripped.startswith(("\\\\", "/")):
        return True
    if "/" in stripped or "\\" in stripped:
        return True
    return _extension(_display_name(stripped)) != ""


def _local_kind(path: Path) -> str:
    try:
        if path.is_file():
            return "file"
        if path.is_dir():
            return "directory"
        if path.exists():
            return "unknown"
        return "missing"
    except OSError:
        return "unknown"


def _is_within(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path), str(root))).casefold() == str(root).casefold()
    except ValueError:
        return False


def _is_windows_absolute(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _display_name(value: str) -> str:
    stripped = value.rstrip("\\/")
    if _is_windows_absolute(stripped) or "\\" in stripped:
        return PureWindowsPath(stripped).name
    return PurePosixPath(stripped).name


def _extension(name: str) -> str:
    if "." not in name or name.startswith(".") and name.count(".") == 1:
        return ""
    return "." + name.rsplit(".", 1)[1].lower()


def _prefer_kind(current: str, new: str) -> str:
    priority = {"unknown": 0, "missing": 1, "directory": 2, "file": 3}
    return new if priority.get(new, 0) > priority.get(current, 0) else current


def _reference_id(workspace_id: str, *values: str) -> str:
    source = "\0".join((workspace_id, *values))
    return "file_ref_" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _message_text(message: ExtractedChatMessage) -> str:
    return "\n".join(part.text for part in message.content if part.text).strip()


def _strip_wrapper(value: str) -> str:
    stripped = value.strip().strip("<>\"'").rstrip(".,;、。）」』】")
    match = _JAPANESE_PATH_SUFFIX.match(stripped)
    return match.group("path") if match is not None else stripped


def _blank_spans(text: str, spans: Sequence[Tuple[int, int]]) -> str:
    if not spans:
        return text
    characters = list(text)
    for start, end in spans:
        characters[start:end] = " " * (end - start)
    return "".join(characters)


def _merge(first: Sequence[str], second: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *second)))
