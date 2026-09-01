import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import patch

from tools.chat_extractor import ChatContentPart, ExtractedChatMessage
from tools.next_task_extractor import (
    INFERENCE_INPUT_MAX_BYTES,
    InferenceMessage,
    NextTaskCacheEntry,
    NextTaskInferenceContext,
    CodexCliRunner,
    extract_next_task,
)


def chat_message(role: str, text: str, message_id: str = "msg-1") -> ExtractedChatMessage:
    return ExtractedChatMessage(
        message_id=message_id,
        source_message_id=message_id,
        sequence=1,
        created_at="2026-08-08T01:00:00+00:00",
        role=role,
        message_type="chat",
        phase=None,
        turn_id="turn-1",
        content=(ChatContentPart("text", text),),
        display_mode="expanded",
        duplicate_of=None,
        occurrence_count=1,
        removed_automatic_contexts=tuple(),
        source_path=Path("rollout-test.jsonl"),
        source_line_number=1,
        source_start_offset=10,
    )


def context(*, masked: bool = True, text: str = "直近メッセージ") -> NextTaskInferenceContext:
    return NextTaskInferenceContext(
        current_status="現在の作業は完了",
        recent_messages=(InferenceMessage("msg-1", "user", text),),
        decisions=("標準ライブラリだけを使用する",),
        incomplete_tasks=("エラーを抽出する",),
        is_masked=masked,
    )


