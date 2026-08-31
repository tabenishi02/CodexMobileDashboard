import json
import subprocess
import unittest
from unittest.mock import patch

from tools.combined_inference import (
    CombinedTurnCliRunner,
    assess_combined_turn,
    combined_turn_schema,
)


class CombinedInferenceEligibilityTests(unittest.TestCase):
    def test_completed_masked_incremental_turn_is_eligible(self):
        value = assess_combined_turn("completed", "turn-1", ("turn-1",), ("turn-1",), True, True)
        self.assertTrue(value.eligible)

    def test_unmasked_or_cross_turn_input_falls_back(self):
        self.assertEqual("inference_input_not_masked", assess_combined_turn("completed", "turn-1", None, ("turn-1",), False, True).fallback_reason)
        self.assertEqual("cross_turn_context", assess_combined_turn("completed", "turn-1", None, ("turn-1", "turn-2"), True, True).fallback_reason)


class CombinedInferenceRunnerTests(unittest.TestCase):
    def test_schema_requires_all_three_outputs(self):
        schema = combined_turn_schema()
        self.assertEqual(["summary", "decisions", "next_task"], schema["required"])
        self.assertFalse(schema["additionalProperties"])

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


if __name__ == "__main__":
    unittest.main()
