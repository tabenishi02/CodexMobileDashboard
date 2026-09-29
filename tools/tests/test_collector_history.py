import tempfile
import unittest
from pathlib import Path

from tools.collector_history import CollectorHistory, InvalidCollectorHistoryError, load_collector_history, save_collector_history
from tools.record_normalizer import NormalizedContentPart, NormalizedRecord


def record():
    return NormalizedRecord(None, "message", "chat_message", "response_item", None, None, None, "user", None, (NormalizedContentPart("text", "[REDACTED_TOKEN]", "text"),), {}, Path("C:/session.jsonl"), 1, 0, 1)


class HistoryTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            save_collector_history(CollectorHistory().replace("session-1", (record(),)), path)
            self.assertEqual("[REDACTED_TOKEN]", load_collector_history(path).records_for("session-1")[0].content[0].text)

    def test_distinguishes_missing_session_from_recordless_session(self):
        history = CollectorHistory().replace("empty-session", tuple())
        self.assertTrue(history.has_session("empty-session"))
        self.assertFalse(history.has_session("missing-session"))
        self.assertEqual(tuple(), history.records_for("empty-session"))

    def test_invalid_sessions_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text("{\"version\": 1, \"sessions\": []}", encoding="utf-8")
            with self.assertRaises(InvalidCollectorHistoryError):
                load_collector_history(path)


if __name__ == "__main__":
    unittest.main()
