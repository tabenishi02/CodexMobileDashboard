import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.change_summary_generator import ChangeSummary, SummaryEvidenceItem
from tools.decision_extractor import DecisionInferenceCacheEntry, ExtractedDecision, _Proposal, decision_inference_payload, inference_payload
from tools.next_task_extractor import NextTask, NextTaskCacheEntry, NextTaskIssue

from tools.inference_ledger import CombinedTurnCacheEntry, InferenceLedgerEntry, append, combined_turn_cache_entry, combined_turn_payload, decision_inference_cache, decision_inference_entries, latest_decision_history, load, next_task_cache_entry, next_task_payload, summary_cache_entries, summary_payload


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
    def test_combined_turn_restores_only_exact_sha_and_individual_caches(self) -> None:
        summary = ChangeSummary("summary-1", "turn-1", "jsonl", "completed", False, "title", "short", "details", tuple(), tuple(), "codex_generated", "high", ("session-1",), ("message-1",))
        proposals = (_Proposal("adopted", "decision", "description", None, "topic"),)
        decision = ExtractedDecision("decision-1", "2026-08-31T00:00:00+00:00", "message_timestamp", "adopted", "decision", "description", None, ("session-1",), ("message-1",), None, None, "topic")
        task = NextTask("task-1", "task text", "pending", "codex_inferred", "high", "reason", ("message-1",))
        cache = NextTaskCacheEntry("a" * 64, task, None, tuple())
        combined = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "a" * 64, combined_turn_payload(summary, proposals, (decision,), cache), "2026-08-31T00:00:00+00:00", "combined_turn")

        restored = combined_turn_cache_entry((combined,), "workspace-1", "session-1", "turn-1", "a" * 64)

        self.assertIsInstance(restored, CombinedTurnCacheEntry)
        self.assertEqual(summary, restored.summary)
        self.assertEqual(proposals, restored.decision_proposals)
        self.assertEqual(cache, restored.next_task_cache_entry)
        self.assertIsNone(combined_turn_cache_entry((combined,), "workspace-1", "session-1", "turn-1", "b" * 64))
        self.assertEqual(summary, summary_cache_entries((combined,), "workspace-1", "session-1")[0].summary)
        self.assertEqual(proposals, decision_inference_cache((combined,), "workspace-1")["a" * 64])
        self.assertEqual(cache, next_task_cache_entry((combined,), "workspace-1", "session-1"))

    def test_incomplete_combined_turn_falls_back_to_individual_entries(self) -> None:
        summary = ChangeSummary("summary-1", "turn-1", "jsonl", "completed", False, "title", "short", "details", tuple(), tuple(), "codex_generated", "high", ("session-1",), ("message-1",))
        proposals = (_Proposal("adopted", "decision", "description", None, "topic"),)
        task = NextTask("task-1", "task text", "pending", "codex_inferred", "high", "reason", ("message-1",))
        cache = NextTaskCacheEntry("c" * 64, task, None, tuple())
        incomplete = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "a" * 64, {"schema_version": 1, "payload": {"change_summary": {}}}, "2026-08-31T00:00:00+00:00", "combined_turn")
        individual_summary = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "d" * 64, {"schema_version": 1, "payload": summary_payload(summary)}, "2026-08-31T00:00:00+00:00", "change_summary")
        individual_decision = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "e" * 64, inference_payload(proposals), "2026-08-31T00:00:00+00:00", "decision")
        individual_task = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "c" * 64, next_task_payload(cache), "2026-08-31T00:00:00+00:00", "next_task")
        entries = (incomplete, individual_summary, individual_decision, individual_task)

        self.assertIsNone(combined_turn_cache_entry(entries, "workspace-1", "session-1", "turn-1", "a" * 64))
        self.assertEqual(summary, summary_cache_entries(entries, "workspace-1", "session-1")[0].summary)
        self.assertEqual(proposals, decision_inference_cache(entries, "workspace-1")["e" * 64])
        self.assertEqual(cache, next_task_cache_entry(entries, "workspace-1", "session-1"))

    def test_complete_decision_cache_is_scoped_by_all_identity_fields(self) -> None:
        proposals = (_Proposal("adopted", "new", "new description", None, "topic"),)
        old = ExtractedDecision("old", "2026-08-31T00:00:00+00:00", "created_at", "superseded", "old", "old description", None, ("session-1",), ("message-1",), None, "new", "topic")
        new = ExtractedDecision("new", "2026-08-31T00:01:00+00:00", "created_at", "adopted", "new", "new description", None, ("session-1",), ("message-2",), "old", None, "topic")
        cache_entry = DecisionInferenceCacheEntry("session-1", "turn-2", "f" * 64, proposals, (old, new))
        complete = InferenceLedgerEntry("workspace-1", "session-1", "turn-2", "f" * 64, decision_inference_payload(cache_entry), "2026-08-31T00:01:00+00:00", "decision")
        legacy = InferenceLedgerEntry("workspace-1", "session-1", "turn-2", "e" * 64, inference_payload(proposals), "2026-08-31T00:00:00+00:00", "decision")

        restored = decision_inference_entries((legacy, complete), "workspace-1")

        self.assertEqual(
            cache_entry,
            restored[("session-1", "turn-2", "f" * 64)],
        )
        self.assertNotIn(("session-1", "turn-2", "e" * 64), restored)
        self.assertEqual((old, new), latest_decision_history((legacy, complete), "workspace-1"))
        self.assertEqual({}, decision_inference_entries((complete,), "other-workspace"))

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
