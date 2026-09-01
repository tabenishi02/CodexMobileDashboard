import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tools.change_summary_generator import ChangeSummary, ChangeSummaryCacheEntry, GeneratedSummaryContent, SummaryEvidenceItem
from tools.chat_extractor import ChatExtractionResult
from tools.combined_inference import (
    COMBINED_INFERENCE_INPUT_MAX_BYTES,
    CombinedInferenceResult,
)
from tools.collector_runtime import InferenceCallBudget, _build_workspace_snapshot, _incremental_completed_turn_ids, _structured_run_result, run_once
from tools.decision_extractor import DecisionInferenceCacheEntry, ExtractedDecision, _Proposal, decision_inference_payload
from tools.next_task_extractor import NextTask, NextTaskCacheEntry, NextTaskIssue
from tools.inference_metrics import InferenceRunMetrics
from tools.inference_ledger import InferenceLedgerEntry, append, latest_decision_history, load, next_task_payload, summary_payload
from tools.collector_history import CollectorHistory
from tools.collector_state import CollectorState, PendingInference
from tools.record_normalizer import NormalizedContentPart, NormalizedRecord


class CollectorRuntimeTests(unittest.TestCase):
    def test_structured_run_result_exposes_safe_inference_progress(self) -> None:
        metrics = InferenceRunMetrics()
        metrics.counts.update(
            execution=3,
            success=2,
            failure=1,
            limit_reached=4,
        )
        metrics.pending_remaining = 7

        result = _structured_run_result(2, metrics)

        self.assertEqual(
            {
                "executions": 3,
                "failures": 1,
                "limit_reached": 4,
                "pending_remaining": 7,
                "processed_workspaces": 2,
                "progress": 2,
                "successes": 2,
            },
            result.as_dict(),
        )

    def test_multiple_workspaces_keep_phase_and_fallback_tasks_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            roots = (base / "workspace-a", base / "workspace-b")
            for root, task in zip(roots, ("Workspace A task", "Workspace B task")):
                root.mkdir()
                (root / "TASKS.md").write_text(
                    "- [ ] " + task + "\n", encoding="utf-8"
                )
            settings = SimpleNamespace(
                inference_ledger_file=base / "ledger.json",
                ai_inference_mode="off",
                output_dir=base / "output",
                queue_dir=base / "queue",
            )
            chats = (
                ChatExtractionResult(
                    (_runtime_message("message-a", "turn-a", "進捗を確認しました。"),),
                    tuple(),
                    tuple(),
                ),
                ChatExtractionResult(
                    (_runtime_message("message-b", "turn-b", "進捗を確認しました。"),),
                    tuple(),
                    tuple(),
                ),
            )
            work = (
                SimpleNamespace(codex_status="idle", turns=(_runtime_turn("turn-a"),)),
                SimpleNamespace(codex_status="idle", turns=(_runtime_turn("turn-b"),)),
            )
            captured = {}

            def capture_snapshot(context, project, _chats, _work, next_task, *_args, **_kwargs):
                captured[context.workspace_id] = (project, next_task)
                return object()

            with patch(
                "tools.collector_runtime.extract_chat_messages", side_effect=chats
            ), patch(
                "tools.collector_runtime.extract_current_work_status", side_effect=work
            ), patch(
                "tools.collector_runtime.extract_decisions",
                return_value=SimpleNamespace(decisions=tuple(), issues=tuple()),
            ), patch(
                "tools.collector_runtime.generate_change_summaries",
                return_value=SimpleNamespace(
                    summaries=tuple(), issues=tuple(), cache_entries=tuple()
                ),
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.build_json_snapshot",
                side_effect=capture_snapshot,
            ), patch("tools.collector_runtime.save_json_snapshot"):
                for index, root in enumerate(roots):
                    session_id = "session-" + str(index)
                    _build_workspace_snapshot(
                        root,
                        "workspace-" + str(index),
                        session_id,
                        {session_id: tuple()},
                        settings,
                        None,
                    )

        project_a, next_task_a = captured["workspace-0"]
        project_b, next_task_b = captured["workspace-1"]
        self.assertIsNone(project_a.phase)
        self.assertIsNone(project_b.phase)
        self.assertEqual("workspace-a", project_a.name)
        self.assertEqual("workspace-b", project_b.name)
        self.assertEqual("Workspace A task", next_task_a.task.text)
        self.assertEqual("Workspace B task", next_task_b.task.text)
        self.assertNotEqual(next_task_a.task.task_id, next_task_b.task.task_id)

    def test_collector_skips_ai_for_known_short_non_change_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(
                inference_ledger_file=Path(directory) / "ledger.json",
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            message = _runtime_message("message-1", "turn-1", "了解しました。")
            chat = ChatExtractionResult((message,), tuple(), tuple())
            work = SimpleNamespace(
                codex_status="idle", turns=(_runtime_turn("turn-1"),)
            )
            combined_runner = Mock()
            decisions = Mock()
            summaries = Mock()
            next_task = Mock()
            budget = InferenceCallBudget()
            with patch(
                "tools.collector_runtime.extract_chat_messages", return_value=chat
            ), patch(
                "tools.collector_runtime.extract_current_work_status", return_value=work
            ), patch(
                "tools.collector_runtime.extract_decisions", decisions
            ), patch(
                "tools.collector_runtime.generate_change_summaries", summaries
            ), patch(
                "tools.collector_runtime.extract_next_task", next_task
            ), patch(
                "tools.collector_runtime.CombinedTurnCliRunner",
                return_value=combined_runner,
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.build_json_snapshot", return_value=object()
            ) as json_builder, patch(
                "tools.collector_runtime.record_inference_metric"
            ) as metric, patch("tools.collector_runtime.save_json_snapshot"):
                remaining = _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                    {"session-1": (_terminal_record("turn-1"),)},
                    budget.try_acquire,
                )

        combined_runner.infer.assert_not_called()
        decisions.assert_not_called()
        summaries.assert_not_called()
        next_task.assert_not_called()
        metric.assert_called_once_with("combined_turn", "skipped")
        self.assertIsNone(json_builder.call_args.args[1].phase)
        self.assertEqual(0, budget.calls)
        self.assertEqual(tuple(), remaining)

    def test_collector_change_candidate_uses_one_combined_cli_and_skips_individual_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"
            settings = SimpleNamespace(
                inference_ledger_file=ledger,
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            outside = _runtime_message(
                "message-outside", "turn-outside", "outside-turn-secret"
            )
            message = _runtime_message("message-1", "turn-1", "実装しました。")
            chat = ChatExtractionResult((outside, message), tuple(), tuple())
            work = SimpleNamespace(
                codex_status="idle",
                turns=(_runtime_turn("turn-1"),),
            )
            terminal = _terminal_record("turn-1")
            combined_runner = Mock()
            combined_runner.infer.return_value = _combined_result()
            decisions = Mock()
            summaries = Mock()
            next_task = Mock()
            budget = InferenceCallBudget()
            with patch(
                "tools.collector_runtime.extract_chat_messages", return_value=chat
            ), patch(
                "tools.collector_runtime.extract_current_work_status", return_value=work
            ), patch(
                "tools.collector_runtime.extract_decisions", decisions
            ), patch(
                "tools.collector_runtime.generate_change_summaries", summaries
            ), patch(
                "tools.collector_runtime.extract_next_task", next_task
            ), patch(
                "tools.collector_runtime.CombinedTurnCliRunner",
                return_value=combined_runner,
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(
                    references=(
                        SimpleNamespace(
                            path="src/target.py",
                            display_name="target.py",
                            scope="workspace",
                            source_message_ids=("message-1",),
                        ),
                        SimpleNamespace(
                            path="secret/outside.txt",
                            display_name="outside.txt",
                            scope="workspace",
                            source_message_ids=("message-outside",),
                        ),
                    )
                ),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.build_json_snapshot", return_value=object()
            ), patch("tools.collector_runtime.save_json_snapshot"):
                remaining = _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                    {"session-1": (terminal,)},
                    budget.try_acquire,
                )
                ledger_kinds = [entry.inference_kind for entry in load(ledger)]

        combined_runner.infer.assert_called_once()
        prompt = combined_runner.infer.call_args.args[0]
        self.assertIn("実装しました。", prompt)
        self.assertIn("src/target.py", prompt)
        self.assertNotIn("outside-turn-secret", prompt)
        self.assertNotIn("secret/outside.txt", prompt)
        self.assertLessEqual(
            len(prompt.encode("utf-8")), COMBINED_INFERENCE_INPUT_MAX_BYTES
        )
        decisions.assert_not_called()
        summaries.assert_not_called()
        next_task.assert_not_called()
        self.assertEqual(1, budget.calls)
        self.assertEqual(tuple(), remaining)
        self.assertEqual(["combined_turn"], ledger_kinds)

    def test_combined_proposals_build_complete_history_before_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"
            old = ExtractedDecision(
                "decision-old",
                "2026-08-30T00:00:00+00:00",
                "message_timestamp",
                "adopted",
                "Old decision",
                "Old description",
                None,
                ("session-1",),
                ("message-old",),
                None,
                None,
                "topic",
            )
            old_cache = DecisionInferenceCacheEntry(
                "session-1",
                "turn-old",
                "b" * 64,
                (_Proposal("adopted", "Old decision", "Old description", None, "topic"),),
                (old,),
            )
            append(
                ledger,
                InferenceLedgerEntry(
                    "workspace-1",
                    "session-1",
                    "turn-old",
                    "b" * 64,
                    decision_inference_payload(old_cache),
                    "2026-08-30T00:00:00+00:00",
                    "decision",
                ),
            )
            settings = SimpleNamespace(
                inference_ledger_file=ledger,
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            message = _runtime_message(
                "message-new", "turn-1", "Implemented the replacement decision."
            )
            message.created_at = "2026-08-31T00:00:00+00:00"
            chat = ChatExtractionResult((message,), tuple(), tuple())
            work = SimpleNamespace(
                codex_status="idle", turns=(_runtime_turn("turn-1"),)
            )
            combined_runner = Mock()
            combined_runner.infer.return_value = _combined_result()
            saved_conversions = []
            json_builder = Mock(return_value=object())
            with patch(
                "tools.collector_runtime.extract_chat_messages", return_value=chat
            ), patch(
                "tools.collector_runtime.extract_current_work_status", return_value=work
            ), patch(
                "tools.collector_runtime.CombinedTurnCliRunner",
                return_value=combined_runner,
            ), patch(
                "tools.collector_runtime.save_combined_turn",
                side_effect=lambda *args, **kwargs: saved_conversions.append(
                    kwargs["conversion"]
                ),
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.build_json_snapshot", json_builder
            ), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                    {"session-1": (_terminal_record("turn-1"),)},
                )

        history = saved_conversions[0].decision_history
        self.assertEqual(2, len(history))
        self.assertEqual("superseded", history[0].status)
        self.assertEqual(history[1].decision_id, history[0].superseded_by)
        self.assertEqual(history[0].decision_id, history[1].supersedes)
        self.assertEqual("2026-08-30T00:00:00+00:00", history[0].decided_at)
        self.assertEqual("2026-08-31T00:00:00+00:00", history[1].decided_at)
        self.assertEqual(("session-1",), history[1].source_session_ids)
        self.assertEqual(("message-new",), history[1].source_message_ids)
        self.assertEqual(history, json_builder.call_args.args[6].decisions)

    def test_collector_falls_back_only_for_failed_or_ineligible_combined_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(
                inference_ledger_file=Path(directory) / "ledger.json",
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            messages = (
                _runtime_message("message-1", "turn-1"),
                _runtime_message("message-2", "turn-2"),
            )
            combined_runner = Mock()
            combined_runner.infer.side_effect = RuntimeError("codex_nonzero_exit")
            decisions = Mock(
                return_value=SimpleNamespace(decisions=tuple(), issues=tuple())
            )
            summaries = Mock(
                return_value=SimpleNamespace(cache_entries=tuple(), issues=tuple())
            )
            next_task = Mock(
                return_value=SimpleNamespace(
                    inference_attempted=False, cache_entry=None, issues=tuple()
                )
            )
            common_patches = (
                patch(
                    "tools.collector_runtime.extract_decisions", decisions
                ),
                patch(
                    "tools.collector_runtime.generate_change_summaries", summaries
                ),
                patch("tools.collector_runtime.extract_next_task", next_task),
                patch(
                    "tools.collector_runtime.CombinedTurnCliRunner",
                    return_value=combined_runner,
                ),
                patch(
                    "tools.collector_runtime.extract_file_references",
                    return_value=SimpleNamespace(references=tuple()),
                ),
                patch(
                    "tools.collector_runtime.extract_development_errors",
                    return_value=object(),
                ),
                patch(
                    "tools.collector_runtime.collect_git_changes",
                    return_value=_runtime_git(),
                ),
                patch(
                    "tools.collector_runtime.build_json_snapshot", return_value=object()
                ),
                patch("tools.collector_runtime.save_json_snapshot"),
            )
            with common_patches[0], common_patches[1], common_patches[2], common_patches[3], common_patches[4], common_patches[5], common_patches[6], common_patches[7], common_patches[8]:
                with patch(
                    "tools.collector_runtime.extract_chat_messages",
                    return_value=ChatExtractionResult((messages[0],), tuple(), tuple()),
                ), patch(
                    "tools.collector_runtime.extract_current_work_status",
                    return_value=SimpleNamespace(
                        codex_status="idle", turns=(_runtime_turn("turn-1"),)
                    ),
                ):
                    _build_workspace_snapshot(
                        root,
                        "workspace-1",
                        "session-1",
                        {"session-1": tuple()},
                        settings,
                        None,
                        {"session-1": (_terminal_record("turn-1"),)},
                    )
                with patch(
                    "tools.collector_runtime.extract_chat_messages",
                    return_value=ChatExtractionResult(messages, tuple(), tuple()),
                ), patch(
                    "tools.collector_runtime.extract_current_work_status",
                    return_value=SimpleNamespace(
                        codex_status="idle",
                        turns=(_runtime_turn("turn-1"), _runtime_turn("turn-2")),
                    ),
                ):
                    _build_workspace_snapshot(
                        root,
                        "workspace-1",
                        "session-1",
                        {"session-1": tuple()},
                        settings,
                        None,
                        {
                            "session-1": (
                                _terminal_record("turn-1"),
                                _terminal_record("turn-2"),
                            )
                        },
                    )

        self.assertEqual(1, combined_runner.infer.call_count)
        self.assertEqual(2, decisions.call_count)
        self.assertEqual(2, summaries.call_count)
        self.assertEqual(2, next_task.call_count)

    def test_pending_routes_precede_new_turn_and_only_successes_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(
                inference_ledger_file=Path(directory) / "ledger.json",
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )

            def message(message_id, turn_id):
                return SimpleNamespace(
                    message_id=message_id,
                    created_at=message_id,
                    turn_id=turn_id,
                    role="assistant",
                    message_type="chat",
                    phase="final_answer",
                    content=(SimpleNamespace(kind="text", text="masked"),),
                )

            old_message = message("01-old", "old-turn")
            new_message = message("02-new", "new-turn")
            chat = ChatExtractionResult(
                (old_message, new_message), tuple(), tuple()
            )
            work = SimpleNamespace(
                codex_status="idle",
                turns=(
                    SimpleNamespace(
                        turn_id="old-turn", status="completed", rolled_back=False
                    ),
                    SimpleNamespace(
                        turn_id="new-turn", status="completed", rolled_back=False
                    ),
                ),
            )
            terminal = SimpleNamespace(
                category="turn",
                subtype="turn_status",
                turn_id="new-turn",
                attributes={"status": "completed"},
            )
            initial = tuple(
                PendingInference("workspace-1", "session-1", "old-turn", kind)
                for kind in ("decision", "change_summary", "next_task")
            )
            captured = {}

            def decisions(sources, *args, **kwargs):
                captured["decision_turns"] = {
                    source.message.turn_id for source in sources
                }
                return SimpleNamespace(
                    decisions=tuple(),
                    issues=(
                        SimpleNamespace(
                            session_id="session-1",
                            message_id="01-old",
                            kind="codex_timeout",
                        ),
                    ),
                )

            def summaries(*args, **kwargs):
                captured["summary_turns"] = kwargs["inference_turn_ids"]
                return SimpleNamespace(cache_entries=tuple(), issues=tuple())

            def next_task(messages, *args, **kwargs):
                captured["next_turns"] = {message.turn_id for message in messages}
                return SimpleNamespace(
                    inference_attempted=False,
                    cache_entry=None,
                    issues=(SimpleNamespace(kind="inference_limit_reached"),),
                )

            with patch(
                "tools.collector_runtime.extract_chat_messages", return_value=chat
            ), patch(
                "tools.collector_runtime.extract_current_work_status", return_value=work
            ), patch(
                "tools.collector_runtime.extract_decisions", side_effect=decisions
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.generate_change_summaries",
                side_effect=summaries,
            ), patch(
                "tools.collector_runtime.extract_next_task", side_effect=next_task
            ), patch(
                "tools.collector_runtime.collect_git_changes", return_value=object()
            ), patch(
                "tools.collector_runtime.build_json_snapshot", return_value=object()
            ), patch(
                "tools.collector_runtime._try_combined_inference", return_value=None
            ), patch("tools.collector_runtime.save_json_snapshot"):
                remaining = _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                    {"session-1": (terminal,)},
                    pending_inferences=initial,
                )

        self.assertEqual({"old-turn"}, captured["decision_turns"])
        self.assertEqual({"old-turn"}, captured["summary_turns"])
        self.assertEqual({"old-turn"}, captured["next_turns"])
        self.assertEqual(
            {
                (item.turn_id, item.inference_kind) for item in remaining
            },
            {
                ("old-turn", "decision"),
                ("old-turn", "next_task"),
                ("new-turn", "decision"),
                ("new-turn", "change_summary"),
                ("new-turn", "next_task"),
            },
        )

    def test_pending_success_after_restart_is_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(
                inference_ledger_file=Path(directory) / "ledger.json",
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            message = SimpleNamespace(
                message_id="message-1",
                created_at="01",
                turn_id="turn-1",
                role="assistant",
                message_type="chat",
                phase="final_answer",
                content=(SimpleNamespace(kind="text", text="masked"),),
            )
            pending = (
                PendingInference(
                    "workspace-1", "session-1", "turn-1", "decision"
                ),
            )
            with patch(
                "tools.collector_runtime.extract_chat_messages",
                return_value=ChatExtractionResult((message,), tuple(), tuple()),
            ), patch(
                "tools.collector_runtime.extract_current_work_status",
                return_value=SimpleNamespace(codex_status="idle", turns=tuple()),
            ), patch(
                "tools.collector_runtime.extract_decisions",
                return_value=SimpleNamespace(decisions=tuple(), issues=tuple()),
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.generate_change_summaries",
                return_value=SimpleNamespace(cache_entries=tuple(), issues=tuple()),
            ), patch(
                "tools.collector_runtime.extract_next_task",
                return_value=SimpleNamespace(
                    inference_attempted=False, cache_entry=None, issues=tuple()
                ),
            ), patch(
                "tools.collector_runtime.collect_git_changes", return_value=object()
            ), patch(
                "tools.collector_runtime.build_json_snapshot", return_value=object()
            ), patch("tools.collector_runtime.save_json_snapshot"):
                remaining = _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                    pending_inferences=pending,
                )

        self.assertEqual(tuple(), remaining)

    def test_run_log_reports_pending_counts_without_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pending = PendingInference(
                "secret-workspace",
                "secret-session",
                "secret-turn",
                "decision",
            )
            settings = SimpleNamespace(
                state_file=Path(directory) / "state.json",
                history_file=Path(directory) / "history.json",
                sessions_dir=Path(directory),
                archived_sessions_dir=None,
                scan_archived_sessions=False,
                allowed_roots=tuple(),
                max_calls_per_run=3,
            )
            saved = []
            with patch(
                "tools.collector_runtime.load_collector_state",
                return_value=CollectorState(pending_inferences=(pending,)),
            ), patch(
                "tools.collector_runtime.load_collector_history",
                return_value=CollectorHistory(),
            ), patch(
                "tools.collector_runtime.discover_session_files",
                return_value=tuple(),
            ), patch(
                "tools.collector_runtime.build_session_index", return_value={}
            ), patch(
                "tools.collector_runtime.save_collector_history"
            ), patch(
                "tools.collector_runtime.save_collector_state",
                side_effect=lambda state, path: saved.append(state),
            ), self.assertLogs("collector", level="INFO") as logs:
                result = run_once(settings)

        self.assertEqual(0, result.processed_workspaces)
        self.assertEqual(0, result.executions)
        self.assertEqual(0, result.successes)
        self.assertEqual(0, result.failures)
        self.assertEqual(0, result.limit_reached)
        self.assertEqual(1, result.pending_remaining)
        self.assertEqual(0, result.progress)

        output = "\n".join(logs.output)
        self.assertIn(
            "inference_pending_completed carried=1 added=0 remaining=1", output
        )
        self.assertIn(
            "inference_run_metrics executions=0 cache_hits=0 skipped=0 "
            "limit_reached=0 successes=0 failures=0 fallbacks=0 "
            "input_bytes=0 pending_remaining=1",
            output,
        )
        self.assertNotIn("secret-", output)
        self.assertEqual((pending,), saved[0].pending_inferences)

    def test_default_budget_is_shared_across_all_individual_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(inference_ledger_file=Path(directory) / "ledger.json", ai_inference_mode="backfill", tasks_path=Path(directory) / "TASKS.md", output_dir=Path(directory) / "output", queue_dir=Path(directory) / "queue")
            budget = InferenceCallBudget()
            acquired = []
            def decisions(*args, **kwargs):
                acquired.extend(("decision", kwargs["can_infer"]()) for _ in range(2))
                return SimpleNamespace(decisions=tuple())
            def summaries(*args, **kwargs):
                acquired.extend(("summary", kwargs["can_infer"]()) for _ in range(2))
                return SimpleNamespace(cache_entries=tuple())
            def next_task(*args, **kwargs):
                acquired.append(("next_task", kwargs["can_infer"]()))
                return SimpleNamespace(inference_attempted=False, cache_entry=None)
            work = SimpleNamespace(codex_status="idle", turns=tuple())
            with patch("tools.collector_runtime.extract_chat_messages", return_value=ChatExtractionResult(tuple(), tuple(), tuple())), patch("tools.collector_runtime.extract_current_work_status", return_value=work), patch("tools.collector_runtime.extract_decisions", side_effect=decisions), patch("tools.collector_runtime.extract_file_references", return_value=SimpleNamespace(references=tuple())), patch("tools.collector_runtime.extract_development_errors", return_value=object()), patch("tools.collector_runtime.generate_change_summaries", side_effect=summaries), patch("tools.collector_runtime.extract_next_task", side_effect=next_task), patch("tools.collector_runtime.collect_git_changes", return_value=object()), patch("tools.collector_runtime.build_json_snapshot", return_value=object()), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(root, "workspace-1", "session-1", {"session-1": tuple()}, settings, None, can_infer=budget.try_acquire)

        self.assertEqual(
            [("decision", True), ("decision", True), ("summary", True), ("summary", False), ("next_task", False)],
            acquired,
        )
        self.assertEqual(3, budget.calls)
        self.assertEqual(2, budget.deferred)
        self.assertEqual(3, budget.limit)

    def test_incremental_completed_turn_ids_include_terminal_failures_and_exclude_rollbacks(self) -> None:
        work = SimpleNamespace(turns=(
            SimpleNamespace(turn_id="completed", status="completed", rolled_back=False),
            SimpleNamespace(turn_id="failed", status="failed", rolled_back=False),
            SimpleNamespace(turn_id="rolled-back", status="completed", rolled_back=True),
            SimpleNamespace(turn_id="working", status="in_progress", rolled_back=False),
        ))
        records = {"session-1": (
            SimpleNamespace(category="turn", subtype="turn_status", turn_id="completed", attributes={"status": "completed"}),
            SimpleNamespace(category="turn", subtype="turn_status", turn_id="failed", attributes={"status": "aborted"}),
            SimpleNamespace(category="turn", subtype="turn_status", turn_id="rolled-back", attributes={"status": "completed"}),
            SimpleNamespace(category="turn", subtype="turn_status", turn_id="working", attributes={"status": "started"}),
        )}

        self.assertEqual(
            {"completed", "failed"},
            _incremental_completed_turn_ids(work, records),
        )

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

    def test_incremental_mode_passes_only_newly_completed_turn_ids_to_inference(self) -> None:
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
            work = SimpleNamespace(codex_status="idle", turns=(SimpleNamespace(turn_id="new-turn", status="completed", rolled_back=False),))
            terminal = SimpleNamespace(category="turn", subtype="turn_status", turn_id="new-turn", attributes={"status": "completed"})
            with patch("tools.collector_runtime.extract_chat_messages", return_value=ChatExtractionResult(tuple(), tuple(), tuple())), patch("tools.collector_runtime.extract_current_work_status", return_value=work), patch("tools.collector_runtime.extract_decisions", return_value=SimpleNamespace(decisions=tuple())), patch("tools.collector_runtime.extract_file_references", return_value=SimpleNamespace(references=tuple())), patch("tools.collector_runtime.extract_development_errors", return_value=object()), patch("tools.collector_runtime.generate_change_summaries", side_effect=summaries), patch("tools.collector_runtime.extract_next_task", side_effect=next_task), patch("tools.collector_runtime.collect_git_changes", return_value=object()), patch("tools.collector_runtime.build_json_snapshot", return_value=object()), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(root, "workspace-1", "session-1", {"session-1": tuple()}, settings, None, {"session-1": (terminal,)})
        self.assertEqual({"new-turn"}, captured["turn_ids"])
        self.assertFalse(captured["allow_inference"])

    def test_in_progress_turn_is_withheld_until_terminal_event_for_all_routes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(inference_ledger_file=Path(directory) / "ledger.json", ai_inference_mode="incremental", tasks_path=Path(directory) / "TASKS.md", output_dir=Path(directory) / "output", queue_dir=Path(directory) / "queue")
            def message(message_id, role, phase, text):
                return SimpleNamespace(message_id=message_id, created_at=message_id, turn_id="turn-1", role=role, message_type="chat", phase=phase, content=(SimpleNamespace(kind="text", text=text),))
            user = message("01-user", "user", None, "案Aを採用します。")
            commentary = message("02-commentary", "assistant", "commentary", "作業中です。")
            final = message("03-final", "assistant", "final_answer", "完了しました。")
            partial_chat = ChatExtractionResult((user, commentary), tuple(), tuple())
            completed_chat = ChatExtractionResult((user, final), tuple(), tuple())
            in_progress = SimpleNamespace(codex_status="working", turns=(SimpleNamespace(turn_id="turn-1", status="in_progress", rolled_back=False),))
            completed = SimpleNamespace(codex_status="idle", turns=(SimpleNamespace(turn_id="turn-1", status="completed", rolled_back=False),))
            started_record = SimpleNamespace(category="turn", subtype="turn_status", turn_id="turn-1", attributes={"status": "started"})
            completed_record = SimpleNamespace(category="turn", subtype="turn_status", turn_id="turn-1", attributes={"status": "completed"})
            captured = {"decisions": [], "summaries": [], "next": []}
            def decisions(sources, *args, **kwargs):
                captured["decisions"].append(tuple(item.message.turn_id for item in sources))
                return SimpleNamespace(decisions=tuple())
            def summaries(*args, **kwargs):
                captured["summaries"].append(kwargs["inference_turn_ids"])
                return SimpleNamespace(cache_entries=tuple())
            def next_task(messages, *args, **kwargs):
                captured["next"].append((kwargs["allow_inference"], tuple(item.turn_id for item in messages)))
                return SimpleNamespace(inference_attempted=False, cache_entry=None)
            with patch("tools.collector_runtime.extract_chat_messages", side_effect=(partial_chat, completed_chat)), patch("tools.collector_runtime.extract_current_work_status", side_effect=(in_progress, completed)), patch("tools.collector_runtime.extract_decisions", side_effect=decisions), patch("tools.collector_runtime.extract_file_references", return_value=SimpleNamespace(references=tuple())), patch("tools.collector_runtime.extract_development_errors", return_value=object()), patch("tools.collector_runtime.generate_change_summaries", side_effect=summaries), patch("tools.collector_runtime.extract_next_task", side_effect=next_task), patch("tools.collector_runtime.collect_git_changes", return_value=object()), patch("tools.collector_runtime._try_combined_inference", return_value=None), patch("tools.collector_runtime.build_json_snapshot", return_value=object()), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(root, "workspace-1", "session-1", {"session-1": tuple()}, settings, None, {"session-1": (started_record,)})
                _build_workspace_snapshot(root, "workspace-1", "session-1", {"session-1": tuple()}, settings, None, {"session-1": (completed_record,)})

        self.assertEqual([tuple(), ("turn-1", "turn-1")], captured["decisions"])
        self.assertEqual([set(), {"turn-1"}], captured["summaries"])
        self.assertEqual([(False, tuple()), (True, ("turn-1", "turn-1"))], captured["next"])

    def test_initial_history_is_not_passed_to_incremental_inference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(state_file=Path(directory) / "state.json", history_file=Path(directory) / "history.json", sessions_dir=Path(directory), archived_sessions_dir=None, scan_archived_sessions=False, allowed_roots=(root,), ai_inference_mode="incremental")
            session = SimpleNamespace(session_id="session-1", current_file=SimpleNamespace(workspace_candidates=tuple(), last_timestamp="", path=Path(directory) / "session.jsonl"))
            record = _terminal_record("old-turn")
            captured = []
            with patch("tools.collector_runtime.load_collector_state", return_value=CollectorState()), patch("tools.collector_runtime.load_collector_history", return_value=CollectorHistory()), patch("tools.collector_runtime.discover_session_files", return_value=tuple()), patch("tools.collector_runtime.build_session_index", return_value={"session-1": session}), patch("tools.collector_runtime._workspace_root", return_value=root), patch("tools.collector_runtime.collect_incremental_records", return_value=SimpleNamespace(records=(record,), resume=SimpleNamespace(replay_from_start=True), next_state=CollectorState())), patch("tools.collector_runtime._build_workspace_snapshot", side_effect=lambda *args: captured.append(args[6])), patch("tools.collector_runtime.save_collector_history"), patch("tools.collector_runtime.save_collector_state"):
                self.assertEqual(1, run_once(settings).processed_workspaces)
        self.assertEqual({"session-1": tuple()}, captured[0])

    def test_incremental_second_run_without_new_records_starts_no_cli(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(
                inference_ledger_file=Path(directory) / "ledger.json",
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            message = _runtime_message(
                "message-1", "turn-1", "Implemented the requested source change."
            )
            chat = ChatExtractionResult((message,), tuple(), tuple())
            work = SimpleNamespace(
                codex_status="idle", turns=(_runtime_turn("turn-1"),)
            )
            combined_runner = Mock()
            combined_runner.infer.return_value = _combined_result()
            decision_runner = Mock()
            summary_runner = Mock()
            next_task_runner = Mock()
            budget = InferenceCallBudget()
            with patch(
                "tools.collector_runtime.extract_chat_messages", return_value=chat
            ), patch(
                "tools.collector_runtime.extract_current_work_status", return_value=work
            ), patch(
                "tools.collector_runtime.CombinedTurnCliRunner",
                return_value=combined_runner,
            ), patch(
                "tools.decision_extractor.DecisionCliRunner",
                return_value=decision_runner,
            ), patch(
                "tools.change_summary_generator.ChangeSummaryCliRunner",
                return_value=summary_runner,
            ), patch(
                "tools.next_task_extractor.CodexCliRunner",
                return_value=next_task_runner,
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.build_json_snapshot", return_value=object()
            ), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                    {"session-1": (_terminal_record("turn-1"),)},
                    budget.try_acquire,
                )
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                    {"session-1": tuple()},
                    budget.try_acquire,
                )

        combined_runner.infer.assert_called_once()
        decision_runner.extract.assert_not_called()
        summary_runner.generate.assert_not_called()
        next_task_runner.infer.assert_not_called()
        self.assertEqual(1, budget.calls)

    def test_combined_decisions_survive_empty_run_and_restart_with_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"

            def settings():
                return SimpleNamespace(
                    inference_ledger_file=ledger,
                    ai_inference_mode="incremental",
                    tasks_path=Path(directory) / "TASKS.md",
                    output_dir=Path(directory) / "output",
                    queue_dir=Path(directory) / "queue",
                )

            first_message = _runtime_message(
                "message-1", "turn-1", "Implemented the first decision."
            )
            first_message.created_at = "2026-08-31T00:01:00+00:00"
            second_message = _runtime_message(
                "message-2", "turn-2", "Implemented the replacement decision."
            )
            second_message.created_at = "2026-08-31T00:02:00+00:00"
            first_chat = ChatExtractionResult((first_message,), tuple(), tuple())
            second_chat = ChatExtractionResult(
                (first_message, second_message), tuple(), tuple()
            )
            first_work = SimpleNamespace(
                codex_status="idle", turns=(_runtime_turn("turn-1"),)
            )
            second_work = SimpleNamespace(
                codex_status="idle",
                turns=(_runtime_turn("turn-1"), _runtime_turn("turn-2")),
            )
            combined_runner = Mock()
            combined_runner.infer.side_effect = (
                _combined_result("First decision", "First description"),
                _combined_result("Replacement decision", "Replacement description"),
            )
            displayed_histories = []

            def capture_snapshot(*args, **kwargs):
                displayed_histories.append(tuple(args[6].decisions))
                return object()

            first_budget = InferenceCallBudget()
            restarted_budget = InferenceCallBudget()
            with patch(
                "tools.collector_runtime.extract_chat_messages",
                side_effect=(first_chat, first_chat, second_chat),
            ), patch(
                "tools.collector_runtime.extract_current_work_status",
                side_effect=(first_work, first_work, second_work),
            ), patch(
                "tools.collector_runtime.CombinedTurnCliRunner",
                return_value=combined_runner,
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.build_json_snapshot",
                side_effect=capture_snapshot,
            ), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings(),
                    None,
                    {"session-1": (_terminal_record("turn-1"),)},
                    first_budget.try_acquire,
                )
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings(),
                    None,
                    {"session-1": tuple()},
                    first_budget.try_acquire,
                )
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings(),
                    None,
                    {"session-1": (_terminal_record("turn-2"),)},
                    restarted_budget.try_acquire,
                )

            restored = latest_decision_history(load(ledger), "workspace-1")

        self.assertEqual(2, combined_runner.infer.call_count)
        self.assertEqual(1, first_budget.calls)
        self.assertEqual(1, restarted_budget.calls)
        self.assertEqual(displayed_histories[0], displayed_histories[1])
        self.assertEqual(1, len(displayed_histories[1]))
        self.assertEqual(2, len(displayed_histories[2]))
        previous, replacement = displayed_histories[2]
        self.assertEqual("superseded", previous.status)
        self.assertEqual(replacement.decision_id, previous.superseded_by)
        self.assertEqual(previous.decision_id, replacement.supersedes)
        self.assertEqual(displayed_histories[2], restored)

    def test_combined_cache_change_and_failure_preserve_snapshot_models_and_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"
            settings = SimpleNamespace(
                inference_ledger_file=ledger,
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            chats = (
                ChatExtractionResult(
                    (
                        _runtime_message(
                            "message-1", "turn-1", "Implemented source change A."
                        ),
                    ),
                    tuple(),
                    tuple(),
                ),
                ChatExtractionResult(
                    (
                        _runtime_message(
                            "message-1", "turn-1", "Implemented source change A."
                        ),
                    ),
                    tuple(),
                    tuple(),
                ),
                ChatExtractionResult(
                    (
                        _runtime_message(
                            "message-1", "turn-1", "Implemented source change B."
                        ),
                    ),
                    tuple(),
                    tuple(),
                ),
            )
            work = SimpleNamespace(
                codex_status="idle", turns=(_runtime_turn("turn-1"),)
            )
            combined_runner = Mock()
            combined_runner.infer.side_effect = (
                _combined_result(),
                RuntimeError("codex_nonzero_exit"),
            )
            models = []
            documents = []

            def render_snapshot(
                context,
                project,
                chat,
                current_work,
                next_task,
                errors,
                decisions,
                files,
                git,
                summaries,
                collector,
                **kwargs,
            ):
                models.append((next_task, decisions, summaries))
                document = {
                    "change_summaries": [
                        summary_payload(item) for item in summaries.summaries
                    ],
                    "decisions": [vars(item) for item in decisions.decisions],
                    "next_task": vars(next_task.task) if next_task.task else None,
                }
                documents.append(document)
                return SimpleNamespace(documents={"inference.json": document})

            decisions = Mock(side_effect=lambda *args, **kwargs: models[0][1])
            summaries = Mock(
                side_effect=lambda *args, **kwargs: SimpleNamespace(
                    summaries=models[0][2].summaries,
                    issues=tuple(),
                    cache_entries=tuple(),
                    inference_calls=0,
                )
            )
            next_task = Mock(
                side_effect=lambda *args, **kwargs: SimpleNamespace(
                    task=models[0][0].task,
                    issues=tuple(),
                    cache_entry=None,
                    inference_attempted=False,
                    inference_input_bytes=0,
                    inference_input_truncated=False,
                )
            )
            with patch(
                "tools.collector_runtime.extract_chat_messages", side_effect=chats
            ), patch(
                "tools.collector_runtime.extract_current_work_status", return_value=work
            ), patch(
                "tools.collector_runtime.CombinedTurnCliRunner",
                return_value=combined_runner,
            ), patch(
                "tools.collector_runtime.extract_decisions", decisions
            ), patch(
                "tools.collector_runtime.generate_change_summaries", summaries
            ), patch(
                "tools.collector_runtime.extract_next_task", next_task
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.build_json_snapshot",
                side_effect=render_snapshot,
            ), patch("tools.collector_runtime.save_json_snapshot"):
                for _ in range(3):
                    _build_workspace_snapshot(
                        root,
                        "workspace-1",
                        "session-1",
                        {"session-1": tuple()},
                        settings,
                        None,
                        {"session-1": (_terminal_record("turn-1"),)},
                    )
                ledger_kinds = [item.inference_kind for item in load(ledger)]

        self.assertEqual(2, combined_runner.infer.call_count)
        self.assertEqual(documents[0], documents[1])
        self.assertEqual(documents[0], documents[2])
        self.assertEqual(["combined_turn"], ledger_kinds)
        decisions.assert_called_once()
        summaries.assert_called_once()
        next_task.assert_called_once()

    def test_failed_individual_inference_results_are_not_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"
            settings = SimpleNamespace(
                inference_ledger_file=ledger,
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            message = _runtime_message(
                "message-1", "turn-1", "Implemented source change."
            )
            failed_task = NextTask(
                "task-fallback",
                "Fallback task",
                "pending",
                "fallback",
                "low",
                "codex_nonzero_exit",
                ("message-1",),
            )
            with patch(
                "tools.collector_runtime.extract_chat_messages",
                return_value=ChatExtractionResult((message,), tuple(), tuple()),
            ), patch(
                "tools.collector_runtime.extract_current_work_status",
                return_value=SimpleNamespace(
                    codex_status="idle", turns=(_runtime_turn("turn-1"),)
                ),
            ), patch(
                "tools.collector_runtime._try_combined_inference", return_value=None
            ), patch(
                "tools.collector_runtime.extract_decisions",
                return_value=SimpleNamespace(decisions=tuple(), issues=tuple()),
            ), patch(
                "tools.collector_runtime.generate_change_summaries",
                return_value=SimpleNamespace(
                    summaries=tuple(),
                    issues=tuple(),
                    cache_entries=(SimpleNamespace(summary=None),),
                ),
            ), patch(
                "tools.collector_runtime.extract_next_task",
                return_value=SimpleNamespace(
                    task=failed_task,
                    issues=tuple(),
                    cache_entry=NextTaskCacheEntry(
                        "a" * 64, failed_task, None, tuple()
                    ),
                    inference_attempted=True,
                ),
            ) as next_task_extractor, patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.build_json_snapshot", return_value=object()
            ), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                    {"session-1": (_terminal_record("turn-1"),)},
                )

            self.assertEqual(tuple(), load(ledger))
            self.assertEqual(root / "TASKS.md", next_task_extractor.call_args.args[2])

    def test_change_summary_success_is_persisted_before_generator_returns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"
            settings = SimpleNamespace(
                inference_ledger_file=ledger,
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            summary = ChangeSummary("summary-1", "turn-1", "jsonl", "completed", False, "title", "short", "details", tuple(), tuple(), "codex_generated", "high", ("session-1",), ("message-1",))
            cache_entry = ChangeSummaryCacheEntry(
                "turn-1", "a" * 64, summary, None, tuple()
            )

            def interrupt_after_success(*args, **kwargs):
                kwargs["on_inference_success"](cache_entry)
                raise RuntimeError("interrupted_after_summary_success")

            with patch(
                "tools.collector_runtime.extract_chat_messages",
                return_value=ChatExtractionResult(tuple(), tuple(), tuple()),
            ), patch(
                "tools.collector_runtime.extract_current_work_status",
                return_value=SimpleNamespace(codex_status="idle", turns=tuple()),
            ), patch(
                "tools.collector_runtime._try_combined_inference", return_value=None
            ), patch(
                "tools.collector_runtime.extract_decisions",
                return_value=SimpleNamespace(decisions=tuple(), issues=tuple()),
            ), patch(
                "tools.collector_runtime.generate_change_summaries",
                side_effect=interrupt_after_success,
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "interrupted_after_summary_success"
                ):
                    _build_workspace_snapshot(
                        root,
                        "workspace-1",
                        "session-1",
                        {"session-1": tuple()},
                        settings,
                        None,
                    )

            entries = load(ledger)

        self.assertEqual(1, len(entries))
        self.assertEqual("change_summary", entries[0].inference_kind)
        self.assertEqual("session-1", entries[0].session_id)
        self.assertEqual("turn-1", entries[0].turn_id)
        self.assertEqual("a" * 64, entries[0].input_sha256)
        self.assertEqual(
            {"schema_version": 1, "payload": summary_payload(summary)},
            entries[0].result,
        )

    def test_change_summary_success_uses_only_one_collector_save_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"
            settings = SimpleNamespace(
                inference_ledger_file=ledger,
                ai_inference_mode="incremental",
                tasks_path=Path(directory) / "TASKS.md",
                output_dir=Path(directory) / "output",
                queue_dir=Path(directory) / "queue",
            )
            summary = ChangeSummary("summary-1", "turn-1", "jsonl", "completed", False, "title", "short", "details", tuple(), tuple(), "codex_generated", "high", ("session-1",), ("message-1",))
            cache_entry = ChangeSummaryCacheEntry(
                "turn-1", "a" * 64, summary, None, tuple()
            )

            def generate_success(*args, **kwargs):
                kwargs["on_inference_success"](cache_entry)
                return SimpleNamespace(
                    summaries=(summary,), issues=tuple(), cache_entries=(cache_entry,)
                )

            with patch(
                "tools.collector_runtime.extract_chat_messages",
                return_value=ChatExtractionResult(tuple(), tuple(), tuple()),
            ), patch(
                "tools.collector_runtime.extract_current_work_status",
                return_value=SimpleNamespace(codex_status="idle", turns=tuple()),
            ), patch(
                "tools.collector_runtime._try_combined_inference", return_value=None
            ), patch(
                "tools.collector_runtime.extract_decisions",
                return_value=SimpleNamespace(decisions=tuple(), issues=tuple()),
            ), patch(
                "tools.collector_runtime.generate_change_summaries",
                side_effect=generate_success,
            ), patch(
                "tools.collector_runtime.extract_next_task",
                return_value=SimpleNamespace(
                    task=None,
                    issues=tuple(),
                    cache_entry=None,
                    inference_attempted=False,
                ),
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.append_inference_ledger", wraps=append
            ) as save, patch(
                "tools.collector_runtime.build_json_snapshot", return_value=object()
            ), patch("tools.collector_runtime.save_json_snapshot"):
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings,
                    None,
                )

            entries = load(ledger)

        save.assert_called_once()
        self.assertEqual(1, len(entries))
        self.assertEqual("change_summary", entries[0].inference_kind)

    def test_change_summary_restart_reuses_first_turn_after_second_turn_interruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledger = Path(directory) / "ledger.json"

            def settings():
                return SimpleNamespace(
                    inference_ledger_file=ledger,
                    ai_inference_mode="incremental",
                    tasks_path=Path(directory) / "TASKS.md",
                    output_dir=Path(directory) / "output",
                    queue_dir=Path(directory) / "queue",
                )

            messages = (
                _runtime_message("message-1", "turn-1", "確認中です A。"),
                _runtime_message("message-2", "turn-2", "確認中です B。"),
            )
            chat = ChatExtractionResult(messages, tuple(), tuple())

            def summary_turn(turn_id, message_id):
                return SimpleNamespace(
                    turn_id=turn_id,
                    turn_id_source="jsonl",
                    status="completed",
                    rolled_back=False,
                    user_message_id=None,
                    assistant_message_ids=(message_id,),
                )

            work = SimpleNamespace(
                codex_status="idle",
                turns=(
                    summary_turn("turn-1", "message-1"),
                    summary_turn("turn-2", "message-2"),
                ),
            )

            def content(message_id):
                return GeneratedSummaryContent(
                    "title",
                    "short",
                    "details",
                    (SummaryEvidenceItem("evidence", (message_id,)),),
                    tuple(),
                    "high",
                )

            first_runner = Mock()
            first_runner.generate.side_effect = (
                content("message-1"),
                KeyboardInterrupt(),
            )
            restarted_runner = Mock()

            def restarted_generate(prompt):
                message_id = "message-1" if "message-1" in prompt else "message-2"
                return content(message_id)

            restarted_runner.generate.side_effect = restarted_generate
            first_budget = InferenceCallBudget()
            restarted_budget = InferenceCallBudget()
            terminals = {
                "session-1": (
                    _terminal_record("turn-1"),
                    _terminal_record("turn-2"),
                )
            }
            common_patches = (
                patch(
                    "tools.collector_runtime.extract_chat_messages", return_value=chat
                ),
                patch(
                    "tools.collector_runtime.extract_current_work_status",
                    return_value=work,
                ),
                patch(
                    "tools.change_summary_generator.ChangeSummaryCliRunner",
                    side_effect=(first_runner, restarted_runner),
                ),
                patch(
                    "tools.collector_runtime.extract_decisions",
                    return_value=SimpleNamespace(decisions=tuple(), issues=tuple()),
                ),
                patch(
                    "tools.collector_runtime.extract_next_task",
                    return_value=SimpleNamespace(
                        task=None,
                        issues=tuple(),
                        cache_entry=None,
                        inference_attempted=False,
                    ),
                ),
                patch(
                    "tools.collector_runtime.extract_file_references",
                    return_value=SimpleNamespace(references=tuple()),
                ),
                patch(
                    "tools.collector_runtime.extract_development_errors",
                    return_value=object(),
                ),
                patch(
                    "tools.collector_runtime.collect_git_changes",
                    return_value=_runtime_git(),
                ),
                patch(
                    "tools.collector_runtime.build_json_snapshot", return_value=object()
                ),
                patch("tools.collector_runtime.save_json_snapshot"),
            )
            with common_patches[0], common_patches[1], common_patches[2], common_patches[3], common_patches[4], common_patches[5], common_patches[6], common_patches[7], common_patches[8], common_patches[9]:
                with self.assertRaises(KeyboardInterrupt):
                    _build_workspace_snapshot(
                        root,
                        "workspace-1",
                        "session-1",
                        {"session-1": tuple()},
                        settings(),
                        None,
                        terminals,
                        first_budget.try_acquire,
                    )
                interrupted_entries = load(ledger)
                _build_workspace_snapshot(
                    root,
                    "workspace-1",
                    "session-1",
                    {"session-1": tuple()},
                    settings(),
                    None,
                    terminals,
                    restarted_budget.try_acquire,
                )
                restored_entries = load(ledger)

        self.assertEqual(["turn-1"], [entry.turn_id for entry in interrupted_entries])
        self.assertEqual(["turn-1", "turn-2"], [entry.turn_id for entry in restored_entries])
        self.assertEqual(2, first_runner.generate.call_count)
        restarted_runner.generate.assert_called_once()
        restarted_prompt = restarted_runner.generate.call_args.args[0]
        self.assertNotIn("message-1", restarted_prompt)
        self.assertIn("message-2", restarted_prompt)
        self.assertEqual(2, first_budget.calls)
        self.assertEqual(1, restarted_budget.calls)

    def test_change_summary_incremental_pending_and_backfill_share_immediate_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            ledgers = tuple(
                Path(directory) / name
                for name in ("incremental.json", "pending.json", "backfill.json")
            )
            summary = ChangeSummary("summary-1", "turn-1", "jsonl", "completed", False, "title", "short", "details", tuple(), tuple(), "codex_generated", "high", ("session-1",), ("message-1",))
            cache_entry = ChangeSummaryCacheEntry(
                "turn-1", "a" * 64, summary, None, tuple()
            )
            observed_turn_filters = []

            def generate_success(*args, **kwargs):
                observed_turn_filters.append(kwargs["inference_turn_ids"])
                kwargs["on_inference_success"](cache_entry)
                return SimpleNamespace(
                    summaries=(summary,),
                    issues=tuple(),
                    cache_entries=(cache_entry,),
                )

            work = SimpleNamespace(
                codex_status="idle", turns=(_runtime_turn("turn-1"),)
            )
            pending = (
                PendingInference(
                    "workspace-1", "session-1", "turn-1", "change_summary"
                ),
            )
            cases = (
                (
                    "incremental",
                    {"session-1": (_terminal_record("turn-1"),)},
                    tuple(),
                ),
                ("incremental", {"session-1": tuple()}, pending),
                ("backfill", {"session-1": tuple()}, tuple()),
            )
            restored = []
            with patch(
                "tools.collector_runtime.extract_chat_messages",
                return_value=ChatExtractionResult(tuple(), tuple(), tuple()),
            ), patch(
                "tools.collector_runtime.extract_current_work_status",
                return_value=work,
            ), patch(
                "tools.collector_runtime._try_combined_inference", return_value=None
            ), patch(
                "tools.collector_runtime.extract_decisions",
                return_value=SimpleNamespace(decisions=tuple(), issues=tuple()),
            ), patch(
                "tools.collector_runtime.generate_change_summaries",
                side_effect=generate_success,
            ), patch(
                "tools.collector_runtime.extract_next_task",
                return_value=SimpleNamespace(
                    task=None,
                    issues=tuple(),
                    cache_entry=None,
                    inference_attempted=False,
                ),
            ), patch(
                "tools.collector_runtime.extract_file_references",
                return_value=SimpleNamespace(references=tuple()),
            ), patch(
                "tools.collector_runtime.extract_development_errors",
                return_value=object(),
            ), patch(
                "tools.collector_runtime.collect_git_changes",
                return_value=_runtime_git(),
            ), patch(
                "tools.collector_runtime.append_inference_ledger", wraps=append
            ) as save, patch(
                "tools.collector_runtime.build_json_snapshot", return_value=object()
            ), patch("tools.collector_runtime.save_json_snapshot"):
                for ledger, (mode, records, pending_inferences) in zip(
                    ledgers, cases
                ):
                    settings = SimpleNamespace(
                        inference_ledger_file=ledger,
                        ai_inference_mode=mode,
                        tasks_path=Path(directory) / "TASKS.md",
                        output_dir=Path(directory) / "output",
                        queue_dir=Path(directory) / "queue",
                    )
                    _build_workspace_snapshot(
                        root,
                        "workspace-1",
                        "session-1",
                        {"session-1": tuple()},
                        settings,
                        None,
                        inference_records_by_session=records,
                        pending_inferences=pending_inferences,
                    )
                    restored.append(load(ledger))

        self.assertEqual(
            [{"turn-1"}, {"turn-1"}, None], observed_turn_filters
        )
        self.assertEqual(3, save.call_count)
        self.assertEqual(list(ledgers), [call.args[0] for call in save.call_args_list])
        for entries in restored:
            self.assertEqual(1, len(entries))
            self.assertEqual("change_summary", entries[0].inference_kind)
            self.assertEqual("a" * 64, entries[0].input_sha256)
            self.assertEqual(
                {"schema_version": 1, "payload": summary_payload(summary)},
                entries[0].result,
            )

    def test_existing_history_is_remasked_before_snapshot_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            state = CollectorState((SimpleNamespace(session_id="session-1"),), tuple())
            record = NormalizedRecord(
                "2026-09-01T00:00:00+00:00",
                "conversation",
                "message",
                "response_item",
                "message",
                "message-1",
                "turn-1",
                "assistant",
                "final_answer",
                (NormalizedContentPart("text", "C:\\Users\\sample\\.ssh\\config", "output_text"),),
                {},
                Path(directory) / "session.jsonl",
                1,
                0,
                100,
            )
            history = CollectorHistory((("session-1", (record,)),))
            settings = SimpleNamespace(
                state_file=Path(directory) / "state.json",
                history_file=Path(directory) / "history.json",
                sessions_dir=Path(directory),
                archived_sessions_dir=None,
                scan_archived_sessions=False,
                allowed_roots=(root,),
                ai_inference_mode="incremental",
                max_calls_per_run=3,
            )
            session = SimpleNamespace(
                session_id="session-1",
                current_file=SimpleNamespace(
                    workspace_candidates=tuple(),
                    last_timestamp="2026-09-01T00:00:00+00:00",
                    path=Path(directory) / "session.jsonl",
                ),
            )
            captured = []

            with patch("tools.collector_runtime.load_collector_state", return_value=state), patch(
                "tools.collector_runtime.load_collector_history", return_value=history
            ), patch("tools.collector_runtime.discover_session_files", return_value=tuple()), patch(
                "tools.collector_runtime.build_session_index", return_value={"session-1": session}
            ), patch("tools.collector_runtime._workspace_root", return_value=root), patch(
                "tools.collector_runtime.collect_incremental_records",
                return_value=SimpleNamespace(
                    records=tuple(),
                    resume=SimpleNamespace(replay_from_start=False),
                    next_state=state,
                ),
            ), patch(
                "tools.collector_runtime._build_workspace_snapshot",
                side_effect=lambda *args: captured.append(args[3]) or tuple(),
            ), patch("tools.collector_runtime.save_collector_history") as save_history, patch(
                "tools.collector_runtime.save_collector_state"
            ):
                self.assertEqual(1, run_once(settings).processed_workspaces)

        snapshot_record = captured[0]["session-1"][0]
        persisted_record = save_history.call_args.args[0].records_for("session-1")[0]
        self.assertEqual("[REDACTED:LOCAL_PATH]", snapshot_record.content[0].text)
        self.assertEqual("[REDACTED:LOCAL_PATH]", persisted_record.content[0].text)
        self.assertEqual("local_path", persisted_record.content[0].redactions[0].type)
    def test_incremental_sequence_excludes_history_carries_limit_and_recovers_interrupted_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            settings = SimpleNamespace(
                state_file=Path(directory) / "state.json",
                history_file=Path(directory) / "history.json",
                sessions_dir=Path(directory),
                archived_sessions_dir=None,
                scan_archived_sessions=False,
                allowed_roots=(root,),
                ai_inference_mode="incremental",
                max_calls_per_run=3,
            )
            session = SimpleNamespace(
                session_id="session-1",
                current_file=SimpleNamespace(
                    workspace_candidates=tuple(),
                    last_timestamp="2026-08-31T00:00:00+00:00",
                    path=Path(directory) / "session.jsonl",
                ),
            )
            cursor = SimpleNamespace(session_id="session-1")
            known_state = CollectorState((cursor,), tuple())
            record = _terminal_record("turn-1")
            stored = [CollectorState()]
            build_calls = []
            save_calls = [0]

            def collect_records(*args):
                return SimpleNamespace(
                    records=(record,),
                    resume=SimpleNamespace(replay_from_start=True),
                    next_state=known_state,
                )

            def build_snapshot(*args):
                build_calls.append((args[6], args[8]))
                invocation = len(build_calls)
                if invocation == 1:
                    return tuple()
                if invocation == 2:
                    self.assertEqual(
                        [True, True, True, False],
                        [args[7]() for _ in range(4)],
                    )
                    return (
                        PendingInference(
                            args[1], "session-1", "turn-1", "change_summary"
                        ),
                    )
                return tuple()

            def save_state(state, path):
                save_calls[0] += 1
                if save_calls[0] == 3:
                    raise OSError("interrupted state replace")
                stored[0] = state

            with patch(
                "tools.collector_runtime.load_collector_state",
                side_effect=lambda path: stored[0],
            ), patch(
                "tools.collector_runtime.load_collector_history",
                return_value=CollectorHistory(),
            ), patch(
                "tools.collector_runtime.discover_session_files", return_value=tuple()
            ), patch(
                "tools.collector_runtime.build_session_index",
                return_value={"session-1": session},
            ), patch(
                "tools.collector_runtime._workspace_root", return_value=root
            ), patch(
                "tools.collector_runtime.collect_incremental_records",
                side_effect=collect_records,
            ), patch(
                "tools.collector_runtime._build_workspace_snapshot",
                side_effect=build_snapshot,
            ), patch(
                "tools.collector_runtime.save_collector_history"
            ), patch(
                "tools.collector_runtime.save_collector_state",
                side_effect=save_state,
            ):
                self.assertEqual(1, run_once(settings).processed_workspaces)
                self.assertEqual(1, run_once(settings).processed_workspaces)
                self.assertEqual(1, len(stored[0].pending_inferences))
                with self.assertRaisesRegex(OSError, "interrupted state replace"):
                    run_once(settings)
                self.assertEqual(1, len(stored[0].pending_inferences))
                self.assertEqual(1, run_once(settings).processed_workspaces)

        self.assertEqual(tuple(), build_calls[0][0]["session-1"])
        self.assertEqual((record,), build_calls[1][0]["session-1"])
        self.assertEqual(tuple(), build_calls[1][1])
        self.assertEqual(build_calls[2][1], build_calls[3][1])
        self.assertEqual(tuple(), stored[0].pending_inferences)