class StubRunner:
    def __init__(
        self,
        result: Tuple[str, str, str] = ("エラーを抽出する", "次の未完了工程", "high"),
        error: Optional[BaseException] = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.prompt = ""

    def infer(self, prompt: str) -> Tuple[str, str, str]:
        self.calls += 1
        self.prompt = prompt
        if self.error is not None:
            raise self.error
        return self.result


class NextTaskExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.tasks_path = Path(self.directory.name) / "TASKS.md"
        self.tasks_path.write_text(
            "- [x] 完了済み\n- [ ] フォールバック作業\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_explicit_task_has_priority_over_cli(self) -> None:
        runner = StubRunner()
        messages = [chat_message("assistant", "次のタスクは「エラーを抽出する」です。")]

        result = extract_next_task(
            messages, context(), self.tasks_path, runner=runner
        )

        self.assertEqual("エラーを抽出する", result.task.text)
        self.assertEqual("explicit", result.task.origin)
        self.assertEqual(("msg-1",), result.task.source_message_ids)
        self.assertEqual(0, runner.calls)

    def test_current_task_title_is_not_mistaken_for_next_task(self) -> None:
        runner = StubRunner()
        messages = [
            chat_message(
                "user",
                "「次に行う作業を抽出する」では機能の実装を行います。",
            )
        ]

        result = extract_next_task(
            messages, context(), self.tasks_path, runner=runner
        )

        self.assertEqual("codex_inferred", result.task.origin)
        self.assertEqual(1, runner.calls)

    def test_masked_context_is_inferred_in_structured_prompt(self) -> None:
        runner = StubRunner()

        result = extract_next_task([], context(), self.tasks_path, runner=runner)

        self.assertEqual("codex_inferred", result.task.origin)
        self.assertTrue(result.inference_attempted)
        self.assertEqual(1, runner.calls)
        payload = json.loads(runner.prompt.split("\n", 1)[1])
        self.assertEqual("直近メッセージ", payload["recent_messages"][0]["text"])
        self.assertLessEqual(result.inference_input_bytes, INFERENCE_INPUT_MAX_BYTES)

    def test_unmasked_context_never_calls_cli_and_uses_tasks(self) -> None:
        runner = StubRunner()

        result = extract_next_task(
            [], context(masked=False), self.tasks_path, runner=runner
        )

        self.assertEqual("フォールバック作業", result.task.text)
        self.assertEqual("fallback", result.task.origin)
        self.assertFalse(result.inference_attempted)
        self.assertEqual(0, runner.calls)
        self.assertIn("inference_input_not_masked", [item.kind for item in result.issues])

    def test_cli_failure_is_cached_for_five_minutes(self) -> None:
        now = datetime(2026, 8, 8, tzinfo=timezone.utc)
        failing = StubRunner(error=RuntimeError("codex_nonzero_exit"))
        first = extract_next_task(
            [], context(), self.tasks_path, runner=failing, now=now
        )
        second_runner = StubRunner()

        second = extract_next_task(
            [],
            context(),
            self.tasks_path,
            runner=second_runner,
            cache_entry=first.cache_entry,
            now=now + timedelta(seconds=299),
        )

        self.assertEqual("fallback", second.task.origin)
        self.assertEqual(0, second_runner.calls)
        self.assertEqual(now + timedelta(minutes=5), first.cache_entry.expires_at)

    def test_failure_cache_expires_after_five_minutes(self) -> None:
        now = datetime(2026, 8, 8, tzinfo=timezone.utc)
        failing = StubRunner(error=RuntimeError("codex_nonzero_exit"))
        first = extract_next_task(
            [], context(), self.tasks_path, runner=failing, now=now
        )
        recovered = StubRunner()

        result = extract_next_task(
            [],
            context(),
            self.tasks_path,
            runner=recovered,
            cache_entry=first.cache_entry,
            now=now + timedelta(minutes=5),
        )

        self.assertEqual("codex_inferred", result.task.origin)
        self.assertEqual(1, recovered.calls)

    def test_same_successful_evidence_is_reused_without_expiration(self) -> None:
        first_runner = StubRunner()
        first = extract_next_task(
            [], context(), self.tasks_path, runner=first_runner
        )
        second_runner = StubRunner()

        second = extract_next_task(
            [],
            context(),
            self.tasks_path,
            runner=second_runner,
            cache_entry=first.cache_entry,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(first.task, second.task)
        self.assertEqual(0, second_runner.calls)

    def test_input_over_128_kib_is_truncated_without_changing_source(self) -> None:
        large_text = "あ" * 100_000
        source = context(text=large_text)
        runner = StubRunner()

        result = extract_next_task([], source, self.tasks_path, runner=runner)

        self.assertLessEqual(result.inference_input_bytes, INFERENCE_INPUT_MAX_BYTES)
        self.assertTrue(result.inference_input_truncated)
        self.assertEqual(100_000, len(source.recent_messages[0].text))
        self.assertIn("inference_input_truncated", [item.kind for item in result.issues])

    def test_timeout_uses_fallback_without_retry(self) -> None:
        runner = StubRunner(
            error=subprocess.TimeoutExpired(["codex", "exec"], timeout=120)
        )

        result = extract_next_task([], context(), self.tasks_path, runner=runner)

        self.assertEqual(1, runner.calls)
        self.assertEqual("fallback", result.task.origin)
        self.assertIn("codex_timeout", [item.kind for item in result.issues])

    def test_missing_tasks_file_can_return_no_task(self) -> None:
        runner = StubRunner(error=RuntimeError("codex_nonzero_exit"))
        workspace = Path(self.directory.name) / "workspace-without-tasks"
        workspace.mkdir()
        tasks_path = workspace / "TASKS.md"

        result = extract_next_task(
            [], context(), tasks_path, runner=runner
        )

        self.assertIsNone(result.task)
        self.assertIsNotNone(result.cache_entry)
        self.assertIn("fallback_task_not_found", [item.kind for item in result.issues])

        second_runner = StubRunner()
        cached = extract_next_task(
            [],
            context(),
            tasks_path,
            runner=second_runner,
            cache_entry=result.cache_entry,
        )
        self.assertIsNone(cached.task)
        self.assertEqual(0, second_runner.calls)

    @patch("tools.next_task_extractor.subprocess.run")
    def test_cli_runner_uses_isolated_supported_flags(self, run: object) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {"task": "次の作業", "reason": "根拠", "confidence": "medium"},
            ensure_ascii=False,
        )
        runner = CodexCliRunner(executable="codex.exe")

        result = runner.infer("マスク済み入力")

        self.assertEqual(("次の作業", "根拠", "medium"), result)
        command = run.call_args.args[0]
        self.assertIn("--ephemeral", command)
        self.assertIn("read-only", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertNotIn("--ask-for-approval", command)
        self.assertEqual("-", command[-1])
        self.assertEqual("マスク済み入力", run.call_args.kwargs["input"])
        self.assertFalse(run.call_args.kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
