"""Extract project decisions from user-confirmed Codex conversations."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from tools.chat_extractor import ExtractedChatMessage
from tools.next_task_extractor import INFERENCE_INPUT_MAX_BYTES, INFERENCE_TIMEOUT_SECONDS


LOGGER = logging.getLogger("converter")
DECISION_TITLE_MAX_CHARACTERS = 120

_AMBIGUOUS_REFERENCE = re.compile(
    r"案\s*[A-Za-z0-9一二三四五六七八九十]+|提案|推奨|そのまま|それ以外|"
    r"こちら|上記|前述|先ほど|問題ありません"
)
_DECISION_TRIGGER = re.compile(
    r"採用|とします|にします|必須|使用します|使います|取り下げ|採用しません|"
    r"不要です|対応しません|固定します|変更します"
)
_REASON_PREFIX = re.compile(r"^(?P<reason>.+?(?:ため|ので))[、,]\s*(?P<body>.+)$")
_ADOPT_SUBJECT = re.compile(
    r"^(?P<topic>[^。！？!?：:]{1,100}?)(?:は|については)\s*"
    r"(?P<choice>.+?)(?:を採用します|とします|にします|を使用します|を使います|"
    r"を必須とします|で固定します)$"
)
_ADOPT_CHOICE = re.compile(
    r"^(?P<choice>.+?)(?:を採用します|とします|を使用します|を使います|"
    r"を必須とします|で固定します)$"
)
_REJECT = re.compile(
    r"^(?P<choice>.+?)(?:を採用しません|は採用しません|を取り下げます|"
    r"は不要です|には対応しません)$"
)


@dataclass(frozen=True)
class DecisionSourceMessage:
    session_id: str
    message: ExtractedChatMessage
    masked_text: Optional[str] = None


@dataclass(frozen=True)
class ExtractedDecision:
    decision_id: str
    decided_at: Optional[str]
    decided_at_source: str
    status: str
    title: str
    description: str
    reason: Optional[str]
    source_session_ids: Tuple[str, ...]
    source_message_ids: Tuple[str, ...]
    supersedes: Optional[str]
    superseded_by: Optional[str]
    topic_key: str


@dataclass(frozen=True)
class DecisionExtractionIssue:
    session_id: str
    message_id: str
    kind: str


@dataclass(frozen=True)
class DecisionExtractionResult:
    decisions: Tuple[ExtractedDecision, ...]
    issues: Tuple[DecisionExtractionIssue, ...]


@dataclass(frozen=True)
class _Proposal:
    status: str
    title: str
    description: str
    reason: Optional[str]
    topic_key: str


class DecisionCliRunner:
    """Resolve contextual decision references with an isolated Codex CLI."""

    def __init__(
        self,
        executable: Optional[str] = None,
        timeout_seconds: int = INFERENCE_TIMEOUT_SECONDS,
    ) -> None:
        self._executable = executable
        self._timeout_seconds = timeout_seconds
        self._cache: Dict[str, Tuple[_Proposal, ...]] = {}

    def extract(self, prompt: str) -> Tuple[_Proposal, ...]:
        cache_key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        executable = self._executable or shutil.which("codex")
        if executable is None:
            raise RuntimeError("codex_not_found")
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "status": {"enum": ["adopted", "rejected"]},
                            "title": {"type": "string", "minLength": 1},
                            "description": {"type": "string", "minLength": 1},
                            "reason": {"type": ["string", "null"]},
                            "topic_key": {"type": "string", "minLength": 1},
                        },
                        "required": [
                            "status",
                            "title",
                            "description",
                            "reason",
                            "topic_key",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["decisions"],
            "additionalProperties": False,
        }
        with tempfile.TemporaryDirectory(prefix="cmd-decisions-") as directory:
            schema_path = Path(directory) / "decisions.schema.json"
            schema_path.write_text(
                json.dumps(schema, ensure_ascii=False), encoding="utf-8"
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


def extract_decisions(
    source_messages: Iterable[DecisionSourceMessage],
    workspace_id: str,
    *,
    runner: Optional[DecisionCliRunner] = None,
    allow_inference: bool = True,
) -> DecisionExtractionResult:
    """Build workspace-wide decision history in chronological input order."""

    sources = tuple(source_messages)
    decisions: List[ExtractedDecision] = []
    active_by_topic: Dict[str, int] = {}
    same_decision: Dict[str, int] = {}
    issues: List[DecisionExtractionIssue] = []
    cli_runner = runner or DecisionCliRunner()

    for position, source in enumerate(sources):
        message = source.message
        if message.role != "user" or message.message_type != "chat":
            continue
        text = _message_text(message)
        if not _DECISION_TRIGGER.search(text):
            continue

        proposals: Tuple[_Proposal, ...]
        if _AMBIGUOUS_REFERENCE.search(text):
            if not allow_inference:
                issues.append(DecisionExtractionIssue(source.session_id, message.message_id, "inference_disabled"))
                continue
            masked_context = _masked_context(sources, position)
            if masked_context is None:
                issues.append(
                    DecisionExtractionIssue(
                        source.session_id,
                        message.message_id,
                        "inference_input_not_masked",
                    )
                )
                continue
            try:
                prompt = _build_cli_prompt(masked_context)
                proposals = cli_runner.extract(prompt)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                kind = _runner_error_kind(error)
                issues.append(
                    DecisionExtractionIssue(source.session_id, message.message_id, kind)
                )
                LOGGER.warning("決定事項のCodex CLI抽出に失敗: kind=%s", kind)
                continue
        else:
            proposals = _local_proposals(text)

        for proposal in proposals:
            decided_at, decided_at_source = _decision_time(message)
            semantic_key = _semantic_key(proposal)
            existing_index = same_decision.get(semantic_key)
            if existing_index is not None:
                existing = decisions[existing_index]
                reason_compatible = (
                    existing.reason is None
                    or proposal.reason is None
                    or _normalize(existing.reason) == _normalize(proposal.reason)
                )
                if existing.superseded_by is None and reason_compatible:
                    decisions[existing_index] = replace(
                        existing,
                        reason=existing.reason or proposal.reason,
                        source_session_ids=_merge(
                            existing.source_session_ids, (source.session_id,)
                        ),
                        source_message_ids=_merge(
                            existing.source_message_ids, (message.message_id,)
                        ),
                    )
                    continue

            prior_index = active_by_topic.get(proposal.topic_key)
            decision_id = _decision_id(
                workspace_id,
                source.session_id,
                message.message_id,
                proposal,
            )
            prior_id: Optional[str] = None
            if prior_index is not None:
                prior = decisions[prior_index]
                prior_id = prior.decision_id
                decisions[prior_index] = replace(
                    prior,
                    status="superseded",
                    superseded_by=decision_id,
                )

            decision = ExtractedDecision(
                decision_id=decision_id,
                decided_at=decided_at,
                decided_at_source=decided_at_source,
                status=proposal.status,
                title=_limit(proposal.title, DECISION_TITLE_MAX_CHARACTERS),
                description=proposal.description,
                reason=proposal.reason,
                source_session_ids=(source.session_id,),
                source_message_ids=(message.message_id,),
                supersedes=prior_id,
                superseded_by=None,
                topic_key=proposal.topic_key,
            )
            decisions.append(decision)
            new_index = len(decisions) - 1
            active_by_topic[proposal.topic_key] = new_index
            same_decision[semantic_key] = new_index

    LOGGER.info(
        "決定事項を抽出: workspace_id=%s decisions=%d warnings=%d",
        workspace_id,
        len(decisions),
        len(issues),
    )
    return DecisionExtractionResult(tuple(decisions), tuple(issues))


def _local_proposals(text: str) -> Tuple[_Proposal, ...]:
    proposals: List[_Proposal] = []
    for sentence in _sentences(text):
        reason: Optional[str] = None
        reason_match = _REASON_PREFIX.match(sentence)
        if reason_match is not None:
            reason = reason_match.group("reason").strip()
            sentence = reason_match.group("body").strip()

        rejected = _REJECT.match(sentence)
        if rejected is not None:
            choice = rejected.group("choice").strip()
            proposals.append(
                _Proposal(
                    "rejected",
                    _limit(choice, DECISION_TITLE_MAX_CHARACTERS),
                    sentence,
                    reason,
                    _topic_key(choice),
                )
            )
            continue

        adopted = _ADOPT_SUBJECT.match(sentence)
        if adopted is not None:
            topic = adopted.group("topic").strip()
            proposals.append(
                _Proposal(
                    "adopted",
                    _limit(f"{topic}: {adopted.group('choice').strip()}", DECISION_TITLE_MAX_CHARACTERS),
                    sentence,
                    reason,
                    _topic_key(topic),
                )
            )
            continue

        choice_match = _ADOPT_CHOICE.match(sentence)
        if choice_match is not None:
            choice = choice_match.group("choice").strip()
            proposals.append(
                _Proposal(
                    "adopted",
                    _limit(choice, DECISION_TITLE_MAX_CHARACTERS),
                    sentence,
                    reason,
                    _topic_key(choice),
                )
            )
    return tuple(proposals)


def _masked_context(
    sources: Sequence[DecisionSourceMessage], position: int
) -> Optional[Tuple[Tuple[str, str, str], ...]]:
    current = sources[position]
    if current.masked_text is None:
        return None
    selected: List[Tuple[str, str, str]] = [
        (current.message.message_id, current.message.role, current.masked_text)
    ]
    for source in reversed(sources[:position]):
        message = source.message
        if message.role != "assistant" or message.message_type != "chat":
            continue
        if source.masked_text is None:
            return None
        selected.insert(0, (message.message_id, message.role, source.masked_text))
        break
    return tuple(selected) if len(selected) == 2 else None


def _build_cli_prompt(
    messages: Tuple[Tuple[str, str, str], ...],
) -> str:
    instruction = (
        "ユーザーが明示的に採用、拒否、変更、撤回した決定だけを抽出してください。"
        "Codexの提案だけでは決定にせず、独立して変更可能な項目ごとに分割してください。"
        "理由は会話に明記された場合だけ設定し、なければnullにしてください。"
        "決定はユーザーの記述順で返し、topic_keyは同じ論点の更新を同じ短い識別子で表してください。\n"
    )
    payload = {
        "messages": [
            {"message_id": item[0], "role": item[1], "text": item[2]}
            for item in messages
        ]
    }
    text = instruction + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = text.encode("utf-8")
    if len(encoded) > INFERENCE_INPUT_MAX_BYTES:
        raise RuntimeError("inference_input_too_large")
    return text


def _validate_cli_value(value: object) -> Tuple[_Proposal, ...]:
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise RuntimeError("codex_invalid_result")
    proposals: List[_Proposal] = []
    for item in value["decisions"]:
        if not isinstance(item, dict):
            raise RuntimeError("codex_invalid_result")
        status = item.get("status")
        title = item.get("title")
        description = item.get("description")
        reason = item.get("reason")
        topic_key = item.get("topic_key")
        if (
            status not in ("adopted", "rejected")
            or not isinstance(title, str)
            or not title.strip()
            or not isinstance(description, str)
            or not description.strip()
            or (reason is not None and not isinstance(reason, str))
            or not isinstance(topic_key, str)
            or not topic_key.strip()
        ):
            raise RuntimeError("codex_invalid_result")
        proposals.append(
            _Proposal(
                str(status),
                title.strip(),
                description.strip(),
                reason.strip() if isinstance(reason, str) and reason.strip() else None,
                _topic_key(topic_key),
            )
        )
    return tuple(proposals)


def _sentences(text: str) -> Tuple[str, ...]:
    return tuple(
        value.strip(" \t\r\n・-*#")
        for value in re.split(r"[。！？!?]+|\r?\n", text)
        if value.strip(" \t\r\n・-*#")
    )


def _message_text(message: ExtractedChatMessage) -> str:
    return "\n".join(part.text for part in message.content if part.text).strip()


def _decision_time(message: ExtractedChatMessage) -> Tuple[Optional[str], str]:
    if message.created_at is not None:
        return message.created_at, "message_timestamp"
    try:
        modified = message.source_path.stat().st_mtime
        return datetime.fromtimestamp(modified, tz=timezone.utc).isoformat(), "file_mtime"
    except (OSError, OverflowError, ValueError):
        return None, "missing"


def _decision_id(
    workspace_id: str,
    session_id: str,
    message_id: str,
    proposal: _Proposal,
) -> str:
    value = "\0".join(
        (
            workspace_id,
            session_id,
            message_id,
            proposal.status,
            proposal.topic_key,
            proposal.description,
        )
    )
    return "decision_" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _semantic_key(proposal: _Proposal) -> str:
    value = "\0".join(
        (
            proposal.status,
            proposal.topic_key,
            _normalize(proposal.description),
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _topic_key(value: str) -> str:
    normalized = _normalize(value).replace(" ", "_")
    if len(normalized) <= 120:
        return normalized or "decision_unknown"
    suffix = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return normalized[:103] + "_" + suffix


def _normalize(value: str) -> str:
    return " ".join(value.split()).strip(" \t:：-—").casefold()


def _limit(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else value[: maximum - 1] + "…"


def _merge(first: Sequence[str], second: Sequence[str]) -> Tuple[str, ...]:
    return tuple(dict.fromkeys((*first, *second)))


def _runner_error_kind(error: BaseException) -> str:
    if isinstance(error, subprocess.TimeoutExpired):
        return "codex_timeout"
    value = str(error)
    known = {
        "codex_not_found",
        "codex_nonzero_exit",
        "codex_invalid_json",
        "codex_invalid_result",
        "inference_input_too_large",
    }
    return value if value in known else "codex_execution_error"
