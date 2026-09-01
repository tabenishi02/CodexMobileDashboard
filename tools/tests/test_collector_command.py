import io
import json
import logging
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.collector import main


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
            with patch("tools.collector._runtime_settings", return_value=runtime), patch("tools.collector.replace", side_effect=lambda value, **kwargs: unittest.mock.Mock(**kwargs)), patch("tools.collector.run_once", return_value=1) as run:
                code = main(["--config", str(config), "backfill-ai", "--no-send"])
        self.assertEqual(0, code)
        self.assertEqual("backfill", run.call_args.args[0].ai_inference_mode)
        self.assertIsNone(run.call_args.args[1])

    def test_collect_once_configures_all_component_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            log_directory = Path(directory) / "logs"
            config = self.write_config(directory, log_directory=log_directory)
            runtime = unittest.mock.Mock()

            def log_component_events(_settings, _sender):
                for name in ("collector", "converter", "sender"):
                    logging.getLogger(name).info("manual_collect_component_ready")
                return 1

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
