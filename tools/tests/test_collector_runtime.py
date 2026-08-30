import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.change_summary_generator import ChangeSummary, SummaryEvidenceItem
from tools.chat_extractor import ChatExtractionResult
from tools.collector_runtime import _build_workspace_snapshot
from tools.inference_ledger import InferenceLedgerEntry, append, summary_payload


class CollectorRuntimeTests(unittest.TestCase):
    def test_restart_reuses_saved_change_summary_ledger_entry(self) -> None:
        summary = ChangeSummary("summary-1", "turn-1", "jsonl", "completed", False, "title", "short", "details", (SummaryEvidenceItem("highlight", ("message-1",)),), tuple(), "codex_generated", "high", ("session-1",), ("message-1",))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"
            append(ledger, InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "a" * 64, {"schema_version": 1, "payload": summary_payload(summary)}, "2026-08-31T00:00:00+00:00", "change_summary"))
            settings = SimpleNamespace(inference_ledger_file=ledger, ai_inference_mode="incremental", tasks_path=Path(directory) / "TASKS.md", output_dir=Path(directory) / "output", queue_dir=Path(directory) / "queue")
            captured = []
            summaries = SimpleNamespace(cache_entries=tuple())
            with patch("tools.collector_runtime.extract_chat_messages", return_value=ChatExtractionResult(tuple(), tuple(), tuple())), patch("tools.collector_runtime.extract_current_work_status", return_value=SimpleNamespace(codex_status="idle")), patch("tools.collector_runtime.extract_decisions", return_value=SimpleNamespace(decisions=tuple())), patch("tools.collector_runtime.extract_file_references", return_value=SimpleNamespace(references=tuple())), patch("tools.collector_runtime.extract_development_errors", return_value=object()), patch("tools.collector_runtime.generate_change_summaries", side_effect=lambda *args, **kwargs: captured.extend(kwargs["cache_entries"]) or summaries), patch("tools.collector_runtime.extract_next_task", return_value=SimpleNamespace(inference_attempted=False, cache_entry=None)), patch("tools.collector_runtime.collect_git_changes", return_value=object()), patch("tools.collector_runtime.build_json_snapshot", return_value=object()), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(root, "workspace-1", "session-1", {"session-1": tuple()}, settings, None)
        self.assertEqual(1, len(captured))
        self.assertEqual(summary, captured[0].summary)
        self.assertEqual("a" * 64, captured[0].evidence_hash)


if __name__ == "__main__":
    unittest.main()