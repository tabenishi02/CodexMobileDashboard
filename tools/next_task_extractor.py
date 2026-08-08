"""Extract an explicit next task or infer one through an isolated Codex CLI."""

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
from typing import Iterable, List, Optional, Sequence, Tuple

from tools.chat_extractor import ExtractedChatMessage


LOGGER = logging.getLogger("converter")
INFERENCE_INPUT_MAX_BYTES = 128 * 1024
INFERENCE_TIMEOUT_SECONDS = 120
FAILURE_CACHE_SECONDS = 5 * 60

_CONFIDENCE_VALUES = ("high", "medium", "low")
_EXPLICIT_PATTERNS = (
    re.compile(
        r"(?:次に行う|次の)(?:作業|タスク)\s*(?:は|[:：])\s*[「『\"']?"
        r"(?P<task>[^\r\n。！？!?」』\"']+)",
    ),
    re.compile(
        r"次\s*は\s*[「『\"']?(?P<task>[^\r\n。！？!?」』\"']+)",
    ),
    re.compile(
        r"(?:Next task|Next action)\s*[:：]\s*[\"']?"
        r"(?P<task>[^\r\n.!?\"']+)",
        re.IGNORECASE,
    ),
)
_UNCHECKED_TASK = re.compile(r"^\s*-\s*\[ \]\s+(.+?)\s*$")


@dataclass(frozen=True)
class InferenceMessage:
    message_id: str
    role: str
    text: str


@dataclass(frozen=True)
class NextTaskInferenceContext:
    current_status: Optional[str]
    recent_messages: Tuple[InferenceMessage, ...]
    decisions: Tuple[str, ...]
    incomplete_tasks: Tuple[str, ...]
    is_masked: bool


@dataclass(frozen=True)
class NextTask:
    task_id: str
    text: str
    status: str
    origin: str
    confidence: str
    reason: Optional[str]
    source_message_ids: Tuple[str, ...]


@dataclass(frozen=True)
class NextTaskIssue:
    kind: str


@dataclass(frozen=True)
class NextTaskCacheEntry:
    evidence_hash: str
    task: Optional[NextTask]
    expires_at: Optional[datetime]
    issues: Tuple[NextTaskIssue, ...]


@dataclass(frozen=True)
class NextTaskExtractionResult:
    task: Optional[NextTask]
    issues: Tuple[NextTaskIssue, ...]
    cache_entry: Optional[NextTaskCacheEntry]
    inference_attempted: bool
    inference_input_bytes: int
    inference_input_truncated: bool


@dataclass(frozen=True)
class _PromptBuildResult:
    text: str
    byte_count: int
    truncated: bool


class CodexCliRunner:
    """Run Codex with saved authentication and no project access."""

    def __init__(
        self,
        executable: Optional[str] = None,
        timeout_seconds: int = INFERENCE_TIMEOUT_SECONDS,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def infer(self, prompt: str) -> Tuple[str, str, str]:
        executable = self._executable or shutil.which("codex")
        if executable is None:
            raise RuntimeError("codex_not_found")

        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "task": {"type": "string", "minLength": 1},
                "reason": {"type": "string"},
                "confidence": {"enum": list(_CONFIDENCE_VALUES)},
            },
            "required": ["task", "reason", "confidence"],
            "additionalProperties": False,
        }
        with tempfile.TemporaryDirectory(prefix="cmd-next-task-") as directory:
            schema_path = Path(directory) / "next-task.schema.json"
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
        if not isinstance(value, dict):
            raise RuntimeError("codex_invalid_result")
        task = value.get("task")
        reason = value.get("reason")
        confidence = value.get("confidence")
        if (
            not isinstance(task, str)
            or not task.strip()
            or not isinstance(reason, str)
            or confidence not in _CONFIDENCE_VALUES
        ):
            raise RuntimeError("codex_invalid_result")
        return task.strip(), reason.strip(), str(confidence)


