import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tools.collector import main


class CollectorCommandTests(unittest.TestCase):
    def write_config(self, directory, sender=False):
        path = Path(directory) / "collector.ini"
        text = "[storage]\nqueue_dir = " + str(Path(directory) / "queue") + "\n"
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
