import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import patch

from tools.chat_extractor import ChatContentPart, ExtractedChatMessage
from tools.decision_extractor import (
    DecisionCliRunner,
    DecisionInferenceCacheEntry,
    DecisionSourceMessage,
    ExtractedDecision,
    _Proposal,
    apply_inferred_decision_proposals,
    decision_inference_cache_entry_from_payload,
    decision_inference_payload,
    extract_decisions,
    inference_payload,
    proposals_from_inference_payload,
)


def chat_message(
    role: str,
    text: str,
    message_id: str,
    *,
    created_at: Optional[str] = "2026-08-08T01:00:00+00:00",
    path: Path = Path("rollout-test.jsonl"),
) -> ExtractedChatMessage:
    return ExtractedChatMessage(
        message_id=message_id,
        source_message_id=message_id,
        sequence=1,
        created_at=created_at,
        role=role,
        message_type="chat",
        phase="final_answer" if role == "assistant" else None,
        turn_id="turn-1",
        content=(ChatContentPart("text", text),),
        display_mode="expanded",
        duplicate_of=None,
        occurrence_count=1,
        removed_automatic_contexts=tuple(),
        source_path=path,
        source_line_number=1,
        source_start_offset=10,
    )


def source(
    session_id: str,
    role: str,
    text: str,
    message_id: str,
    *,
    masked: bool = False,
) -> DecisionSourceMessage:
    return DecisionSourceMessage(
        session_id,
        chat_message(role, text, message_id),
        text if masked else None,
    )


class StubRunner:
    def __init__(
        self,
        proposals: Tuple[_Proposal, ...] = tuple(),
        error: Optional[BaseException] = None,
    ) -> None:
        self.proposals = proposals
        self.error = error
        self.calls = 0
        self.prompt = ""

    def extract(self, prompt: str) -> Tuple[_Proposal, ...]:
        self.calls += 1
        self.prompt = prompt
        if self.error is not None:
            raise self.error
        return self.proposals


