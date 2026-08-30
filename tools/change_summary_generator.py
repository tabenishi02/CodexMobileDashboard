"""Generate evidence-backed change summaries from masked Codex sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from tools.file_reference_extractor import ExtractedFileReference
from tools.next_task_extractor import (
    FAILURE_CACHE_SECONDS,
    INFERENCE_INPUT_MAX_BYTES,
    INFERENCE_TIMEOUT_SECONDS,
)
from tools.work_status_extractor import CurrentWorkStatus, TurnWorkState


LOGGER = logging.getLogger("converter")
TITLE_MAX_CHARACTERS = 80
SHORT_SUMMARY_MAX_CHARACTERS = 160
DETAILS_MAX_CHARACTERS = 500
ITEM_MAX_CHARACTERS = 200
ITEM_MAX_COUNT = 5

_COMPLETION = re.compile(
    r"実装|作成|追加|更新|修正|変更|対応|完了|成功|通過|確認|コミット|導入|反映"
)
_RESULT = re.compile(
    r"(?:しました|しました。|済み|完了|成功|通過|確認できました|反映しました|追加しました|実装しました)"
)
_FAILURE = re.compile(r"失敗|未完了|中断|できません|エラー|保留")
_GENERIC_RESULT = re.compile(
    r"^(?:対応|実装|作業|修正|変更)?(?:しました|完了しました|済みです)[。.!！]?$"
)
_VERIFICATION = re.compile(r"テスト|検証|確認|ビルド|コンパイル|lint|成功|通過", re.IGNORECASE)
_FENCED_CODE = re.compile(r"(?ms)^[ \t]*```[^\r\n]*\r?\n.*?(?:^[ \t]*```[ \t]*$|\Z)")
_MARKDOWN_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)")


@dataclass(frozen=True)
class SummarySourceMessage:
    session_id: str
    message_id: str
    turn_id: Optional[str]
    role: str
    message_type: str
    phase: Optional[str]
    masked_text: str
    is_masked: bool


@dataclass(frozen=True)
class SummaryEvidenceItem:
    text: str
    source_message_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ChangeSummary:
    summary_id: str
    turn_id: str
    turn_id_source: str
    status: str
    rolled_back: bool
    title: str
    short_summary: str
    details: str
    highlights: Tuple[SummaryEvidenceItem, ...]
    verification: Tuple[SummaryEvidenceItem, ...]
    origin: str
    confidence: str
    source_session_ids: Tuple[str, ...]
    source_message_ids: Tuple[str, ...]


@dataclass(frozen=True)
class ChangeSummaryIssue:
    turn_id: str
    kind: str


@dataclass(frozen=True)
class ChangeSummaryCacheEntry:
    turn_id: str
    evidence_hash: str
    summary: Optional[ChangeSummary]
    expires_at: Optional[datetime]
    issues: Tuple[ChangeSummaryIssue, ...]


@dataclass(frozen=True)
class ChangeSummaryGenerationResult:
    summaries: Tuple[ChangeSummary, ...]
    issues: Tuple[ChangeSummaryIssue, ...]
    cache_entries: Tuple[ChangeSummaryCacheEntry, ...]
    inference_attempt_count: int


@dataclass(frozen=True)
class GeneratedSummaryContent:
    title: str
    short_summary: str
    details: str
    highlights: Tuple[SummaryEvidenceItem, ...]
    verification: Tuple[SummaryEvidenceItem, ...]
    confidence: str


@dataclass(frozen=True)
class _Prompt:
    text: str
    byte_count: int
    truncated: bool
    source_message_ids: Tuple[str, ...]


class ChangeSummaryCliRunner:
    """Generate one structured summary in an isolated Codex process."""

    def __init__(
        self,
        executable: Optional[str] = None,
        timeout_seconds: int = INFERENCE_TIMEOUT_SECONDS,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._cache: Dict[str, GeneratedSummaryContent] = {}

    def generate(self, prompt: str) -> GeneratedSummaryContent:
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        executable = self._executable or shutil.which("codex")
        if executable is None:
            raise RuntimeError("codex_not_found")
        schema = _output_schema()
        with tempfile.TemporaryDirectory(prefix="cmd-change-summary-") as directory:
            schema_path = Path(directory) / "change-summary.schema.json"
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False), encoding="utf-8"
            )
            command = [
                executable,
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--color",
                "never",
                "--output-schema",
                str(schema_path),
                "-",
            ]
            completed = subprocess.run(
                command,
                cwd=directory,
                input=prompt,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
                shell=False,
            )
        if completed.returncode != 0:
            raise RuntimeError("codex_nonzero_exit")
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError("codex_invalid_json") from error
        content = _parse_cli_content(value)
        self._cache[cache_key] = content
        return content


def generate_change_summaries(
    session_id: str,
    work_status: CurrentWorkStatus,
    source_messages: Iterable[SummarySourceMessage],
    *,
    file_references: Iterable[ExtractedFileReference] = tuple(),
    runner: Optional[ChangeSummaryCliRunner] = None,
    cache_entries: Iterable[ChangeSummaryCacheEntry] = tuple(),
    inference_turn_ids: Optional[Set[str]] = None,
    now: Optional[datetime] = None,
    allow_inference: bool = True,
    can_infer: Optional[Callable[[], bool]] = None,
) -> ChangeSummaryGenerationResult:
    """Summarize terminal turns using only explicitly masked session evidence."""

    messages = tuple(source_messages)
    references = tuple(file_references)
    cache_by_turn = {}
    for entry in cache_entries:
        cache_by_turn.setdefault(entry.turn_id, []).append(entry)
    current_time = now or datetime.now(timezone.utc)
    cli_runner = runner or ChangeSummaryCliRunner()
    summaries: List[ChangeSummary] = []
    issues: List[ChangeSummaryIssue] = []
    updated_cache: List[ChangeSummaryCacheEntry] = []
    attempts = 0

    for turn in work_status.turns:
        if turn.status == "in_progress" or (inference_turn_ids is not None and turn.turn_id not in inference_turn_ids):
            continue
        turn_messages = _messages_for_turn(session_id, turn, messages)
        input_is_masked = all(message.is_masked for message in turn_messages)
        evidence_hash = (
            _evidence_hash(session_id, turn, turn_messages, references)
            if input_is_masked
            else _unmasked_evidence_key(session_id, turn)
        )
        cached = next((entry for entry in cache_by_turn.get(turn.turn_id, ()) if _cache_is_usable(entry, evidence_hash, current_time)), None)
        if cached is not None:
            assert cached is not None
            if cached.summary is not None:
                summaries.append(cached.summary)
            issues.extend(cached.issues)
            updated_cache.append(cached)
            continue

        turn_issues: List[ChangeSummaryIssue] = []
        if not input_is_masked:
            turn_issues.append(
                ChangeSummaryIssue(turn.turn_id, "summary_input_not_masked")
            )
            cache = _failure_cache(turn, evidence_hash, turn_issues, current_time)
            issues.extend(turn_issues)
            updated_cache.append(cache)
            continue
        explicit = _explicit_summary(session_id, turn, turn_messages)
        if explicit is not None:
            cache = ChangeSummaryCacheEntry(
                turn.turn_id, evidence_hash, explicit, None, tuple()
            )
            summaries.append(explicit)
            updated_cache.append(cache)
            continue

        if not turn_messages:
            turn_issues.append(ChangeSummaryIssue(turn.turn_id, "summary_evidence_missing"))
            cache = _failure_cache(turn, evidence_hash, turn_issues, current_time)
            issues.extend(turn_issues)
            updated_cache.append(cache)
            continue

        prompt = _build_prompt(turn, turn_messages, references)
        if prompt.truncated:
            turn_issues.append(ChangeSummaryIssue(turn.turn_id, "summary_input_truncated"))
        if not prompt.source_message_ids:
            turn_issues.append(ChangeSummaryIssue(turn.turn_id, "summary_input_empty"))
            cache = _failure_cache(turn, evidence_hash, turn_issues, current_time)
            issues.extend(turn_issues)
            updated_cache.append(cache)
            continue

        if not allow_inference or (can_infer is not None and not can_infer()):
            turn_issues.append(ChangeSummaryIssue(turn.turn_id, "inference_limit_reached" if can_infer is not None else "inference_disabled"))
            updated_cache.append(ChangeSummaryCacheEntry(turn.turn_id, evidence_hash, None, None, tuple(turn_issues)))
            issues.extend(turn_issues)
            continue

        attempts += 1
        try:
            generated = cli_runner.generate(prompt.text)
            validated = _validate_generated_content(
                generated, set(prompt.source_message_ids)
            )
            summary = _make_summary(
                session_id,
                turn,
                validated,
                "codex_generated",
                prompt.source_message_ids,
            )
            summaries.append(summary)
            cache = ChangeSummaryCacheEntry(
                turn.turn_id, evidence_hash, summary, None, tuple(turn_issues)
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            kind = _runner_error_kind(error)
            turn_issues.append(ChangeSummaryIssue(turn.turn_id, kind))
            LOGGER.warning(
                "変更要約のCodex CLI生成に失敗: turn_id=%s kind=%s",
                turn.turn_id,
                kind,
            )
            cache = _failure_cache(turn, evidence_hash, turn_issues, current_time)
        issues.extend(turn_issues)
        updated_cache.append(cache)

    LOGGER.info(
        "変更要約を生成: session_id=%s summaries=%d warnings=%d cli_attempts=%d",
        session_id,
        len(summaries),
        len(issues),
        attempts,
    )
    return ChangeSummaryGenerationResult(
        tuple(summaries), tuple(issues), tuple(updated_cache), attempts
    )


def _explicit_summary(
    session_id: str,
    turn: TurnWorkState,
    messages: Sequence[SummarySourceMessage],
) -> Optional[ChangeSummary]:
    assistants = [
        message
        for message in messages
        if message.role == "assistant" and message.message_type == "chat"
    ]
    finals = [message for message in assistants if message.phase == "final_answer"]
    candidates = finals or [
        message for message in assistants if message.phase != "commentary"
    ]
    for message in reversed(candidates):
        text = _clean_text(message.masked_text)
        sentence = _explicit_result_sentence(text, turn.status)
        if sentence is None:
            continue
        details = _limit(_plain_text(text), DETAILS_MAX_CHARACTERS)
        bullets = _bullet_items(text, message.message_id)
        verification = tuple(
            item for item in bullets if _VERIFICATION.search(item.text)
        )[:ITEM_MAX_COUNT]
        highlights = tuple(
            item for item in bullets if not _VERIFICATION.search(item.text)
        )[:ITEM_MAX_COUNT]
        content = GeneratedSummaryContent(
            title=_title_from_sentence(sentence),
            short_summary=_limit(sentence, SHORT_SUMMARY_MAX_CHARACTERS),
            details=details or _limit(sentence, DETAILS_MAX_CHARACTERS),
            highlights=highlights,
            verification=verification,
            confidence="high" if turn.status == "completed" else "medium",
        )
        return _make_summary(
            session_id,
            turn,
            content,
            "explicit",
            (message.message_id,),
        )
    return None


def _build_prompt(
    turn: TurnWorkState,
    messages: Sequence[SummarySourceMessage],
    references: Sequence[ExtractedFileReference],
) -> _Prompt:
    instruction = (
        "次のマスク済みCodexセッション情報だけを根拠に、日本語の変更要約を生成してください。"
        "Git差分やプロジェクトファイルを参照してはいけません。"
        "Codex最終回答は実施内容・結果の最優先根拠、ユーザー指示は目的だけ、途中経過は補助だけ、"
        "ツール概要は明示的な成功・失敗だけの根拠です。ファイル参照だけから変更を断定しないでください。"
        "失敗・未完了は完了と断定せず、ロールバック済みなら現在も有効とは表現しないでください。"
        "入力に存在しない変更、ファイル名、検証結果を生成しないでください。"
        "コードブロックは生成しないでください。"
        "title、short_summary、details、highlights、verification、confidenceをJSONで返してください。\n"
    )
    payload: Dict[str, object] = {
        "turn": {
            "turn_id": turn.turn_id,
            "status": turn.status,
            "rolled_back": turn.rolled_back,
        },
        "messages": [],
        "file_references": [],
    }
    truncated = False
    accepted_ids: List[str] = []

    ordered = sorted(messages, key=_message_priority)
    for message in ordered:
        safe_text = _clean_text(message.masked_text)
        if not safe_text:
            continue
        item = {
            "message_id": message.message_id,
            "role": message.role,
            "message_type": message.message_type,
            "phase": message.phase,
            "text": safe_text,
        }
        candidate = dict(payload)
        candidate["messages"] = list(payload["messages"]) + [item]
        if _prompt_fits(instruction, candidate):
            payload = candidate
            accepted_ids.append(message.message_id)
            continue
        shortened = dict(item)
        shortened["text"] = _fit_message_text(
            instruction, payload, shortened, safe_text
        )
        candidate = dict(payload)
        candidate["messages"] = list(payload["messages"]) + [shortened]
        if shortened["text"] and _prompt_fits(instruction, candidate):
            payload = candidate
            accepted_ids.append(message.message_id)
        truncated = True
        break

    accepted = set(accepted_ids)
    for reference in references:
        if not accepted.intersection(reference.source_message_ids):
            continue
        item = {
            "path": reference.path,
            "display_name": reference.display_name,
            "scope": reference.scope,
            "source_message_ids": [
                value for value in reference.source_message_ids if value in accepted
            ],
        }
        candidate = dict(payload)
        candidate["file_references"] = list(payload["file_references"]) + [item]
        if _prompt_fits(instruction, candidate):
            payload = candidate
        else:
            truncated = True
            break

    text = instruction + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _Prompt(
        text,
        len(text.encode("utf-8")),
        truncated,
        tuple(accepted_ids),
    )


def _messages_for_turn(
    session_id: str,
    turn: TurnWorkState,
    messages: Sequence[SummarySourceMessage],
) -> Tuple[SummarySourceMessage, ...]:
    source_ids = set(turn.assistant_message_ids)
    if turn.user_message_id is not None:
        source_ids.add(turn.user_message_id)
    return tuple(
        message
        for message in messages
        if message.session_id == session_id
        and (message.turn_id == turn.turn_id or message.message_id in source_ids)
        and (
            (message.message_type == "chat" and message.role in ("user", "assistant"))
            or message.message_type == "tool_summary"
        )
    )


def _message_priority(message: SummarySourceMessage) -> int:
    if message.role == "assistant" and message.phase == "final_answer":
        priority = 0
    elif message.role == "user" and message.message_type == "chat":
        priority = 1
    elif message.role == "assistant" and message.phase == "commentary":
        priority = 2
    elif message.message_type == "tool_summary":
        priority = 3
    else:
        priority = 4
    return priority


def _explicit_result_sentence(text: str, status: str) -> Optional[str]:
    for sentence in _sentences(text):
        if _GENERIC_RESULT.fullmatch(sentence):
            continue
        if status == "completed" and _COMPLETION.search(sentence) and _RESULT.search(sentence):
            return sentence
        if status in ("failed", "incomplete") and _FAILURE.search(sentence):
            return sentence
    return None


def _sentences(text: str) -> Tuple[str, ...]:
    plain = _plain_text(text)
    return tuple(
        item.strip()
        for item in re.split(r"(?<=[。！？!?])\s+|[\r\n]+", plain)
        if item.strip()
    )


def _bullet_items(text: str, message_id: str) -> Tuple[SummaryEvidenceItem, ...]:
    items: List[SummaryEvidenceItem] = []
    for line in text.splitlines():
        if not re.match(r"^\s*[-*+]\s+\S", line):
            continue
        value = _MARKDOWN_PREFIX.sub("", line).strip()
        if value:
            items.append(
                SummaryEvidenceItem(
                    _limit(value, ITEM_MAX_CHARACTERS), (message_id,)
                )
            )
    return tuple(items)


def _clean_text(text: str) -> str:
    return _FENCED_CODE.sub("", text).strip()


def _plain_text(text: str) -> str:
    cleaned = _clean_text(text)
    lines = [_MARKDOWN_PREFIX.sub("", line).strip() for line in cleaned.splitlines()]
    return " ".join(line for line in lines if line)


def _title_from_sentence(sentence: str) -> str:
    value = sentence.strip().rstrip("。.!！")
    value = re.sub(r"^(?:対応内容|実施内容|結果|変更内容)\s*[:：]\s*", "", value)
    return _limit(value, TITLE_MAX_CHARACTERS)


def _make_summary(
    session_id: str,
    turn: TurnWorkState,
    content: GeneratedSummaryContent,
    origin: str,
    source_message_ids: Sequence[str],
) -> ChangeSummary:
    source_ids = tuple(dict.fromkeys(source_message_ids))
    identity = "\0".join((session_id, turn.turn_id, *source_ids))
    summary_id = "change_summary_" + hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()
    short_summary = content.short_summary
    details = content.details
    if turn.rolled_back:
        short_summary = _append_notice(
            short_summary,
            "このターンはロールバック済みです。",
            SHORT_SUMMARY_MAX_CHARACTERS,
        )
        details = _append_notice(
            details,
            "このターンはロールバックされ、最新の有効な変更には含まれません。",
            DETAILS_MAX_CHARACTERS,
        )
    return ChangeSummary(
        summary_id=summary_id,
        turn_id=turn.turn_id,
        turn_id_source=turn.turn_id_source,
        status=turn.status,
        rolled_back=turn.rolled_back,
        title=_limit(content.title, TITLE_MAX_CHARACTERS),
        short_summary=_limit(short_summary, SHORT_SUMMARY_MAX_CHARACTERS),
        details=_limit(details, DETAILS_MAX_CHARACTERS),
        highlights=content.highlights[:ITEM_MAX_COUNT],
        verification=content.verification[:ITEM_MAX_COUNT],
        origin=origin,
        confidence=content.confidence,
        source_session_ids=(session_id,),
        source_message_ids=source_ids,
    )


def _validate_generated_content(
    content: GeneratedSummaryContent, allowed_ids: Set[str]
) -> GeneratedSummaryContent:
    if not content.title or not content.short_summary or not content.details:
        raise RuntimeError("codex_invalid_result")
    if any(
        "```" in value
        for value in (content.title, content.short_summary, content.details)
    ):
        raise RuntimeError("codex_invalid_result")
    if content.confidence not in ("high", "medium", "low"):
        raise RuntimeError("codex_invalid_result")
    for item in (*content.highlights, *content.verification):
        if not item.text or not item.source_message_ids:
            raise RuntimeError("codex_invalid_result")
        if len(item.text) > ITEM_MAX_CHARACTERS or "```" in item.text:
            raise RuntimeError("codex_invalid_result")
        if any(source_id not in allowed_ids for source_id in item.source_message_ids):
            raise RuntimeError("codex_invalid_source_reference")
    return content


def _parse_cli_content(value: object) -> GeneratedSummaryContent:
    if not isinstance(value, dict):
        raise RuntimeError("codex_invalid_result")
    title = value.get("title")
    short_summary = value.get("short_summary")
    details = value.get("details")
    confidence = value.get("confidence")
    if not all(isinstance(item, str) for item in (title, short_summary, details, confidence)):
        raise RuntimeError("codex_invalid_result")
    return GeneratedSummaryContent(
        str(title).strip(),
        str(short_summary).strip(),
        str(details).strip(),
        _parse_evidence_items(value.get("highlights")),
        _parse_evidence_items(value.get("verification")),
        str(confidence),
    )


def _parse_evidence_items(value: object) -> Tuple[SummaryEvidenceItem, ...]:
    if not isinstance(value, list) or len(value) > ITEM_MAX_COUNT:
        raise RuntimeError("codex_invalid_result")
    items: List[SummaryEvidenceItem] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise RuntimeError("codex_invalid_result")
        text = entry.get("text")
        source_ids = entry.get("source_message_ids")
        if not isinstance(text, str) or not isinstance(source_ids, list):
            raise RuntimeError("codex_invalid_result")
        if not all(isinstance(item, str) for item in source_ids):
            raise RuntimeError("codex_invalid_result")
        items.append(SummaryEvidenceItem(text.strip(), tuple(source_ids)))
    return tuple(items)


def _output_schema() -> Dict[str, object]:
    evidence = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "minLength": 1, "maxLength": ITEM_MAX_CHARACTERS},
            "source_message_ids": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "minLength": 1},
            },
        },
        "required": ["text", "source_message_ids"],
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": TITLE_MAX_CHARACTERS},
            "short_summary": {"type": "string", "minLength": 1, "maxLength": SHORT_SUMMARY_MAX_CHARACTERS},
            "details": {"type": "string", "minLength": 1, "maxLength": DETAILS_MAX_CHARACTERS},
            "highlights": {"type": "array", "maxItems": ITEM_MAX_COUNT, "items": evidence},
            "verification": {"type": "array", "maxItems": ITEM_MAX_COUNT, "items": evidence},
            "confidence": {"enum": ["high", "medium", "low"]},
        },
        "required": ["title", "short_summary", "details", "highlights", "verification", "confidence"],
        "additionalProperties": False,
    }


def _evidence_hash(
    session_id: str,
    turn: TurnWorkState,
    messages: Sequence[SummarySourceMessage],
    references: Sequence[ExtractedFileReference],
) -> str:
    message_ids = {message.message_id for message in messages}
    value = {
        "session_id": session_id,
        "turn": {
            "turn_id": turn.turn_id,
            "turn_id_source": turn.turn_id_source,
            "status": turn.status,
            "rolled_back": turn.rolled_back,
        },
        "messages": [
            {
                "message_id": item.message_id,
                "role": item.role,
                "message_type": item.message_type,
                "phase": item.phase,
                "masked_text": _clean_text(item.masked_text),
                "is_masked": item.is_masked,
            }
            for item in messages
        ],
        "file_references": [
            {
                "reference_id": item.reference_id,
                "path": item.path,
                "display_name": item.display_name,
            }
            for item in references
            if message_ids.intersection(item.source_message_ids)
        ],
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _unmasked_evidence_key(session_id: str, turn: TurnWorkState) -> str:
    source = "\0".join((session_id, turn.turn_id, "unmasked"))
    return "unmasked_" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _prompt_fits(instruction: str, payload: Dict[str, object]) -> bool:
    rendered = instruction + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )
    return len(rendered.encode("utf-8")) <= INFERENCE_INPUT_MAX_BYTES


def _fit_message_text(
    instruction: str,
    payload: Dict[str, object],
    item: Dict[str, object],
    original: str,
) -> str:
    low = 0
    high = len(original)
    while low < high:
        middle = (low + high + 1) // 2
        candidate_item = dict(item)
        candidate_item["text"] = original[:middle] + "…"
        candidate = dict(payload)
        candidate["messages"] = list(payload["messages"]) + [candidate_item]
        if _prompt_fits(instruction, candidate):
            low = middle
        else:
            high = middle - 1
    return original[:low] + "…" if low > 0 else ""


def _failure_cache(
    turn: TurnWorkState,
    evidence_hash: str,
    issues: Sequence[ChangeSummaryIssue],
    now: datetime,
) -> ChangeSummaryCacheEntry:
    return ChangeSummaryCacheEntry(
        turn.turn_id,
        evidence_hash,
        None,
        now + timedelta(seconds=FAILURE_CACHE_SECONDS),
        tuple(issues),
    )


def _cache_is_usable(
    entry: Optional[ChangeSummaryCacheEntry], evidence_hash: str, now: datetime
) -> bool:
    if entry is None or entry.evidence_hash != evidence_hash:
        return False
    return entry.expires_at is None or now < entry.expires_at


def _runner_error_kind(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return "codex_timeout"
    value = str(error)
    known = {
        "codex_not_found",
        "codex_nonzero_exit",
        "codex_invalid_json",
        "codex_invalid_result",
        "codex_invalid_source_reference",
    }
    return value if value in known else "codex_execution_error"


def _limit(value: str, maximum: int) -> str:
    cleaned = value.strip()
    return cleaned if len(cleaned) <= maximum else cleaned[: maximum - 1] + "…"


def _append_notice(value: str, notice: str, maximum: int) -> str:
    separator = " " if value.strip() else ""
    reserved = len(separator) + len(notice)
    prefix = _limit(value, max(1, maximum - reserved))
    return prefix + separator + notice
