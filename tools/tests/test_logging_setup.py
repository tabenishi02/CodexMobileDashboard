import json
import logging
import tempfile
import unittest
from pathlib import Path

from tools.collector_state import CollectorState
from tools.incremental_collector import collect_incremental_records
from tools.logging_setup import configure_component_logging


class LoggingSetupTests(unittest.TestCase):
    @staticmethod
    def _clear_handlers() -> None:
        logger = logging.getLogger("collector")
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def tearDown(self) -> None:
        self._clear_handlers()

    def test_creates_utf8_component_log_with_iso_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            try:
                configure_component_logging(Path(directory), components=("collector",))
                logging.getLogger("collector").info("records=1")
            finally:
                self._clear_handlers()

            content = (Path(directory) / "collector.log").read_text(encoding="utf-8")
            self.assertRegex(
                content,
                r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} INFO collector records=1\n$",
            )

    def test_incremental_result_logs_counts_without_message_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            try:
                log_directory = Path(directory) / "logs"
                session_file = Path(directory) / "rollout-test.jsonl"
                source = {
                    "timestamp": "2026-08-29T10:00:00Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "id": "message-1",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "token=raw-secret"}],
                    },
                }
                session_file.write_text(json.dumps(source) + "\n", encoding="utf-8")
                configure_component_logging(log_directory, components=("collector",))
                collect_incremental_records("session-1", session_file, CollectorState())
            finally:
                self._clear_handlers()

            content = (log_directory / "collector.log").read_text(encoding="utf-8")
            self.assertIn("incremental_collection_completed session_id=session-1 records=1", content)
            self.assertIn("redactions=1", content)
            self.assertNotIn("raw-secret", content)

    def test_reconfiguration_does_not_duplicate_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            try:
                configure_component_logging(Path(directory), components=("collector",))
                configure_component_logging(Path(directory), components=("collector",))

                logger = logging.getLogger("collector")
                self.assertEqual(2, len(logger.handlers))
            finally:
                self._clear_handlers()

    def test_rejects_invalid_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "logging_level_invalid"):
                configure_component_logging(Path(directory), level="TRACE")
            with self.assertRaisesRegex(ValueError, "retention_days_invalid"):
                configure_component_logging(Path(directory), retention_days=0)


if __name__ == "__main__":
    unittest.main()