def _runtime_message(message_id, turn_id, text="実装しました。"):
    return SimpleNamespace(
        message_id=message_id,
        created_at=message_id,
        turn_id=turn_id,
        role="assistant",
        message_type="chat",
        phase="final_answer",
        content=(SimpleNamespace(kind="text", text=text),),
    )


def _runtime_turn(turn_id):
    return SimpleNamespace(
        turn_id=turn_id,
        turn_id_source="jsonl",
        status="completed",
        rolled_back=False,
    )


def _terminal_record(turn_id):
    return NormalizedRecord(
        None,
        "turn",
        "turn_status",
        "event_msg",
        "task_complete",
        None,
        turn_id,
        None,
        None,
        tuple(),
        {"status": "completed"},
        Path("session.jsonl"),
        1,
        0,
        1,
    )


def _runtime_git():
    return SimpleNamespace(
        repository=SimpleNamespace(
            collection_status="ok", branch="main", clean=True
        ),
        files=tuple(),
    )


def _combined_result(title="Decision", description="Description"):
    return CombinedInferenceResult(
        {
            "title": "Title",
            "short_summary": "Short",
            "details": "Details",
            "confidence": "high",
        },
        (
            {
                "status": "adopted",
                "title": title,
                "description": description,
                "reason": None,
                "topic_key": "topic",
            },
        ),
        {"task": "Task", "reason": "Reason", "confidence": "medium"},
    )


if __name__ == "__main__":
    unittest.main()
