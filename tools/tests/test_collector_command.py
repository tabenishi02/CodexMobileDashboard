import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.collector import _run_backfill_until_complete, main
from tools.collector_runtime import CollectorRunResult
from tools.inference_ledger import InferenceLedgerEntry, append, load


class CollectorCommandTests(unittest.TestCase):
    @staticmethod
    def clear_component_handlers():
        for name in ("collector", "converter", "sender"):
            logger = logging.getLogger(name)
            for handler in tuple(logger.handlers):
                logger.removeHandler(handler)
                handler.close()

    def tearDown(self):
        self.clear_component_handlers()

    def write_config(self, directory, sender=False, log_directory=None):
        path = Path(directory) / "collector.ini"
        text = "[storage]\nqueue_dir = " + str(Path(directory) / "queue") + "\n"
        if log_directory is not None:
            text += "\n[logging]\ndirectory = " + str(log_directory) + "\n"
        if sender:
            text += (
                "\n[sender]\nbase_url = https://dashboard.example.test\n"
                "token_file = " + str(Path(directory) / "sender.token") + "\n"
                "ca_file = " + str(Path(directory) / "ca.crt") + "\n"
            )
        path.write_text(text, encoding="utf-8")
        return path

    def test_queue_status_outputs_safe_json_without_sender_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(["--config", str(config), "queue-status"])

        self.assertEqual(0, code)
        self.assertEqual({"items": [], "pending_snapshots": 0}, json.loads(output.getvalue()))

    def test_retry_once_uses_sender_and_reports_queue_state(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory, sender=True)
            Path(directory, "sender.token").write_text("secret-token\n", encoding="utf-8")
            queue = unittest.mock.Mock()
            queue.send_next.return_value = object()
            queue.pending.return_value = ()
            output = io.StringIO()
            with patch("tools.collector.PendingSnapshotQueue", return_value=queue), patch("tools.collector.HttpsSnapshotSender", return_value=object()) as sender:
                with redirect_stdout(output):
                    code = main(["--config", str(config), "retry-queued"])

        self.assertEqual(0, code)
        sender.assert_called_once()
        queue.send_next.assert_called_once()
        self.assertEqual({"queue_empty": True, "sent_snapshots": 1}, json.loads(output.getvalue()))

    def test_backfill_ai_forces_backfill_mode_without_sending(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            output = io.StringIO()
            result = CollectorRunResult(1, 3, 2, 1, 4, 5, 2)
            with patch("tools.collector._runtime_settings", return_value=runtime), patch("tools.collector.replace", side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs)), patch("tools.collector.run_once", return_value=result) as run, redirect_stdout(output):
                code = main(["--config", str(config), "backfill-ai", "--no-send"])
        self.assertEqual(0, code)
        self.assertEqual("backfill", run.call_args.args[0].ai_inference_mode)
        self.assertIsNone(run.call_args.args[1])
        self.assertEqual(result.as_dict(), json.loads(output.getvalue()))

    def test_backfill_until_complete_repeats_until_limit_is_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            limits = iter((4, 2, 0))
            output = io.StringIO()

            def run(_settings, sender):
                self.assertIsNone(sender)
                limit = next(limits)
                return CollectorRunResult(1, 3, 3, 0, limit, limit, 3)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs),
            ), patch("tools.collector.run_once", side_effect=run) as run_mock, redirect_stdout(output):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                        "--max-runs",
                        "5",
                    ]
                )

        self.assertEqual(0, code)
        self.assertEqual(3, run_mock.call_count)
        self.assertEqual(
            {
                "completed": True,
                "executions": 9,
                "failures": 0,
                "limit_reached": 0,
                "pending_remaining": 0,
                "processed_workspaces": 1,
                "progress": 9,
                "runs": 3,
                "successes": 9,
            },
            json.loads(output.getvalue()),
        )

    def test_backfill_until_complete_stops_at_max_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            output = io.StringIO()

            def run(_settings, _sender):
                return CollectorRunResult(1, 3, 3, 0, 7, 7, 3)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs),
            ), patch("tools.collector.run_once", side_effect=run) as run_mock, redirect_stdout(output):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                        "--max-runs",
                        "2",
                    ]
                )

        self.assertEqual(3, code)
        self.assertEqual(2, run_mock.call_count)
        result = json.loads(output.getvalue())
        self.assertEqual("max_runs_reached", result["stop_reason"])
        self.assertEqual(6, result["executions"])
        self.assertEqual(6, result["progress"])
        self.assertEqual(7, result["pending_remaining"])

    def test_backfill_resets_shared_call_limit_per_finite_iteration(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(
                ai_inference_mode="incremental", max_calls_per_run=2
            )
            output = io.StringIO()
            results = (
                CollectorRunResult(1, 2, 2, 0, 2, 2, 2),
                CollectorRunResult(1, 2, 2, 0, 1, 1, 2),
                CollectorRunResult(1, 1, 1, 0, 0, 0, 1),
            )

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(
                    max_calls_per_run=value.max_calls_per_run, **kwargs
                ),
            ), patch(
                "tools.collector.run_once", side_effect=results
            ) as run_mock, redirect_stdout(output):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                        "--max-runs",
                        "3",
                    ]
                )

        self.assertEqual(0, code)
        self.assertEqual(3, run_mock.call_count)
        result = json.loads(output.getvalue())
        self.assertEqual(5, result["executions"])
        self.assertEqual(3, result["runs"])
        self.assertTrue(result["completed"])

    def test_backfill_stops_if_runtime_reports_per_run_hard_limit_violation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(
                ai_inference_mode="incremental", max_calls_per_run=2
            )
            output = io.StringIO()
            invalid = CollectorRunResult(1, 3, 3, 0, 1, 1, 3)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(
                    max_calls_per_run=value.max_calls_per_run, **kwargs
                ),
            ), patch(
                "tools.collector.run_once", return_value=invalid
            ) as run_mock, redirect_stdout(output):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                    ]
                )

        self.assertEqual(2, code)
        self.assertEqual(1, run_mock.call_count)
        result = json.loads(output.getvalue())
        self.assertEqual("hard_limit_exceeded", result["stop_reason"])
        self.assertEqual(3, result["executions"])

    def test_backfill_until_complete_stops_when_limit_remains_without_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            output = io.StringIO()
            stalled = CollectorRunResult(1, 0, 0, 0, 7, 7, 0)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs),
            ), patch(
                "tools.collector.run_once", return_value=stalled
            ) as run_mock, redirect_stdout(output):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                        "--max-runs",
                        "5",
                    ]
                )

        self.assertEqual(3, code)
        self.assertEqual(1, run_mock.call_count)
        result = json.loads(output.getvalue())
        self.assertFalse(result["completed"])
        self.assertEqual("no_progress", result["stop_reason"])
        self.assertEqual(7, result["limit_reached"])
        self.assertEqual(0, result["progress"])

    def test_backfill_until_complete_accepts_zero_limit_without_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            output = io.StringIO()
            complete = CollectorRunResult(1, 0, 0, 0, 0, 0, 0)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs),
            ), patch(
                "tools.collector.run_once", return_value=complete
            ) as run_mock, redirect_stdout(output):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                    ]
                )

        self.assertEqual(0, code)
        self.assertEqual(1, run_mock.call_count)
        result = json.loads(output.getvalue())
        self.assertTrue(result["completed"])
        self.assertEqual(0, result["limit_reached"])
        self.assertEqual(0, result["progress"])

    def test_backfill_until_complete_stops_on_inference_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            output = io.StringIO()
            failed = CollectorRunResult(1, 1, 0, 1, 2, 2, 0)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs),
            ), patch(
                "tools.collector.run_once", return_value=failed
            ) as run_mock, redirect_stdout(output):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                    ]
                )

        self.assertEqual(2, code)
        self.assertEqual(1, run_mock.call_count)
        result = json.loads(output.getvalue())
        self.assertEqual("inference_failure", result["stop_reason"])
        self.assertEqual(1, result["failures"])

    def test_backfill_until_complete_stops_safely_on_save_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            output = io.StringIO()
            error_output = io.StringIO()
            secret = "C:\\private\\ledger.json token=secret"
            first = CollectorRunResult(1, 3, 3, 0, 2, 2, 3)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs),
            ), patch(
                "tools.collector.run_once", side_effect=(first, OSError(secret))
            ) as run_mock, redirect_stdout(output), redirect_stderr(error_output):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                    ]
                )

        self.assertEqual(2, code)
        self.assertEqual(2, run_mock.call_count)
        result = json.loads(output.getvalue())
        self.assertEqual("save_failure", result["stop_reason"])
        self.assertEqual(3, result["progress"])
        self.assertEqual("", error_output.getvalue())
        self.assertNotIn("private", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_backfill_interrupt_keeps_ledger_and_next_invocation_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            ledger = Path(directory) / "ledger.json"
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            saved = InferenceLedgerEntry(
                "workspace-1",
                "session-1",
                "turn-1",
                "a" * 64,
                {"schema_version": 1, "payload": {}},
                "2026-09-02T00:00:00+00:00",
                "change_summary",
            )
            calls = 0

            def interrupt_after_saved_progress(_settings, _sender):
                nonlocal calls
                calls += 1
                if calls == 1:
                    append(ledger, saved)
                    return CollectorRunResult(1, 1, 1, 0, 1, 1, 1)
                raise KeyboardInterrupt("token=secret")

            output = io.StringIO()
            error_output = io.StringIO()
            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs),
            ), patch(
                "tools.collector.run_once", side_effect=interrupt_after_saved_progress
            ), redirect_stdout(output), redirect_stderr(error_output):
                interrupted_code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                    ]
                )

            self.assertEqual(130, interrupted_code)
            self.assertEqual((saved,), load(ledger))
            interrupted = json.loads(output.getvalue())
            self.assertEqual("interrupted", interrupted["stop_reason"])
            self.assertNotIn("secret", output.getvalue())
            self.assertEqual("", error_output.getvalue())

            resumed_output = io.StringIO()
            complete = CollectorRunResult(1, 0, 0, 0, 0, 0, 0)
            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs),
            ), patch(
                "tools.collector.run_once", return_value=complete
            ), redirect_stdout(resumed_output):
                resumed_code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                    ]
                )

            self.assertEqual(0, resumed_code)
            self.assertEqual((saved,), load(ledger))

    def test_single_collection_interrupt_exits_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            error_output = io.StringIO()
            with patch(
                "tools.collector._runtime_settings", return_value=unittest.mock.Mock()
            ), patch(
                "tools.collector.run_once", side_effect=KeyboardInterrupt("secret")
            ), redirect_stderr(error_output):
                code = main(
                    ["--config", str(config), "collect-once", "--no-send"]
                )

        self.assertEqual(130, code)
        self.assertEqual("manual_command_interrupted\n", error_output.getvalue())

    def test_backfill_until_complete_sends_only_final_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory, sender=True)
            Path(directory, "sender.token").write_text("secret-token\n", encoding="utf-8")
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            sender = object()
            calls = []
            backfill_results = iter(
                (
                    CollectorRunResult(1, 3, 3, 0, 2, 2, 3),
                    CollectorRunResult(1, 2, 2, 0, 0, 0, 2),
                )
            )

            def run(settings, selected_sender):
                calls.append((settings.ai_inference_mode, selected_sender))
                if settings.ai_inference_mode == "backfill":
                    return next(backfill_results)
                return CollectorRunResult(1, 0, 0, 0, 0, 0, 0)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector._sender", return_value=sender
            ), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(
                    max_calls_per_run=3, **kwargs
                ),
            ), patch("tools.collector.run_once", side_effect=run), redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--until-complete",
                        "--max-runs",
                        "5",
                    ]
                )

        self.assertEqual(0, code)
        self.assertEqual(
            [
                ("backfill", None),
                ("backfill", None),
                ("incremental", sender),
            ],
            calls,
        )

    def test_backfill_no_send_keeps_every_iteration_local(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory, sender=True)
            Path(directory, "sender.token").write_text("secret-token\n", encoding="utf-8")
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            calls = []
            results = iter(
                (
                    CollectorRunResult(1, 3, 3, 0, 1, 1, 3),
                    CollectorRunResult(1, 1, 1, 0, 0, 0, 1),
                )
            )

            def run(settings, selected_sender):
                calls.append((settings.ai_inference_mode, selected_sender))
                return next(results)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector._sender"
            ) as sender_factory, patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(
                    max_calls_per_run=3, **kwargs
                ),
            ), patch("tools.collector.run_once", side_effect=run), redirect_stdout(io.StringIO()):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--no-send",
                        "--until-complete",
                        "--max-runs",
                        "5",
                    ]
                )

        self.assertEqual(0, code)
        sender_factory.assert_not_called()
        self.assertEqual([("backfill", None), ("backfill", None)], calls)

    def test_backfill_incomplete_does_not_send_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory, sender=True)
            Path(directory, "sender.token").write_text("secret-token\n", encoding="utf-8")
            runtime = unittest.mock.Mock(ai_inference_mode="incremental")
            sender = object()
            calls = []

            def run(settings, selected_sender):
                calls.append((settings.ai_inference_mode, selected_sender))
                return CollectorRunResult(1, 0, 0, 0, 2, 2, 0)

            with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                "tools.collector._sender", return_value=sender
            ), patch(
                "tools.collector.replace",
                side_effect=lambda value, **kwargs: unittest.mock.Mock(
                    max_calls_per_run=3, **kwargs
                ),
            ), patch("tools.collector.run_once", side_effect=run), redirect_stdout(
                io.StringIO()
            ), redirect_stderr(io.StringIO()):
                code = main(
                    [
                        "--config",
                        str(config),
                        "backfill-ai",
                        "--until-complete",
                        "--max-runs",
                        "5",
                    ]
                )

        self.assertEqual(3, code)
        self.assertEqual([("backfill", None)], calls)

    def test_backfill_max_runs_requires_until_complete_and_valid_range(self):
        with tempfile.TemporaryDirectory() as directory:
            config = self.write_config(directory)
            for arguments in (
                ["backfill-ai", "--max-runs", "2"],
                ["backfill-ai", "--until-complete", "--max-runs", "0"],
                ["backfill-ai", "--until-complete", "--max-runs", "1001"],
            ):
                with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        main(["--config", str(config), *arguments])
                    self.assertEqual(2, raised.exception.code)

    def test_backfill_orchestrator_rejects_non_finite_run_bounds(self):
        runtime = unittest.mock.Mock(max_calls_per_run=3)
        for max_runs in (0, 1001, True):
            with self.subTest(max_runs=max_runs), self.assertRaisesRegex(
                ValueError, "backfill_max_runs_invalid"
            ):
                _run_backfill_until_complete(runtime, None, max_runs)

    def test_collect_once_configures_all_component_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            log_directory = Path(directory) / "logs"
            config = self.write_config(directory, log_directory=log_directory)
            runtime = unittest.mock.Mock()

            def log_component_events(_settings, _sender):
                for name in ("collector", "converter", "sender"):
                    logging.getLogger(name).info("manual_collect_component_ready")
                return CollectorRunResult(1, 0, 0, 0, 0, 0, 0)

            try:
                with patch("tools.collector._runtime_settings", return_value=runtime), patch(
                    "tools.collector.run_once", side_effect=log_component_events
                ):
                    code = main(["--config", str(config), "collect-once", "--no-send"])
            finally:
                self.clear_component_handlers()

            self.assertEqual(0, code)
            for name in ("collector", "converter", "sender"):
                content = (log_directory / f"{name}.log").read_text(encoding="utf-8")
                self.assertIn(f"INFO {name} manual_collect_component_ready", content)

    def test_collect_once_logs_safe_failure_without_exception_details(self):
        with tempfile.TemporaryDirectory() as directory:
            log_directory = Path(directory) / "logs"
            config = self.write_config(directory, log_directory=log_directory)
            secret = "token=collector-secret C:\\private\\session.jsonl"
            error_output = io.StringIO()
            try:
                with patch("tools.collector._runtime_settings", return_value=unittest.mock.Mock()), patch(
                    "tools.collector.run_once", side_effect=OSError(secret)
                ), redirect_stderr(error_output):
                    code = main(["--config", str(config), "collect-once", "--no-send"])
            finally:
                self.clear_component_handlers()

            content = (log_directory / "collector.log").read_text(encoding="utf-8")
            self.assertEqual(2, code)
            self.assertIn("ERROR collector manual_collection_failed kind=configuration_or_io", content)
            self.assertNotIn("collector-secret", content)
            self.assertNotIn("session.jsonl", content)

    def test_invalid_configuration_does_not_echo_token_or_path_details(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "collector.ini"
            config.write_text("[storage]\n", encoding="utf-8")
            error = io.StringIO()
            with redirect_stderr(error):
                code = main(["--config", str(config), "queue-status"])

        self.assertEqual(2, code)
        self.assertEqual("manual_command_failed kind=configuration_or_io\n", error.getvalue())


if __name__ == "__main__":
    unittest.main()
