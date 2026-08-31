import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.change_summary_generator import ChangeSummary, SummaryEvidenceItem
from tools.chat_extractor import ChatExtractionResult
from tools.collector_runtime import _build_workspace_snapshot, run_once
from tools.decision_extractor import DecisionInferenceCacheEntry, ExtractedDecision, _Proposal, decision_inference_payload
from tools.next_task_extractor import NextTask, NextTaskCacheEntry, NextTaskIssue
from tools.inference_ledger import InferenceLedgerEntry, append, next_task_payload, summary_payload
from tools.collector_history import CollectorHistory
from tools.collector_state import CollectorState


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


    def test_restart_restores_all_inference_kinds_before_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"
            restored_decision = ExtractedDecision("decision-1", "2026-08-31T00:00:00+00:00", "created_at", "adopted", "title", "description", None, ("session-1",), ("message-1",), None, None, "topic")
            decision_cache = DecisionInferenceCacheEntry("session-1", "turn-1", "b" * 64, (_Proposal("adopted", "title", "description", None, "topic"),), (restored_decision,))
            decision = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "b" * 64, decision_inference_payload(decision_cache), "2026-08-31T00:00:00+00:00", "decision")
            task = NextTask("task-1", "task", "pending", "codex_inferred", "high", "reason", tuple())
            next_entry = InferenceLedgerEntry("workspace-1", "session-1", "turn-1", "c" * 64, next_task_payload(NextTaskCacheEntry("c" * 64, task, None, (NextTaskIssue("warning"),))), "2026-08-31T00:00:00+00:00", "next_task")
            append(ledger, decision)
            append(ledger, next_entry)
            settings = SimpleNamespace(inference_ledger_file=ledger, ai_inference_mode="incremental", tasks_path=Path(directory) / "TASKS.md", output_dir=Path(directory) / "output", queue_dir=Path(directory) / "queue")
            captured = {}
            def decisions(*args, **kwargs):
                captured["decision_cache"] = kwargs["inference_cache"]
                captured["restored_decisions"] = kwargs["restored_decisions"]
                return SimpleNamespace(decisions=tuple())
            def next_task(*args, **kwargs):
                captured["next_task_cache"] = kwargs["cache_entry"]
                return SimpleNamespace(inference_attempted=False, cache_entry=None)
            with patch("tools.collector_runtime.extract_chat_messages", return_value=ChatExtractionResult(tuple(), tuple(), tuple())), patch("tools.collector_runtime.extract_current_work_status", return_value=SimpleNamespace(codex_status="idle")), patch("tools.collector_runtime.extract_decisions", side_effect=decisions), patch("tools.collector_runtime.extract_file_references", return_value=SimpleNamespace(references=tuple())), patch("tools.collector_runtime.extract_development_errors", return_value=object()), patch("tools.collector_runtime.generate_change_summaries", return_value=SimpleNamespace(cache_entries=tuple())), patch("tools.collector_runtime.extract_next_task", side_effect=next_task), patch("tools.collector_runtime.collect_git_changes", return_value=object()), patch("tools.collector_runtime.build_json_snapshot", return_value=object()), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(root, "workspace-1", "session-1", {"session-1": tuple()}, settings, None)
        self.assertIn(("session-1", "turn-1", "b" * 64), captured["decision_cache"])
        self.assertEqual((restored_decision,), captured["restored_decisions"])
        self.assertEqual(task, captured["next_task_cache"].task)

    def test_incremental_mode_passes_only_new_turn_ids_to_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(inference_ledger_file=Path(directory) / "ledger.json", ai_inference_mode="incremental", tasks_path=Path(directory) / "TASKS.md", output_dir=Path(directory) / "output", queue_dir=Path(directory) / "queue")
            captured = {}
            def summaries(*args, **kwargs):
                captured["turn_ids"] = kwargs["inference_turn_ids"]
                return SimpleNamespace(cache_entries=tuple())
            def next_task(*args, **kwargs):
                captured["allow_inference"] = kwargs["allow_inference"]
                return SimpleNamespace(inference_attempted=False, cache_entry=None)
            with patch("tools.collector_runtime.extract_chat_messages", return_value=ChatExtractionResult(tuple(), tuple(), tuple())), patch("tools.collector_runtime.extract_current_work_status", return_value=SimpleNamespace(codex_status="idle")), patch("tools.collector_runtime.extract_decisions", return_value=SimpleNamespace(decisions=tuple())), patch("tools.collector_runtime.extract_file_references", return_value=SimpleNamespace(references=tuple())), patch("tools.collector_runtime.extract_development_errors", return_value=object()), patch("tools.collector_runtime.generate_change_summaries", side_effect=summaries), patch("tools.collector_runtime.extract_next_task", side_effect=next_task), patch("tools.collector_runtime.collect_git_changes", return_value=object()), patch("tools.collector_runtime.build_json_snapshot", return_value=object()), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(root, "workspace-1", "session-1", {"session-1": tuple()}, settings, None, {"session-1": (SimpleNamespace(turn_id="new-turn"),)})
        self.assertEqual({"new-turn"}, captured["turn_ids"])
        self.assertFalse(captured["allow_inference"])

    def test_initial_history_is_not_passed_to_incremental_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(state_file=Path(directory) / "state.json", history_file=Path(directory) / "history.json", sessions_dir=Path(directory), archived_sessions_dir=None, scan_archived_sessions=False, allowed_roots=(root,), ai_inference_mode="incremental")
            session = SimpleNamespace(session_id="session-1", current_file=SimpleNamespace(workspace_candidates=tuple(), last_timestamp="", path=Path(directory) / "session.jsonl"))
            record = SimpleNamespace(turn_id="old-turn")
            captured = []
            with patch("tools.collector_runtime.load_collector_state", return_value=CollectorState()), patch("tools.collector_runtime.load_collector_history", return_value=CollectorHistory()), patch("tools.collector_runtime.discover_session_files", return_value=tuple()), patch("tools.collector_runtime.build_session_index", return_value={"session-1": session}), patch("tools.collector_runtime._workspace_root", return_value=root), patch("tools.collector_runtime.collect_incremental_records", return_value=SimpleNamespace(records=(record,), resume=SimpleNamespace(replay_from_start=True), next_state=CollectorState())), patch("tools.collector_runtime._build_workspace_snapshot", side_effect=lambda *args: captured.append(args[6])), patch("tools.collector_runtime.save_collector_history"), patch("tools.collector_runtime.save_collector_state"):
                self.assertEqual(1, run_once(settings))
        self.assertEqual({"session-1": tuple()}, captured[0])

if __name__ == "__main__":
    unittest.main()
