import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.change_summary_generator import ChangeSummary, SummaryEvidenceItem
from tools.next_task_extractor import NextTask, NextTaskCacheEntry, NextTaskIssue

from tools.inference_ledger import InferenceLedgerEntry, append, load, next_task_cache_entry, next_task_payload, summary_cache_entries, summary_payload


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

    def test_versioned_summary_payload_restores_and_legacy_is_ignored(self) -> None:
        summary = ChangeSummary("summary-1", "turn-1", "jsonl", "completed", False, "title", "short", "details", (SummaryEvidenceItem("highlight", ("message-1",)),), tuple(), "codex_generated", "high", ("session-1",), ("message-1",))
        complete = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "d" * 64, {"schema_version": 1, "payload": summary_payload(summary)}, "2026-08-31T00:00:00+00:00", "change_summary")
        legacy = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "e" * 64, {"payload": summary_payload(summary)}, "2026-08-31T00:00:00+00:00", "change_summary")
        restored = summary_cache_entries((legacy, complete), "workspace-1", "session-1")
        self.assertEqual(summary, restored[0].summary)
        self.assertEqual("d" * 64, restored[0].evidence_hash)
    def test_next_task_payload_restores_matching_session(self) -> None:
        task = NextTask("task-1", "task text", "pending", "codex_inferred", "high", "reason", ("message-1",))
        cache = NextTaskCacheEntry("c" * 64, task, None, (NextTaskIssue("warning"),))
        value = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "c" * 64, next_task_payload(cache), "2026-08-31T00:00:00+00:00", "next_task")
        self.assertEqual(cache, next_task_cache_entry((value,), "workspace-1", "session-1"))
        self.assertIsNone(next_task_cache_entry((value,), "workspace-1", "other-session"))
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
