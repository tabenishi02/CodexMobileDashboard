"""Eligibility rules and isolated runner for one-call combined turn inference."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Tuple

from tools.inference_metrics import record


COMBINED_INFERENCE_INPUT_MAX_BYTES = 64 * 1024
_RULE_BASED_NONCHANGE_MAX_CHARACTERS = 120
_RULE_BASED_NONCHANGE_RESPONSES = frozenset(
    {
        "了解しました",
        "承知しました",
        "確認します",
        "調査します",
        "待機します",
        "ありがとうございます",
        "どういたしまして",
        "問題ありません",
        "お願いします",
        "続けてください",
    }
)



@dataclass(frozen=True)
class CombinedTurnPromptMessage:
    message_id: str
    turn_id: Optional[str]
    role: str
    message_type: str
    phase: Optional[str]
    masked_text: str
    is_masked: bool


@dataclass(frozen=True)
class CombinedTurnPrompt:
    text: str
    byte_count: int
    truncated: bool
    source_message_ids: Tuple[str, ...]

@dataclass(frozen=True)
class CombinedTurnEligibility:
    eligible: bool
    fallback_reason: Optional[str]


@dataclass(frozen=True)
class CombinedInferenceResult:
    summary: dict
    decisions: Tuple[dict, ...]
    next_task: dict


@dataclass(frozen=True)
class CombinedTurnConversion:
    """Combined CLI output expressed through the existing individual data models."""

    summary: object
    decision_proposals: Tuple[object, ...]
    next_task_cache_entry: object


def convert_combined_result(
    result: CombinedInferenceResult,
    *,
    session_id: str,
    turn_id: str,
    turn_id_source: str,
    turn_status: str,
    rolled_back: bool,
    source_message_ids: Sequence[str],
    input_sha256: str,
) -> CombinedTurnConversion:
    """Convert a validated combined result without inventing source evidence."""
    from tools.change_summary_generator import ChangeSummary
    from tools.decision_extractor import _Proposal
    from tools.next_task_extractor import NextTask, NextTaskCacheEntry

    source_ids = tuple(dict.fromkeys(source_message_ids))
    if not source_ids:
        raise ValueError("combined_turn_source_messages_missing")
    if len(input_sha256) != 64:
        raise ValueError("combined_turn_input_sha256_invalid")
    summary_value = result.summary
    identity = "\0".join((session_id, turn_id, *source_ids))
    summary = ChangeSummary(
        "change_summary_" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        turn_id,
        turn_id_source,
        turn_status,
        rolled_back,
        summary_value["title"],
        summary_value["short_summary"],
        summary_value["details"],
        tuple(),
        tuple(),
        "codex_generated",
        summary_value["confidence"],
        (session_id,),
        source_ids,
    )
    proposals = tuple(
        _Proposal(
            item["status"], item["title"], item["description"], item["reason"], item["topic_key"]
        )
        for item in result.decisions
    )
    task_value = result.next_task
    task_identity = "\0".join(("codex_inferred", task_value["task"], *source_ids))
    task = NextTask(
        "task_" + hashlib.sha256(task_identity.encode("utf-8")).hexdigest(),
        task_value["task"],
        "pending",
        "codex_inferred",
        task_value["confidence"],
        task_value["reason"],
        source_ids,
    )
    return CombinedTurnConversion(
        summary,
        proposals,
        NextTaskCacheEntry(input_sha256, task, None, tuple()),
    )


def save_combined_turn(
    ledger_path: Path,
    *,
    workspace_id: str,
    session_id: str,
    turn_id: str,
    input_sha256: str,
    conversion: CombinedTurnConversion,
    generated_at: Optional[str] = None,
):
    """Atomically save all three converted outputs as one `combined_turn` entry."""
    from tools.inference_ledger import InferenceLedgerEntry, append, combined_turn_payload
    from tools.inference_metrics import record

    entry = InferenceLedgerEntry(
        workspace_id,
        session_id,
        turn_id,
        input_sha256,
        combined_turn_payload(
            conversion.summary,
            conversion.decision_proposals,
            conversion.next_task_cache_entry,
        ),
        generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "combined_turn",
    )
    return append(ledger_path, entry)

def build_combined_turn_prompt(
    turn_id: str,
    turn_status: str,
    rolled_back: bool,
    messages: Sequence[CombinedTurnPromptMessage],
    git_changes: object,
    file_references: Sequence[object],
    *,
    input_max_bytes: int = COMBINED_INFERENCE_INPUT_MAX_BYTES,
) -> CombinedTurnPrompt:
    """Build a bounded prompt from one turn and metadata only, never diff bodies."""
    if input_max_bytes <= 0:
        raise ValueError("combined_turn_input_limit_invalid")
    target_messages = tuple(message for message in messages if message.turn_id == turn_id)
    if any(not message.is_masked for message in target_messages):
        raise ValueError("combined_turn_input_not_masked")
    instruction = (
        "次のマスク済みの単一Codexターン、Gitメタデータ、ファイル参照だけを根拠に、"
        "変更要約・ユーザー確定の決定事項・次タスクを生成してください。"
        "Git差分本文、プロジェクトファイル、外部情報を参照せず、入力にない事実を補わないでください。"
        "決定事項はユーザーが明示的に確定したものだけを返してください。"
        "summary、decisions、next_taskをJSONで返してください。\n"
    )
    payload = {
        "turn": {"turn_id": turn_id, "status": turn_status, "rolled_back": rolled_back},
        "messages": [],
        "git": _git_metadata(git_changes),
        "file_references": [],
    }
    truncated = False
    accepted_ids = []
    for message in target_messages:
        item = {
            "message_id": message.message_id,
            "role": message.role,
            "message_type": message.message_type,
            "phase": message.phase,
            "text": message.masked_text,
        }
        accepted, shortened = _append_prompt_item(instruction, payload, "messages", item, input_max_bytes)
        if accepted is None:
            truncated = True
            break
        payload = accepted
        accepted_ids.append(message.message_id)
        truncated = truncated or shortened
        if shortened:
            break
    for item in _git_file_metadata(git_changes):
        candidate = dict(payload)
        git_value = dict(payload["git"])
        git_value["files"] = list(git_value["files"]) + [item]
        candidate["git"] = git_value
        if _prompt_size(instruction, candidate) > input_max_bytes:
            truncated = True
            break
        payload = candidate
    accepted_set = set(accepted_ids)
    for reference in file_references:
        source_ids = tuple(getattr(reference, "source_message_ids", tuple()))
        if not accepted_set.intersection(source_ids):
            continue
        item = {
            "path": getattr(reference, "path", None),
            "display_name": getattr(reference, "display_name", ""),
            "scope": getattr(reference, "scope", ""),
            "source_message_ids": [value for value in source_ids if value in accepted_set],
        }
        accepted, _ = _append_prompt_item(instruction, payload, "file_references", item, input_max_bytes)
        if accepted is None:
            truncated = True
            break
        payload = accepted
    rendered = instruction + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return CombinedTurnPrompt(rendered, len(rendered.encode("utf-8")), truncated, tuple(accepted_ids))


def _git_metadata(git_changes: object) -> dict:
    repository = getattr(git_changes, "repository", None)
    return {
        "collection_status": getattr(repository, "collection_status", None),
        "branch": getattr(repository, "branch", None),
        "clean": getattr(repository, "clean", None),
        "files": [],
    }


def _git_file_metadata(git_changes: object):
    for change in getattr(git_changes, "files", tuple()):
        status = getattr(change, "status", None)
        yield {
            "path": getattr(change, "path", None),
            "old_path": getattr(change, "old_path", None),
            "index": getattr(status, "index", None),
            "worktree": getattr(status, "worktree", None),
            "committed_in_session": getattr(status, "committed_in_session", None),
        }


def _append_prompt_item(instruction, payload, key, item, input_max_bytes):
    candidate = dict(payload)
    candidate[key] = list(payload[key]) + [item]
    if _prompt_size(instruction, candidate) <= input_max_bytes:
        return candidate, False
    text = item.get("text")
    if not isinstance(text, str):
        return None, False
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        shortened = dict(item)
        shortened["text"] = text[:middle] + "…"
        candidate = dict(payload)
        candidate[key] = list(payload[key]) + [shortened]
        if _prompt_size(instruction, candidate) <= input_max_bytes:
            low = middle
        else:
            high = middle - 1
    if low == 0:
        return None, False
    shortened = dict(item)
    shortened["text"] = text[:low] + "…"
    candidate = dict(payload)
    candidate[key] = list(payload[key]) + [shortened]
    return candidate, True


def _prompt_size(instruction, payload):
    return len((instruction + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))).encode("utf-8"))

def assess_rule_based_skip(
    turn_id: str,
    messages: Sequence[CombinedTurnPromptMessage],
    *,
    has_git_changes: bool,
    has_file_references: bool,
) -> Optional[str]:
    """Return a skip reason only for unequivocal short non-change turns."""
    target_messages = tuple(message for message in messages if message.turn_id == turn_id)
    texts = tuple(_normalized_short_text(message.masked_text) for message in target_messages)
    if (
        has_git_changes
        or has_file_references
        or not texts
        or sum(len(value) for value in texts) > _RULE_BASED_NONCHANGE_MAX_CHARACTERS
        or any(not value or value not in _RULE_BASED_NONCHANGE_RESPONSES for value in texts)
    ):
        return None
    return "rule_based_sufficient"


def _normalized_short_text(text: str) -> str:
    return re.sub(r"[\s。！？!?、,]+", "", text).strip()

def assess_combined_turn(
    turn_status: str,
    turn_id: str,
    eligible_turn_ids: Optional[Iterable[str]],
    message_turn_ids: Iterable[Optional[str]],
    inputs_masked: bool,
    has_next_task_candidate: bool,
    *,
    rule_based_sufficient: bool = False,
) -> CombinedTurnEligibility:
    if turn_status == "in_progress":
        return CombinedTurnEligibility(False, "turn_in_progress")
    if eligible_turn_ids is not None and turn_id not in set(eligible_turn_ids):
        return CombinedTurnEligibility(False, "turn_not_incremental")
    if not inputs_masked:
        return CombinedTurnEligibility(False, "inference_input_not_masked")
    if rule_based_sufficient:
        return CombinedTurnEligibility(False, "rule_based_sufficient")
    if not has_next_task_candidate:
        return CombinedTurnEligibility(False, "next_task_context_missing")
    if any(value not in (None, turn_id) for value in message_turn_ids):
        return CombinedTurnEligibility(False, "cross_turn_context")
    return CombinedTurnEligibility(True, None)


@dataclass(frozen=True)
class CombinedTurnExecution:
    conversion: Optional[CombinedTurnConversion]
    used_individual_fallback: bool
    fallback_reason: Optional[str]
    skipped_by_rule: bool = False


def execute_combined_turn(
    eligibility: CombinedTurnEligibility,
    prompt: str,
    *,
    runner: "CombinedTurnCliRunner",
    convert: Callable[[CombinedInferenceResult], CombinedTurnConversion],
    fallback_to_individual: Callable[[str], None],
) -> CombinedTurnExecution:
    """Execute one combined CLI call or delegate once to the individual path."""
    if not eligibility.eligible:
        reason = eligibility.fallback_reason or "combined_turn_ineligible"
        if reason == "rule_based_sufficient":
            return CombinedTurnExecution(None, False, reason, True)
        fallback_to_individual(reason)
        return CombinedTurnExecution(None, True, reason)
    try:
        record("combined_turn", "execution", len(prompt.encode("utf-8")))
        conversion = convert(runner.infer(prompt))
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        reason = "combined_inference_failed"
        record("combined_turn", "failure", len(prompt.encode("utf-8")))
        fallback_to_individual(reason)
        return CombinedTurnExecution(None, True, reason)
    return CombinedTurnExecution(conversion, False, None)

class CombinedTurnCliRunner:
    """Run one isolated structured Codex CLI inference for a completed turn."""

    def __init__(
        self,
        executable: Optional[str] = None,
        timeout_seconds: int = 120,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._cache: dict[str, CombinedInferenceResult] = {}

    def infer(self, prompt: str) -> CombinedInferenceResult:
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        executable = self._executable or shutil.which("codex")
        if executable is None:
            raise RuntimeError("codex_not_found")
        with tempfile.TemporaryDirectory(prefix="cmd-combined-turn-") as directory:
            schema_path = Path(directory) / "combined-turn.schema.json"
            schema_path.write_text(
                json.dumps(combined_turn_schema(), ensure_ascii=False), encoding="utf-8"
            )
            completed = subprocess.run(
                [
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
                ],
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
        result = _validate_cli_value(value)
        self._cache[cache_key] = result
        return result


def combined_turn_schema() -> dict:
    text = {"type": "string", "minLength": 1}
    confidence = {"enum": ["high", "medium", "low"]}
    decision = {
        "type": "object",
        "properties": {
            "status": {"enum": ["adopted", "rejected"]},
            "title": text,
            "description": text,
            "reason": {"type": ["string", "null"]},
            "topic_key": text,
        },
        "required": ["status", "title", "description", "reason", "topic_key"],
        "additionalProperties": False,
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "summary": {
                "type": "object",
                "properties": {
                    "title": text,
                    "short_summary": text,
                    "details": text,
                    "confidence": confidence,
                },
                "required": ["title", "short_summary", "details", "confidence"],
                "additionalProperties": False,
            },
            "decisions": {"type": "array", "items": decision},
            "next_task": {
                "type": "object",
                "properties": {
                    "task": text,
                    "reason": text,
                    "confidence": confidence,
                },
                "required": ["task", "reason", "confidence"],
                "additionalProperties": False,
            },
        },
        "required": ["summary", "decisions", "next_task"],
        "additionalProperties": False,
    }


def _validate_cli_value(value: object) -> CombinedInferenceResult:
    if not isinstance(value, dict) or set(value) != {"summary", "decisions", "next_task"}:
        raise RuntimeError("codex_invalid_result")
    summary = value["summary"]
    next_task = value["next_task"]
    decisions = value["decisions"]
    if not isinstance(summary, dict) or not isinstance(next_task, dict) or not isinstance(decisions, list):
        raise RuntimeError("codex_invalid_result")
    _validate_summary(summary)
    _validate_next_task(next_task)
    for decision in decisions:
        _validate_decision(decision)
    return CombinedInferenceResult(summary, tuple(decisions), next_task)


def _validate_summary(summary: dict) -> None:
    if set(summary) != {"title", "short_summary", "details", "confidence"}:
        raise RuntimeError("codex_invalid_result")
    _require_non_empty_strings(summary, ("title", "short_summary", "details"))
    _require_confidence(summary)


def _validate_next_task(next_task: dict) -> None:
    if set(next_task) != {"task", "reason", "confidence"}:
        raise RuntimeError("codex_invalid_result")
    _require_non_empty_strings(next_task, ("task", "reason"))
    _require_confidence(next_task)


def _validate_decision(decision: object) -> None:
    if not isinstance(decision, dict):
        raise RuntimeError("codex_invalid_result")
    if set(decision) != {"status", "title", "description", "reason", "topic_key"}:
        raise RuntimeError("codex_invalid_result")
    if decision["status"] not in {"adopted", "rejected"}:
        raise RuntimeError("codex_invalid_result")
    _require_non_empty_strings(decision, ("title", "description", "topic_key"))
    if decision["reason"] is not None and not isinstance(decision["reason"], str):
        raise RuntimeError("codex_invalid_result")


def _require_non_empty_strings(value: dict, keys: Tuple[str, ...]) -> None:
    if any(not isinstance(value[key], str) or not value[key] for key in keys):
        raise RuntimeError("codex_invalid_result")


def _require_confidence(value: dict) -> None:
    if value["confidence"] not in {"high", "medium", "low"}:
        raise RuntimeError("codex_invalid_result")
