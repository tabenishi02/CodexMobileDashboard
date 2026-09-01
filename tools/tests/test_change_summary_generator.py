import json
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from unittest.mock import patch

from tools.change_summary_generator import (
    ChangeSummaryCliRunner,
    GeneratedSummaryContent,
    SummaryEvidenceItem,
    SummarySourceMessage,
    generate_change_summaries,
)
from tools.work_status_extractor import CurrentWorkStatus, TurnWorkState


def source_message(
    role: str,
    text: str,
    message_id: str,
    *,
    phase: Optional[str] = None,
    message_type: str = "chat",
    masked: bool = True,
    session_id: str = "session-1",
) -> SummarySourceMessage:
    return SummarySourceMessage(
        session_id=session_id,
        message_id=message_id,
        turn_id="turn-1",
        role=role,
        message_type=message_type,
        phase=phase,
        masked_text=text,
        is_masked=masked,
    )


def turn(
    *,
    status: str = "completed",
    rolled_back: bool = False,
    turn_id_source: str = "jsonl",
) -> TurnWorkState:
    return TurnWorkState(
        turn_id="turn-1",
        turn_id_source=turn_id_source,
        status=status,
        started_at="2026-08-13T01:00:00+00:00",
        started_at_source="event_field",
        completed_at="2026-08-13T01:01:00+00:00",
        completed_at_source="event_field",
        duration_ms=60_000,
        reason=None,
        user_message_id="msg-user",
        assistant_message_ids=("msg-commentary", "msg-final"),
        rolled_back=rolled_back,
    )


def work_status(*turns: TurnWorkState) -> CurrentWorkStatus:
    latest = turns[-1] if turns else None
    return CurrentWorkStatus(
        codex_status="idle",
        current_work=None,
        current_work_message_id=None,
        active_turn_id=None,
        latest_turn_id=latest.turn_id if latest else None,
        latest_turn_status=latest.status if latest else None,
        last_event_at=None,
        turns=tuple(turns),
        issues=tuple(),
    )


