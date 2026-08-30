import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.inference_ledger import InferenceLedgerEntry, append, load


def entry(input_sha256: str = "a" * 64) -> InferenceLedgerEntry:
    return InferenceLedgerEntry(
        "workspace-1", "session-1", "turn-1", input_sha256,
        {"schema_version": 1, "payload": {"value": "ok"}},
        "2026-08-31T00:00:00+00:00", "decision",
    )


class InferenceLedgerTests(unittest.TestCase):
    def test_append_round_trips_all_identity_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "ledger.json"
            append(path, entry())
            restored = load(path)

        self.assertEqual((entry(),), restored)

    def test_failed_replace_preserves_previous_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            first = entry()
            append(path, first)
            with patch("tools.inference_ledger.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    append(path, entry("b" * 64))
            self.assertEqual((first,), load(path))
            self.assertEqual([], list(path.parent.glob(".ledger.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()