class DecisionExtractorTests(unittest.TestCase):
    def test_combined_proposals_preserve_supersedes_relationships(self) -> None:
        first = apply_inferred_decision_proposals(
            (_Proposal("adopted", "案A", "説明A", None, "topic"),),
            "workspace-1",
            "session-1",
            chat_message("assistant", "案A", "message-1"),
            ("message-1",),
        )
        second = apply_inferred_decision_proposals(
            (_Proposal("adopted", "案B", "説明B", None, "topic"),),
            "workspace-1",
            "session-1",
            chat_message("assistant", "案B", "message-2"),
            ("message-2",),
            restored_decisions=first.decisions,
        )

        self.assertEqual("superseded", second.decisions[0].status)
        self.assertEqual(
            second.decisions[1].decision_id, second.decisions[0].superseded_by
        )
        self.assertEqual(
            second.decisions[0].decision_id, second.decisions[1].supersedes
        )

    def test_assistant_proposal_alone_is_not_a_decision(self) -> None:
        result = extract_decisions(
            (source("session-1", "assistant", "HTTPSを採用します。", "msg-a"),),
            "workspace-1",
        )

        self.assertEqual(tuple(), result.decisions)

    def test_self_contained_user_decision_is_extracted_locally(self) -> None:
        runner = StubRunner()
        result = extract_decisions(
            (
                source(
                    "session-1",
                    "user",
                    "通信方式はHTTPSとします。",
                    "msg-u",
                ),
            ),
            "workspace-1",
            runner=runner,
        )

        self.assertEqual(0, runner.calls)
        decision = result.decisions[0]
        self.assertEqual("adopted", decision.status)
        self.assertEqual("通信方式", decision.topic_key)
        self.assertIn("HTTPS", decision.description)
        self.assertEqual(("session-1",), decision.source_session_ids)
        self.assertEqual(("msg-u",), decision.source_message_ids)

    def test_one_message_is_split_into_independent_decisions(self) -> None:
        result = extract_decisions(
            (
                source(
                    "session-1",
                    "user",
                    "通信方式はHTTPSとします。タイムアウトは120秒とします。",
                    "msg-u",
                ),
            ),
            "workspace-1",
            runner=StubRunner(),
        )

        self.assertEqual(2, len(result.decisions))
        self.assertEqual({"通信方式", "タイムアウト"}, {d.topic_key for d in result.decisions})

    def test_same_decision_across_sessions_merges_sources(self) -> None:
        result = extract_decisions(
            (
                source("session-1", "user", "通信方式はHTTPSとします。", "msg-1"),
                source("session-2", "user", "通信方式はHTTPSとします。", "msg-2"),
            ),
            "workspace-1",
            runner=StubRunner(),
        )

        self.assertEqual(1, len(result.decisions))
        self.assertEqual(("session-1", "session-2"), result.decisions[0].source_session_ids)
        self.assertEqual(("msg-1", "msg-2"), result.decisions[0].source_message_ids)

    def test_changed_topic_supersedes_prior_decision_across_sessions(self) -> None:
        result = extract_decisions(
            (
                source("session-1", "user", "タイムアウトは60秒とします。", "msg-1"),
                source("session-2", "user", "タイムアウトは120秒とします。", "msg-2"),
            ),
            "workspace-1",
            runner=StubRunner(),
        )

        old, new = result.decisions
        self.assertEqual("superseded", old.status)
        self.assertEqual(new.decision_id, old.superseded_by)
        self.assertEqual(old.decision_id, new.supersedes)
        self.assertEqual("adopted", new.status)

    def test_reverting_to_old_content_creates_new_history_entry(self) -> None:
        result = extract_decisions(
            (
                source("session-1", "user", "タイムアウトは60秒とします。", "msg-1"),
                source("session-2", "user", "タイムアウトは120秒とします。", "msg-2"),
                source("session-3", "user", "タイムアウトは60秒とします。", "msg-3"),
            ),
            "workspace-1",
            runner=StubRunner(),
        )

        self.assertEqual(3, len(result.decisions))
        self.assertEqual("superseded", result.decisions[1].status)
        self.assertEqual(result.decisions[1].decision_id, result.decisions[2].supersedes)
        self.assertEqual("adopted", result.decisions[2].status)

    def test_rejection_is_a_decision(self) -> None:
        result = extract_decisions(
            (source("session-1", "user", "平文HTTPは不要です。", "msg-u"),),
            "workspace-1",
            runner=StubRunner(),
        )

        self.assertEqual("rejected", result.decisions[0].status)

    def test_reason_is_only_taken_from_explicit_sentence(self) -> None:
        result = extract_decisions(
            (
                source(
                    "session-1",
                    "user",
                    "通信を暗号化するため、通信方式はHTTPSとします。",
                    "msg-u",
                ),
            ),
            "workspace-1",
            runner=StubRunner(),
        )

        self.assertEqual("通信を暗号化するため", result.decisions[0].reason)

    def test_reconfirmation_can_add_previously_missing_reason(self) -> None:
        result = extract_decisions(
            (
                source("session-1", "user", "通信方式はHTTPSとします。", "msg-1"),
                source(
                    "session-1",
                    "user",
                    "通信を暗号化するため、通信方式はHTTPSとします。",
                    "msg-2",
                ),
            ),
            "workspace-1",
            runner=StubRunner(),
        )

        self.assertEqual(1, len(result.decisions))
        self.assertEqual("通信を暗号化するため", result.decisions[0].reason)

    def test_ambiguous_reference_uses_masked_cli_context(self) -> None:
        runner = StubRunner(
            (
                _Proposal(
                    "adopted",
                    "通信方式をHTTPSにする",
                    "通常運用ではHTTPSを必須にします。",
                    None,
                    "通信方式",
                ),
                _Proposal(
                    "adopted",
                    "タイムアウトを120秒にする",
                    "推定処理のタイムアウトを120秒にします。",
                    "処理完了を待つため",
                    "タイムアウト",
                ),
            )
        )
        sources = (
            source(
                "session-1",
                "assistant",
                "HTTPSと120秒を推奨します。",
                "msg-a",
                masked=True,
            ),
            source(
                "session-1",
                "user",
                "あなたの提案をそのまま採用します。",
                "msg-u",
                masked=True,
            ),
        )

        result = extract_decisions(sources, "workspace-1", runner=runner)

        self.assertEqual(1, runner.calls)
        self.assertEqual(2, len(result.decisions))
        payload = json.loads(runner.prompt.split("\n", 1)[1])
        self.assertEqual(["assistant", "user"], [item["role"] for item in payload["messages"]])

    def test_cli_receives_masked_text_instead_of_raw_messages(self) -> None:
        runner = StubRunner()
        assistant = DecisionSourceMessage(
            "session-1",
            chat_message("assistant", "token=raw-secretの案Aです。", "msg-a"),
            "token=[REDACTED:TOKEN]の案Aです。",
        )
        user = DecisionSourceMessage(
            "session-1",
            chat_message("user", "token=raw-secretの案Aを採用します。", "msg-u"),
            "token=[REDACTED:TOKEN]の案Aを採用します。",
        )

        extract_decisions((assistant, user), "workspace-1", runner=runner)

        self.assertNotIn("raw-secret", runner.prompt)
        self.assertIn("[REDACTED:TOKEN]", runner.prompt)

    def test_ambiguous_reference_without_masked_context_is_deferred(self) -> None:
        runner = StubRunner()
        sources = (
            source("session-1", "assistant", "案Aを推奨します。", "msg-a"),
            source("session-1", "user", "案Aを採用します。", "msg-u"),
        )

        result = extract_decisions(sources, "workspace-1", runner=runner)

        self.assertEqual(tuple(), result.decisions)
        self.assertEqual("inference_input_not_masked", result.issues[0].kind)
        self.assertEqual(0, runner.calls)

    def test_cli_failure_is_reported_without_inventing_decision(self) -> None:
        runner = StubRunner(error=subprocess.TimeoutExpired(["codex"], 120))
        sources = (
            source("session-1", "assistant", "案Aを推奨します。", "msg-a", masked=True),
            source("session-1", "user", "案Aを採用します。", "msg-u", masked=True),
        )

        result = extract_decisions(sources, "workspace-1", runner=runner)

        self.assertEqual(tuple(), result.decisions)
        self.assertEqual("codex_timeout", result.issues[0].kind)
        self.assertEqual(1, runner.calls)

    def test_shared_limit_prevents_decision_cli_start(self) -> None:
        runner = StubRunner(
            (_Proposal("adopted", "title", "description", None, "topic"),)
        )
        sources = (
            source("session-1", "assistant", "案Aを推奨します。", "msg-a", masked=True),
            source("session-1", "user", "案Aを採用します。", "msg-u", masked=True),
        )

        result = extract_decisions(
            sources,
            "workspace-1",
            runner=runner,
            can_infer=lambda: False,
        )

        self.assertEqual(0, runner.calls)
        self.assertEqual(tuple(), result.decisions)
        self.assertEqual("inference_limit_reached", result.issues[0].kind)

    def test_missing_timestamp_uses_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            message = chat_message(
                "user",
                "通信方式はHTTPSとします。",
                "msg-u",
                created_at=None,
                path=path,
            )
            result = extract_decisions(
                (DecisionSourceMessage("session-1", message),),
                "workspace-1",
                runner=StubRunner(),
            )

        self.assertEqual("file_mtime", result.decisions[0].decided_at_source)
        self.assertIsNotNone(result.decisions[0].decided_at)

    @patch("tools.decision_extractor.subprocess.run")
    def test_cli_runner_uses_isolated_supported_flags(self, run: object) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "decisions": [
                    {
                        "status": "adopted",
                        "title": "HTTPSを採用",
                        "description": "HTTPSを採用します。",
                        "reason": None,
                        "topic_key": "通信方式",
                    }
                ]
            },
            ensure_ascii=False,
        )
        runner = DecisionCliRunner(executable="codex.exe")

        result = runner.extract("マスク済み入力")

        self.assertEqual(1, len(result))
        command = run.call_args.args[0]
        self.assertIn("--ephemeral", command)
        self.assertIn("read-only", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual("-", command[-1])
        self.assertFalse(run.call_args.kwargs["shell"])

        cached = runner.extract("マスク済み入力")
        self.assertEqual(result, cached)
        self.assertEqual(1, run.call_count)


    def test_inference_payload_round_trip_is_versioned(self) -> None:
        proposals = (_Proposal("adopted", "title", "description", None, "topic"),)
        restored = proposals_from_inference_payload(inference_payload(proposals))
        self.assertEqual(proposals, restored)
        with self.assertRaises(ValueError):
            proposals_from_inference_payload({"payload": {"proposals": []}})

    def test_complete_inference_payload_restores_relationships_and_evidence(self) -> None:
        proposals = (_Proposal("adopted", "new", "new description", "reason", "topic"),)
        old = ExtractedDecision("old", "2026-08-08T01:00:00+00:00", "created_at", "superseded", "old", "old description", None, ("session-1",), ("message-1",), None, "new", "topic")
        new = ExtractedDecision("new", "2026-08-08T02:00:00+00:00", "created_at", "adopted", "new", "new description", "reason", ("session-2",), ("message-2",), "old", None, "topic")
        entry = DecisionInferenceCacheEntry("session-2", "turn-2", "a" * 64, proposals, (old, new))

        restored = decision_inference_cache_entry_from_payload(
            decision_inference_payload(entry), "session-2", "turn-2", "a" * 64
        )

        self.assertEqual(entry, restored)
        self.assertEqual(new.decision_id, restored.decisions[0].superseded_by)
        self.assertEqual(old.decision_id, restored.decisions[1].supersedes)
        self.assertEqual(("message-2",), restored.decisions[1].source_message_ids)
        result = extract_decisions(
            tuple(), "workspace-1", restored_decisions=restored.decisions
        )
        self.assertEqual((old, new), result.decisions)
        invalid = decision_inference_payload(entry)
        invalid["payload"]["decisions"][0]["superseded_by"] = "missing"
        with self.assertRaises(ValueError):
            decision_inference_cache_entry_from_payload(
                invalid, "session-2", "turn-2", "a" * 64
            )

    def test_inference_success_captures_relationships_after_decision_update(self) -> None:
        sources = (
            source("session-1", "user", "通信方式はHTTPとします。", "msg-old", masked=True),
            source("session-1", "assistant", "HTTPSを推奨します。", "msg-a", masked=True),
            source("session-1", "user", "その提案を採用します。", "msg-new", masked=True),
        )
        runner = StubRunner(
            (_Proposal("adopted", "HTTPS", "HTTPSを採用します。", None, "通信方式"),)
        )
        saved = []

        result = extract_decisions(
            sources,
            "workspace-1",
            runner=runner,
            on_inference_success=saved.append,
        )

        self.assertEqual(1, len(saved))
        self.assertEqual(result.decisions, saved[0].decisions)
        self.assertEqual(saved[0].decisions[1].decision_id, saved[0].decisions[0].superseded_by)
        self.assertEqual(saved[0].decisions[0].decision_id, saved[0].decisions[1].supersedes)

    def test_cache_requires_exact_session_turn_and_input_sha(self) -> None:
        proposals = (_Proposal("adopted", "title", "description", None, "topic"),)
        sources = (
            source("session-1", "assistant", "案Aを推奨します。", "msg-a", masked=True),
            source("session-1", "user", "案Aを採用します。", "msg-u", masked=True),
        )
        saved = []
        first_runner = StubRunner(proposals)
        extract_decisions(
            sources,
            "workspace-1",
            runner=first_runner,
            on_inference_success=saved.append,
        )
        cache = {
            (saved[0].session_id, saved[0].turn_id, saved[0].input_sha256): saved[0]
        }

        exact_runner = StubRunner(proposals)
        exact = extract_decisions(
            sources, "workspace-1", runner=exact_runner, inference_cache=cache
        )
        changed_runner = StubRunner(proposals)
        changed_sources = (
            source("session-1", "assistant", "案Bを推奨します。", "msg-a", masked=True),
            source("session-1", "user", "案Aを採用します。", "msg-u", masked=True),
        )
        extract_decisions(
            changed_sources,
            "workspace-1",
            runner=changed_runner,
            inference_cache=cache,
        )
        wrong_session_runner = StubRunner(proposals)
        wrong_session_sources = (
            source("session-2", "assistant", "案Aを推奨します。", "msg-a", masked=True),
            source("session-2", "user", "案Aを採用します。", "msg-u", masked=True),
        )
        extract_decisions(
            wrong_session_sources,
            "workspace-1",
            runner=wrong_session_runner,
            inference_cache=cache,
        )
        wrong_turn_runner = StubRunner(proposals)
        wrong_turn_cache = {
            (saved[0].session_id, "other-turn", saved[0].input_sha256): saved[0]
        }
        extract_decisions(
            sources,
            "workspace-1",
            runner=wrong_turn_runner,
            inference_cache=wrong_turn_cache,
        )

        self.assertEqual(0, exact_runner.calls)
        self.assertEqual(saved[0].decisions, exact.decisions)
        self.assertEqual(1, changed_runner.calls)
        self.assertEqual(1, wrong_session_runner.calls)
        self.assertEqual(1, wrong_turn_runner.calls)

if __name__ == "__main__":
    unittest.main()