def extract_next_task(
    recent_messages: Iterable[ExtractedChatMessage],
    inference_context: NextTaskInferenceContext,
    tasks_path: Path,
    *,
    runner: Optional[CodexCliRunner] = None,
    cache_entry: Optional[NextTaskCacheEntry] = None,
    now: Optional[datetime] = None,
) -> NextTaskExtractionResult:
    """Return one next task, preferring explicit conversation evidence."""

    messages = tuple(recent_messages)
    explicit = _extract_explicit(messages)
    if explicit is not None:
        LOGGER.info("次タスクを明示メッセージから抽出: origin=explicit")
        return NextTaskExtractionResult(explicit, tuple(), None, False, 0, False)

    current_time = now or datetime.now(timezone.utc)
    evidence_hash = _evidence_hash(inference_context)
    if _cache_is_usable(cache_entry, evidence_hash, current_time):
        assert cache_entry is not None
        LOGGER.info(
            "次タスク推定キャッシュを使用: origin=%s",
            cache_entry.task.origin if cache_entry.task is not None else "none",
        )
        return NextTaskExtractionResult(
            cache_entry.task,
            cache_entry.issues,
            cache_entry,
            False,
            0,
            False,
        )

    issues: List[NextTaskIssue] = []
    prompt = _build_prompt(inference_context)
    if prompt.truncated:
        issues.append(NextTaskIssue("inference_input_truncated"))

    if not inference_context.is_masked:
        issues.append(NextTaskIssue("inference_input_not_masked"))
        return _fallback_result(
            tasks_path,
            evidence_hash,
            current_time,
            issues,
            False,
            prompt,
        )

    cli_runner = runner or CodexCliRunner()
    try:
        text, reason, confidence = cli_runner.infer(prompt.text)
        source_ids = tuple(message.message_id for message in inference_context.recent_messages)
        task = _make_task(text, "codex_inferred", confidence, reason, source_ids)
        successful_cache = NextTaskCacheEntry(
            evidence_hash, task, None, tuple(issues)
        )
        LOGGER.info(
            "次タスクをCodex CLIで推定: confidence=%s input_bytes=%d",
            confidence,
            prompt.byte_count,
        )
        return NextTaskExtractionResult(
            task,
            tuple(issues),
            successful_cache,
            True,
            prompt.byte_count,
            prompt.truncated,
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        kind = _runner_error_kind(error)
        issues.append(NextTaskIssue(kind))
        LOGGER.warning("次タスクのCodex CLI推定に失敗: kind=%s", kind)
        return _fallback_result(
            tasks_path,
            evidence_hash,
            current_time,
            issues,
            True,
            prompt,
        )


def _extract_explicit(messages: Sequence[ExtractedChatMessage]) -> Optional[NextTask]:
    for message in reversed(messages):
        if message.message_type != "chat" or message.role not in ("user", "assistant"):
            continue
        text = "\n".join(part.text for part in message.content if part.text)
        for pattern in _EXPLICIT_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            task_text = match.group("task").strip(" \t:：-—")
            if not task_text:
                continue
            return _make_task(
                task_text,
                "explicit",
                "high",
                "最新2ターン内のメッセージに次タスクとして明示されています。",
                (message.message_id,),
            )
    return None


def _build_prompt(context: NextTaskInferenceContext) -> _PromptBuildResult:
    instruction = (
        "次のマスク済み開発コンテキストだけを根拠に、実行可能な次タスクを1件推定してください。"
        "プロジェクトファイルや外部情報を参照せず、task、reason、confidenceをJSONで返してください。\n"
    )
    payload = {
        "incomplete_tasks": [],
        "decisions": [],
        "current_status": None,
        "recent_messages": [],
    }
    truncated = False

    def fits(candidate: object) -> bool:
        rendered = instruction + json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        return len(rendered.encode("utf-8")) <= INFERENCE_INPUT_MAX_BYTES

    for task in context.incomplete_tasks:
        candidate = dict(payload)
        candidate["incomplete_tasks"] = list(payload["incomplete_tasks"]) + [task]
        if fits(candidate):
            payload = candidate
        else:
            truncated = True
            break
    for decision in context.decisions:
        candidate = dict(payload)
        candidate["decisions"] = list(payload["decisions"]) + [decision]
        if fits(candidate):
            payload = candidate
        else:
            truncated = True
            break
    if context.current_status is not None:
        candidate = dict(payload)
        candidate["current_status"] = context.current_status
        if fits(candidate):
            payload = candidate
        else:
            truncated = True
    accepted_messages: List[dict] = []
    for message in reversed(context.recent_messages):
        candidate_messages = [
            {"message_id": message.message_id, "role": message.role, "text": message.text}
        ] + accepted_messages
        candidate = dict(payload)
        candidate["recent_messages"] = candidate_messages
        if fits(candidate):
            payload = candidate
            accepted_messages = candidate_messages
        else:
            truncated = True
            break

    text = instruction + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _PromptBuildResult(text, len(text.encode("utf-8")), truncated)


def _fallback_result(
    tasks_path: Path,
    evidence_hash: str,
    current_time: datetime,
    issues: List[NextTaskIssue],
    inference_attempted: bool,
    prompt: _PromptBuildResult,
) -> NextTaskExtractionResult:
    text = _first_unchecked_task(tasks_path)
    task: Optional[NextTask] = None
    if text is None:
        issues.append(NextTaskIssue("fallback_task_not_found"))
    else:
        task = _make_task(
            text,
            "fallback",
            "low",
            "Codex CLIで推定できなかったため、タスクリストの先頭の未完了項目を使用しました。",
            tuple(),
        )
    cache = NextTaskCacheEntry(
        evidence_hash,
        task,
        current_time + timedelta(seconds=FAILURE_CACHE_SECONDS),
        tuple(issues),
    )
    return NextTaskExtractionResult(
        task,
        tuple(issues),
        cache,
        inference_attempted,
        prompt.byte_count,
        prompt.truncated,
    )


def _first_unchecked_task(path: Path) -> Optional[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        match = _UNCHECKED_TASK.match(line)
        if match is not None:
            return match.group(1).strip()
    return None


def _make_task(
    text: str,
    origin: str,
    confidence: str,
    reason: Optional[str],
    source_message_ids: Tuple[str, ...],
) -> NextTask:
    identity = "\0".join((origin, text, *source_message_ids))
    task_id = "task_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return NextTask(
        task_id,
        text,
        "pending",
        origin,
        confidence,
        reason,
        source_message_ids,
    )


def _evidence_hash(context: NextTaskInferenceContext) -> str:
    value = {
        "current_status": context.current_status,
        "recent_messages": [
            {"message_id": item.message_id, "role": item.role, "text": item.text}
            for item in context.recent_messages
        ],
        "decisions": list(context.decisions),
        "incomplete_tasks": list(context.incomplete_tasks),
        "is_masked": context.is_masked,
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_is_usable(
    entry: Optional[NextTaskCacheEntry], evidence_hash: str, now: datetime
) -> bool:
    if entry is None or entry.evidence_hash != evidence_hash:
        return False
    return entry.expires_at is None or now < entry.expires_at


def _runner_error_kind(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return "codex_timeout"
    text = str(error)
    known = {
        "codex_not_found",
        "codex_nonzero_exit",
        "codex_invalid_json",
        "codex_invalid_result",
    }
    return text if text in known else "codex_execution_error"
