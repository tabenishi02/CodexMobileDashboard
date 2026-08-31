import tempfile
import unittest
from pathlib import Path

from tools.next_task_extractor import InferenceMessage, NextTaskInferenceContext, extract_next_task


class _Runner:
    def __init__(self): self.calls = 0
    def infer(self, prompt):
        self.calls += 1
        return ("next task", "reason", "high")


def _context(text):
    return NextTaskInferenceContext("idle", (InferenceMessage("message-1", "assistant", text),), tuple(), tuple(), True)


class InferenceRegressionTests(unittest.TestCase):
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