class StubRunner:
    def __init__(
        self,
        result: Optional[GeneratedSummaryContent] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        self.result = result or GeneratedSummaryContent(
            title="認証機能の調査",
            short_summary="認証機能を調査しました。",
            details="認証機能の現在状態を調査し、実装前の確認事項を整理しました。",
            highlights=(SummaryEvidenceItem("確認事項を整理", ("msg-final",)),),
            verification=tuple(),
            confidence="medium",
        )
        self.error = error
        self.calls = 0
        self.prompt = ""

    def generate(self, prompt: str) -> GeneratedSummaryContent:
        self.calls += 1
        self.prompt = prompt
        if self.error is not None:
            raise self.error
        return self.result


class ChangeSummaryGeneratorTests(unittest.TestCase):
    def test_cli_success_callback_receives_complete_cache_entry_immediately(self) -> None:
        runner = StubRunner()
        saved = []
        messages = (
            source_message(
                "assistant", "確認中です。", "msg-final", phase="final_answer"
            ),
        )

        result = generate_change_summaries(
            "session-1",
            work_status(turn()),
            messages,
            runner=runner,
            on_inference_success=saved.append,
        )

        self.assertEqual(1, runner.calls)
        self.assertEqual((result.cache_entries[0],), tuple(saved))
        self.assertEqual(result.summaries[0], saved[0].summary)
        self.assertIsNone(saved[0].expires_at)
        self.assertEqual(tuple(), saved[0].issues)

    def test_explicit_final_result_has_priority_over_cli(self) -> None:
        runner = StubRunner()
        messages = (
            source_message("user", "Git取得機能を実装してください。", "msg-user"),
            source_message(
                "assistant",
                "実装を開始します。",
                "msg-commentary",
                phase="commentary",
            ),
            source_message(
                "assistant",
                "Git変更取得機能を実装し、コミットしました。\n\n"
                "- 未追跡ファイルは読み取りません\n"
                "- 全104テストが成功",
                "msg-final",
                phase="final_answer",
            ),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=runner
        )

        summary = result.summaries[0]
        self.assertEqual("explicit", summary.origin)
        self.assertEqual("jsonl", summary.turn_id_source)
        self.assertIn("Git変更取得機能", summary.short_summary)
        self.assertEqual("全104テストが成功", summary.verification[0].text)
        self.assertEqual(0, runner.calls)

    def test_vague_final_uses_prioritized_cli_context(self) -> None:
        runner = StubRunner()
        messages = (
            source_message("user", "認証機能を確認してください。", "msg-user"),
            source_message(
                "assistant",
                "調査しています。",
                "msg-commentary",
                phase="commentary",
            ),
            source_message(
                "assistant", "対応しました。", "msg-final", phase="final_answer"
            ),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=runner
        )

        self.assertEqual("codex_generated", result.summaries[0].origin)
        self.assertEqual(1, runner.calls)
        payload = json.loads(runner.prompt.split("\n", 1)[1])
        self.assertEqual("msg-final", payload["messages"][0]["message_id"])
        self.assertEqual("msg-user", payload["messages"][1]["message_id"])

    def test_user_instruction_alone_does_not_claim_completion(self) -> None:
        runner = StubRunner(error=RuntimeError("codex_nonzero_exit"))
        messages = (
            source_message("user", "認証機能を実装してください。", "msg-user"),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=runner
        )

        self.assertEqual(tuple(), result.summaries)
        self.assertIn("codex_nonzero_exit", [issue.kind for issue in result.issues])

    def test_developer_instruction_is_not_summary_evidence(self) -> None:
        runner = StubRunner(error=RuntimeError("codex_nonzero_exit"))
        messages = (
            source_message(
                "developer",
                "認証機能を実装しました。",
                "msg-final",
                message_type="developer_instruction",
            ),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=runner
        )

        self.assertEqual(tuple(), result.summaries)
        self.assertEqual(0, runner.calls)
        self.assertIn("summary_evidence_missing", [item.kind for item in result.issues])

    def test_code_block_body_is_not_sent_to_cli(self) -> None:
        runner = StubRunner()
        messages = (
            source_message(
                "assistant",
                "確認中です。\n```text\nSECRET_CODE_BODY\n```",
                "msg-final",
                phase="final_answer",
            ),
        )

        generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=runner
        )

        self.assertNotIn("SECRET_CODE_BODY", runner.prompt)

    def test_unmasked_input_never_uses_local_or_cli_summary(self) -> None:
        runner = StubRunner()
        messages = (
            source_message(
                "assistant",
                "認証機能を実装しました。",
                "msg-final",
                phase="final_answer",
                masked=False,
            ),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=runner
        )

        self.assertEqual(tuple(), result.summaries)
        self.assertEqual(0, runner.calls)
        self.assertIn("summary_input_not_masked", [item.kind for item in result.issues])

    def test_failure_is_cached_for_five_minutes(self) -> None:
        now = datetime(2026, 8, 13, tzinfo=timezone.utc)
        failing = StubRunner(error=subprocess.TimeoutExpired("codex", 120))
        messages = (
            source_message("assistant", "確認中です。", "msg-final", phase="final_answer"),
        )
        first = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=failing, now=now
        )
        recovered = StubRunner()

        second = generate_change_summaries(
            "session-1",
            work_status(turn()),
            messages,
            runner=recovered,
            cache_entries=first.cache_entries,
            now=now + timedelta(seconds=299),
        )

        self.assertEqual(tuple(), second.summaries)
        self.assertEqual(0, recovered.calls)
        self.assertEqual(now + timedelta(minutes=5), first.cache_entries[0].expires_at)

    def test_failure_cache_expires_after_five_minutes(self) -> None:
        now = datetime(2026, 8, 13, tzinfo=timezone.utc)
        failing = StubRunner(error=RuntimeError("codex_nonzero_exit"))
        messages = (
            source_message("assistant", "確認中です。", "msg-final", phase="final_answer"),
        )
        first = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=failing, now=now
        )
        recovered = StubRunner()

        second = generate_change_summaries(
            "session-1",
            work_status(turn()),
            messages,
            runner=recovered,
            cache_entries=first.cache_entries,
            now=now + timedelta(minutes=5),
        )

        self.assertEqual(1, recovered.calls)
        self.assertEqual(1, len(second.summaries))

    def test_successful_cli_summary_is_reused_for_same_evidence(self) -> None:
        messages = (
            source_message("assistant", "確認中です。", "msg-final", phase="final_answer"),
        )
        first_runner = StubRunner()
        first = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=first_runner
        )
        second_runner = StubRunner()

        second = generate_change_summaries(
            "session-1",
            work_status(turn()),
            messages,
            runner=second_runner,
            cache_entries=first.cache_entries,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(first.summaries, second.summaries)
        self.assertEqual(0, second_runner.calls)

    def test_failed_turn_is_not_reworded_as_completed(self) -> None:
        generated = GeneratedSummaryContent(
            "認証実装が未完了",
            "認証実装はテスト失敗のため未完了です。",
            "認証実装を試みましたが、テスト失敗のため完了していません。",
            tuple(),
            (SummaryEvidenceItem("テスト失敗", ("msg-final",)),),
            "high",
        )
        runner = StubRunner(result=generated)
        messages = (
            source_message("assistant", "処理を終了します。", "msg-final", phase="final_answer"),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn(status="failed")), messages, runner=runner
        )

        self.assertEqual("failed", result.summaries[0].status)
        self.assertIn("未完了", result.summaries[0].short_summary)

    def test_rolled_back_summary_is_retained_and_marked(self) -> None:
        messages = (
            source_message(
                "assistant",
                "HTTPS設定を変更しました。",
                "msg-final",
                phase="final_answer",
            ),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn(rolled_back=True)), messages
        )

        self.assertTrue(result.summaries[0].rolled_back)
        self.assertIn("ロールバック済み", result.summaries[0].short_summary)

    def test_in_progress_turn_is_not_summarized(self) -> None:
        messages = (
            source_message("assistant", "実装中です。", "msg-final", phase="commentary"),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn(status="in_progress")), messages
        )

        self.assertEqual(tuple(), result.summaries)
        self.assertEqual(0, result.inference_attempt_count)

    def test_cli_source_reference_must_exist_in_input(self) -> None:
        generated = GeneratedSummaryContent(
            "調査結果",
            "調査しました。",
            "調査結果を整理しました。",
            (SummaryEvidenceItem("存在しない根拠", ("msg-unknown",)),),
            tuple(),
            "high",
        )
        runner = StubRunner(result=generated)
        messages = (
            source_message("assistant", "確認中です。", "msg-final", phase="final_answer"),
        )

        result = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=runner
        )

        self.assertEqual(tuple(), result.summaries)
        self.assertIn(
            "codex_invalid_source_reference", [item.kind for item in result.issues]
        )

    def test_large_input_is_truncated_only_for_prompt(self) -> None:
        text = "あ" * 100_000
        messages = (
            source_message("assistant", text, "msg-final", phase="final_answer"),
        )
        runner = StubRunner()

        result = generate_change_summaries(
            "session-1", work_status(turn()), messages, runner=runner
        )

        self.assertIn("summary_input_truncated", [item.kind for item in result.issues])
        self.assertLessEqual(len(runner.prompt.encode("utf-8")), 128 * 1024)
        self.assertEqual(100_000, len(messages[0].masked_text))

    def test_generated_turn_id_source_is_preserved(self) -> None:
        messages = (
            source_message(
                "assistant",
                "変更要約機能を実装しました。",
                "msg-final",
                phase="final_answer",
            ),
        )

        result = generate_change_summaries(
            "session-1",
            work_status(turn(turn_id_source="generated")),
            messages,
        )

        self.assertEqual("generated", result.summaries[0].turn_id_source)
        self.assertRegex(result.summaries[0].summary_id, r"^change_summary_[0-9a-f]{64}$")

    def test_summary_id_is_separate_between_sessions(self) -> None:
        first_message = source_message(
            "assistant",
            "変更要約機能を実装しました。",
            "msg-final",
            phase="final_answer",
        )
        second_message = source_message(
            "assistant",
            "変更要約機能を実装しました。",
            "msg-final",
            phase="final_answer",
            session_id="session-2",
        )

        first = generate_change_summaries(
            "session-1", work_status(turn()), (first_message,)
        )
        second = generate_change_summaries(
            "session-2", work_status(turn()), (second_message,)
        )

        self.assertNotEqual(
            first.summaries[0].summary_id, second.summaries[0].summary_id
        )

    @patch("tools.change_summary_generator.subprocess.run")
    def test_cli_runner_uses_isolated_supported_flags(self, run: object) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "title": "要約",
                "short_summary": "短い要約です。",
                "details": "詳細な要約です。",
                "highlights": [],
                "verification": [],
                "confidence": "medium",
            },
            ensure_ascii=False,
        )
        runner = ChangeSummaryCliRunner(executable="codex.exe")

        result = runner.generate("マスク済み入力")

        self.assertEqual("要約", result.title)
        command = run.call_args.args[0]
        self.assertIn("--ephemeral", command)
        self.assertIn("read-only", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertEqual("-", command[-1])
        self.assertEqual("マスク済み入力", run.call_args.kwargs["input"])
        self.assertFalse(run.call_args.kwargs["shell"])


    def test_prior_matching_hash_is_reused_when_newer_hash_exists(self) -> None:
        first_messages = (source_message("assistant", "Working on the task.", "msg-final", phase="final_answer"),)
        second_messages = (source_message("assistant", "Working on another task.", "msg-final", phase="final_answer"),)
        first = generate_change_summaries("session-1", work_status(turn()), first_messages, runner=StubRunner())
        second = generate_change_summaries("session-1", work_status(turn()), second_messages, runner=StubRunner())
        restored_runner = StubRunner()
        restored = generate_change_summaries("session-1", work_status(turn()), first_messages, runner=restored_runner, cache_entries=(first.cache_entries[0], second.cache_entries[0]))
        self.assertEqual(0, restored_runner.calls)
        self.assertEqual(first.summaries, restored.summaries)

if __name__ == "__main__":
    unittest.main()
