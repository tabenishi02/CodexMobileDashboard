import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from tools.collector_runtime import InferenceCallBudget
from tools.combined_inference import assess_combined_turn, execute_combined_turn
from tools.next_task_extractor import InferenceMessage, NextTaskInferenceContext, extract_next_task


class _Runner:
    def __init__(self): self.calls = 0
    def infer(self, prompt):
        self.calls += 1
        return ("next task", "reason", "high")


def _context(text):
    return NextTaskInferenceContext("idle", (InferenceMessage("message-1", "assistant", text),), tuple(), tuple(), True)


class InferenceRegressionTests(unittest.TestCase):
    def test_combined_and_individual_routes_share_one_budget(self):
        budget = InferenceCallBudget(1)
        combined_runner = Mock()
        combined_runner.infer.return_value = object()
        fallback = Mock()
        execution = execute_combined_turn(
            assess_combined_turn("completed", "turn-1", ("turn-1",), ("turn-1",), True, True),
            "masked prompt",
            runner=combined_runner,
            convert=lambda value: value,
            fallback_to_individual=fallback,
            can_infer=budget.try_acquire,
        )
        individual_runner = _Runner()
        with tempfile.TemporaryDirectory() as directory:
            limited = extract_next_task(
                tuple(),
                _context("context"),
                Path(directory) / "TASKS.md",
                runner=individual_runner,
                can_infer=budget.try_acquire,
            )

        self.assertIsNotNone(execution.conversion)
        combined_runner.infer.assert_called_once_with("masked prompt")
        fallback.assert_not_called()
        self.assertEqual(0, individual_runner.calls)
        self.assertIn("inference_limit_reached", [item.kind for item in limited.issues])
        self.assertEqual(1, budget.calls)
        self.assertEqual(1, budget.deferred)

    def test_off_and_limit_do_not_start_cli(self):
        runner = _Runner()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "TASKS.md"
            disabled = extract_next_task(tuple(), _context("context"), path, runner=runner, allow_inference=False)
            limited = extract_next_task(tuple(), _context("context"), path, runner=runner, can_infer=lambda: False)
        self.assertEqual(0, runner.calls)
        self.assertIn("inference_disabled", [item.kind for item in disabled.issues])
        self.assertIn("inference_limit_reached", [item.kind for item in limited.issues])

    def test_input_change_reinfers_but_identical_input_reuses_cache(self):
        runner = _Runner()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "TASKS.md"
            first = extract_next_task(tuple(), _context("first"), path, runner=runner)
            repeated = extract_next_task(tuple(), _context("first"), path, runner=runner, cache_entry=first.cache_entry)
            changed = extract_next_task(tuple(), _context("changed"), path, runner=runner, cache_entry=first.cache_entry)
        self.assertEqual(2, runner.calls)
        self.assertFalse(repeated.inference_attempted)
        self.assertTrue(changed.inference_attempted)


if __name__ == "__main__":
    unittest.main()
