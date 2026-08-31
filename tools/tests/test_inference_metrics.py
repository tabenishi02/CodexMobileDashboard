import unittest

from tools.inference_metrics import collect_run, record


class InferenceMetricsTests(unittest.TestCase):
    def test_collects_all_safe_events_and_execution_input_bytes(self) -> None:
        with self.assertLogs("collector", level="INFO") as logs:
            with collect_run() as metrics:
                record("change_summary", "execution", 100)
                record("change_summary", "success", 100)
                record("decision", "cache_hit", 200)
                record("next_task", "skipped", 300)
                record("combined_turn", "limit_reached", 400)
                record("combined_turn", "failure", 400)
                record("combined_turn", "fallback", 400)
                metrics.pending_remaining = 2

        output = "\n".join(logs.output)
        self.assertIn(
            "inference_run_metrics executions=1 cache_hits=1 skipped=1 "
            "limit_reached=1 successes=1 failures=1 fallbacks=1 "
            "input_bytes=100 pending_remaining=2",
            output,
        )
        for secret in (
            "prompt-secret",
            "token-secret",
            "workspace-secret",
            "session-secret",
            "turn-secret",
            "C:/secret/file.txt",
            "a" * 64,
        ):
            self.assertNotIn(secret, output)

    def test_rejects_identifier_like_kind_and_unknown_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "inference_metric_kind_invalid"):
            record("workspace-secret", "execution", 1)
        with self.assertRaisesRegex(ValueError, "inference_metric_event_invalid"):
            record("decision", "turn-secret", 1)


if __name__ == "__main__":
    unittest.main()
