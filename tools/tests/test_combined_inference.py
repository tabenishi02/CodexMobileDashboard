import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools.combined_inference import (
    CombinedInferenceResult,
    CombinedTurnCliRunner,
    assess_combined_turn,
    combined_turn_schema,
    convert_combined_result,
    execute_combined_turn,
    save_combined_turn,
)
from tools.inference_ledger import load


class CombinedInferenceEligibilityTests(unittest.TestCase):
    def test_completed_masked_incremental_turn_is_eligible(self):
        value = assess_combined_turn("completed", "turn-1", ("turn-1",), ("turn-1",), True, True)
        self.assertTrue(value.eligible)

    def test_unmasked_or_cross_turn_input_falls_back(self):
        self.assertEqual("inference_input_not_masked", assess_combined_turn("completed", "turn-1", None, ("turn-1",), False, True).fallback_reason)
        self.assertEqual("cross_turn_context", assess_combined_turn("completed", "turn-1", None, ("turn-1", "turn-2"), True, True).fallback_reason)


class CombinedInferenceExecutionTests(unittest.TestCase):
    def test_eligible_turn_calls_combined_cli_once_without_individual_fallback(self):
        conversion = _conversion("a" * 64)
        runner = Mock()
        runner.infer.return_value = _result()
        convert = Mock(return_value=conversion)
        fallback = Mock()

        execution = execute_combined_turn(
            assess_combined_turn("completed", "turn-1", ("turn-1",), ("turn-1",), True, True),
            "masked prompt",
            runner=runner,
            convert=convert,
            fallback_to_individual=fallback,
        )

        runner.infer.assert_called_once_with("masked prompt")
        convert.assert_called_once_with(_result())
        fallback.assert_not_called()
        self.assertEqual(conversion, execution.conversion)
        self.assertFalse(execution.used_individual_fallback)

    def test_failed_or_ineligible_turn_uses_individual_fallback_without_extra_cli_call(self):
        runner = Mock()
        runner.infer.side_effect = RuntimeError("codex_nonzero_exit")
        fallback = Mock()
        convert = Mock()

        failed = execute_combined_turn(
            assess_combined_turn("completed", "turn-1", ("turn-1",), ("turn-1",), True, True),
            "masked prompt",
            runner=runner,
            convert=convert,
            fallback_to_individual=fallback,
        )
        ineligible = execute_combined_turn(
            assess_combined_turn("completed", "turn-1", tuple(), ("turn-1",), True, True),
            "masked prompt",
            runner=runner,
            convert=convert,
            fallback_to_individual=fallback,
        )

        self.assertEqual(1, runner.infer.call_count)
        convert.assert_not_called()
        self.assertEqual([(("combined_inference_failed",), {}), (("turn_not_incremental",), {})], fallback.call_args_list)
        self.assertTrue(failed.used_individual_fallback)
        self.assertTrue(ineligible.used_individual_fallback)

class CombinedInferenceRunnerTests(unittest.TestCase):
    def test_schema_requires_all_three_outputs(self):
        schema = combined_turn_schema()
        self.assertEqual(["summary", "decisions", "next_task"], schema["required"])
        self.assertFalse(schema["additionalProperties"])

    def test_conversion_and_combined_ledger_save_preserve_individual_payloads(self):
        conversion = _conversion("a" * 64)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            save_combined_turn(ledger, workspace_id="workspace-1", session_id="session-1", turn_id="turn-1", input_sha256="a" * 64, conversion=conversion, generated_at="2026-08-31T00:00:00+00:00")
            entries = load(ledger)

        self.assertEqual("combined_turn", entries[0].inference_kind)
        payload = entries[0].result["payload"]
        self.assertEqual("Title", payload["change_summary"]["payload"]["title"])
        self.assertEqual("Decision", payload["decision"]["payload"]["proposals"][0]["title"])
        self.assertEqual("Task", payload["next_task"]["payload"]["task"]["text"])

    def test_failed_combined_save_keeps_previous_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            save_combined_turn(ledger, workspace_id="workspace-1", session_id="session-1", turn_id="turn-1", input_sha256="a" * 64, conversion=_conversion("a" * 64))
            with patch("tools.inference_ledger.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    save_combined_turn(ledger, workspace_id="workspace-1", session_id="session-1", turn_id="turn-2", input_sha256="b" * 64, conversion=_conversion("b" * 64))
            entries = load(ledger)

        self.assertEqual(1, len(entries))
        self.assertEqual("turn-1", entries[0].turn_id)

    @patch("tools.combined_inference.subprocess.run")
    def test_runner_uses_isolated_cli_and_returns_structured_result(self, run):
        payload = {
            "summary": {"title": "Title", "short_summary": "Short", "details": "Details", "confidence": "high"},
            "decisions": [{"status": "adopted", "title": "Decision", "description": "Description", "reason": None, "topic_key": "topic"}],
            "next_task": {"task": "Task", "reason": "Reason", "confidence": "medium"},
        }
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps(payload), "")

        result = CombinedTurnCliRunner(executable="codex-test").infer("masked prompt")

        self.assertEqual("Title", result.summary["title"])
        self.assertEqual("Task", result.next_task["task"])
        command = run.call_args.args[0]
        self.assertEqual("codex-test", command[0])
        self.assertIn("--ephemeral", command)
        self.assertEqual("read-only", command[command.index("--sandbox") + 1])
        self.assertIn("--output-schema", command)
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual("masked prompt", run.call_args.kwargs["input"])

    @patch("tools.combined_inference.subprocess.run")
    def test_runner_rejects_incomplete_result(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "{}", "")

        with self.assertRaisesRegex(RuntimeError, "codex_invalid_result"):
            CombinedTurnCliRunner(executable="codex-test").infer("masked prompt")


def _result():
    return CombinedInferenceResult(
        {"title": "Title", "short_summary": "Short", "details": "Details", "confidence": "high"},
        ({"status": "adopted", "title": "Decision", "description": "Description", "reason": None, "topic_key": "topic"},),
        {"task": "Task", "reason": "Reason", "confidence": "medium"},
    )

def _conversion(input_sha256):
    return convert_combined_result(
        CombinedInferenceResult(
            {"title": "Title", "short_summary": "Short", "details": "Details", "confidence": "high"},
            ({"status": "adopted", "title": "Decision", "description": "Description", "reason": None, "topic_key": "topic"},),
            {"task": "Task", "reason": "Reason", "confidence": "medium"},
        ),
        session_id="session-1",
        turn_id="turn-1",
        turn_id_source="jsonl",
        turn_status="completed",
        rolled_back=False,
        source_message_ids=("message-1",),
        input_sha256=input_sha256,
    )


if __name__ == "__main__":
    unittest.main()
