"""Eligibility rules and isolated runner for one-call combined turn inference."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple


@dataclass(frozen=True)
class CombinedTurnEligibility:
    eligible: bool
    fallback_reason: Optional[str]


@dataclass(frozen=True)
class CombinedInferenceResult:
    summary: dict
    decisions: Tuple[dict, ...]
    next_task: dict


def assess_combined_turn(
    turn_status: str,
    turn_id: str,
    eligible_turn_ids: Optional[Iterable[str]],
    message_turn_ids: Iterable[Optional[str]],
    inputs_masked: bool,
    has_next_task_candidate: bool,
) -> CombinedTurnEligibility:
    if turn_status == "in_progress":
        return CombinedTurnEligibility(False, "turn_in_progress")
    if eligible_turn_ids is not None and turn_id not in set(eligible_turn_ids):
        return CombinedTurnEligibility(False, "turn_not_incremental")
    if not inputs_masked:
        return CombinedTurnEligibility(False, "inference_input_not_masked")
    if not has_next_task_candidate:
        return CombinedTurnEligibility(False, "next_task_context_missing")
    if any(value not in (None, turn_id) for value in message_turn_ids):
        return CombinedTurnEligibility(False, "cross_turn_context")
    return CombinedTurnEligibility(True, None)


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
