import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional, Tuple

from tools.chat_extractor import ChatContentPart, ExtractedChatMessage
from tools.error_extractor import extract_development_errors
from tools.record_normalizer import NormalizedContentPart, NormalizedRecord
from tools.work_status_extractor import CurrentWorkStatus, TurnWorkState


def record(
    category: str,
    subtype: str,
    *,
    offset: int,
    timestamp: Optional[str] = "2026-08-08T01:00:00+00:00",
    turn_id: Optional[str] = "turn-1",
    source_payload_type: Optional[str] = None,
    attributes: Optional[Dict[str, object]] = None,
    content: Tuple[NormalizedContentPart, ...] = tuple(),
    path: Path = Path("rollout-test.jsonl"),
) -> NormalizedRecord:
    return NormalizedRecord(
        timestamp=timestamp,
        category=category,
        subtype=subtype,
        source_record_type="event_msg",
        source_payload_type=source_payload_type or subtype,
        source_id=None,
        turn_id=turn_id,
        role="tool" if category == "tool" else None,
        phase=None,
        content=content,
        attributes=attributes or {},
        source_path=path,
        line_number=offset // 10,
        start_offset=offset,
        end_offset=offset + 10,
    )


def tool_call(call_id: str, offset: int = 100) -> NormalizedRecord:
    return record(
        "tool",
        "tool_call",
        offset=offset,
        source_payload_type="function_call",
        attributes={"call_id": call_id, "name": "shell_command"},
    )


def tool_output(call_id: str, text: str, offset: int = 200) -> NormalizedRecord:
    return record(
        "tool",
        "tool_output",
        offset=offset,
        source_payload_type="function_call_output",
        attributes={"call_id": call_id},
        content=(NormalizedContentPart("tool_output", text, None),),
    )


def tool_message(call_id: str, message_id: str = "msg-tool") -> ExtractedChatMessage:
    return ExtractedChatMessage(
        message_id=message_id,
        source_message_id=message_id,
        sequence=1,
        created_at="2026-08-08T01:00:00+00:00",
        role="tool",
        message_type="tool_summary",
        phase=None,
        turn_id="turn-1",
        content=(
            ChatContentPart("text", "ツール結果を受信"),
            ChatContentPart("call_id", call_id),
        ),
        display_mode="collapsed",
        duplicate_of=None,
        occurrence_count=1,
        removed_automatic_contexts=tuple(),
        source_path=Path("rollout-test.jsonl"),
        source_line_number=20,
        source_start_offset=200,
    )


def work_status(*turns: TurnWorkState) -> CurrentWorkStatus:
    return CurrentWorkStatus(
        codex_status="idle",
        current_work=None,
        current_work_message_id=None,
        active_turn_id=None,
        latest_turn_id=turns[-1].turn_id if turns else None,
        latest_turn_status=turns[-1].status if turns else None,
        last_event_at=None,
        turns=tuple(turns),
        issues=tuple(),
    )


def turn(*, rolled_back: bool) -> TurnWorkState:
    return TurnWorkState(
        turn_id="turn-1",
        status="failed",
        started_at=None,
        started_at_source="missing",
        completed_at=None,
        completed_at_source="missing",
        duration_ms=None,
        reason="interrupted",
        user_message_id=None,
        assistant_message_ids=tuple(),
        rolled_back=rolled_back,
    )


class ErrorExtractorTests(unittest.TestCase):
    def extract(
        self,
        records: Tuple[NormalizedRecord, ...],
        *,
        messages: Tuple[ExtractedChatMessage, ...] = tuple(),
        identities: Optional[Dict[str, str]] = None,
        status: Optional[CurrentWorkStatus] = None,
    ):
        return extract_development_errors(
            records,
            messages,
            status or work_status(),
            "workspace-1",
            "session-1",
            safe_operation_identities=identities,
        )

    def test_nonzero_exit_creates_command_error_and_reference(self) -> None:
        records = (
            tool_call("call-1"),
            tool_output("call-1", "Script failed\nExit code: 2\n詳細", 200),
        )

        result = self.extract(
            records,
            messages=(tool_message("call-1"),),
            identities={"call-1": "python -m unittest"},
        )

        self.assertEqual(1, len(result.errors))
        error = result.errors[0]
        self.assertEqual("command_failure", error.kind)
        self.assertEqual("error", error.severity)
        self.assertEqual("open", error.status)
        self.assertEqual(("msg-tool",), error.source_message_ids)
        self.assertIn("終了コード2", error.summary)
        self.assertNotIn("python -m unittest", error.summary)
        self.assertFalse(error.details_complete)

    def test_same_safe_command_is_aggregated_then_resolved(self) -> None:
        records = (
            tool_call("call-1", 100),
            tool_output("call-1", "Exit code: 1\n失敗", 200),
            tool_call("call-2", 300),
            tool_output("call-2", "Exit code: 1\n失敗", 400),
            tool_call("call-3", 500),
            tool_output("call-3", "Exit code: 0\n成功", 600),
        )
        identities = {key: "python test.py" for key in ("call-1", "call-2", "call-3")}

        result = self.extract(records, identities=identities)

        self.assertEqual(1, len(result.errors))
        error = result.errors[0]
        self.assertEqual(2, error.occurrence_count)
        self.assertEqual("resolved", error.status)
        self.assertIsNotNone(error.resolved_at)

    def test_different_command_success_does_not_resolve(self) -> None:
        records = (
            tool_call("call-1", 100),
            tool_output("call-1", "Exit code: 1", 200),
            tool_call("call-2", 300),
            tool_output("call-2", "Exit code: 0", 400),
        )

        result = self.extract(
            records,
            identities={"call-1": "python test.py", "call-2": "git status"},
        )

        self.assertEqual("open", result.errors[0].status)

    def test_no_safe_identity_does_not_merge_or_auto_resolve(self) -> None:
        records = (
            tool_call("call-1", 100),
            tool_output("call-1", "Exit code: 1", 200),
            tool_call("call-2", 300),
            tool_output("call-2", "Exit code: 1", 400),
            tool_call("call-3", 500),
            tool_output("call-3", "Exit code: 0", 600),
        )

        result = self.extract(records)

        self.assertEqual(2, len(result.errors))
        self.assertTrue(all(item.status == "open" for item in result.errors))

    def test_exit_zero_stderr_alone_is_ignored(self) -> None:
        result = self.extract(
            (tool_call("call-1"), tool_output("call-1", "warning\nExit code: 0"))
        )

        self.assertEqual(tuple(), result.errors)

    def test_traceback_with_exit_zero_is_warning(self) -> None:
        output = """Traceback (most recent call last):
  File "tools/sender.py", line 120, in send
    raise TimeoutError()
TimeoutError: timed out
Exit code: 0"""

        result = self.extract((tool_call("call-1"), tool_output("call-1", output)))

        error = result.errors[0]
        self.assertEqual("python_exception", error.kind)
        self.assertEqual("warning", error.severity)
        self.assertIn("TimeoutError", error.summary)
        self.assertTrue(error.details_complete)

    def test_unittest_failures_are_individual_errors(self) -> None:
        output = """FAIL: test_one (tests.SampleTests)
AssertionError: one
FAIL: test_two (tests.SampleTests)
AssertionError: two
Exit code: 1"""

        result = self.extract((tool_call("call-1"), tool_output("call-1", output)))

        self.assertEqual(2, len(result.errors))
        self.assertEqual(
            {"tests.SampleTests.test_one", "tests.SampleTests.test_two"},
            {item.summary.rsplit(": ", 1)[1] for item in result.errors},
        )
        first = next(item for item in result.errors if "test_one" in item.summary)
        self.assertNotIn("test_two", first.details_text or "")

    def test_patch_failure_resolves_only_for_same_file_set(self) -> None:
        failed = record(
            "file_change",
            "patch_result",
            offset=100,
            attributes={
                "success": False,
                "changes": ({"path": "tools/a.py", "type": "update"},),
            },
            content=(NormalizedContentPart("stderr", "適用失敗", None),),
        )
        succeeded = record(
            "file_change",
            "patch_result",
            offset=200,
            attributes={
                "success": True,
                "changes": ({"path": "tools/a.py", "type": "update"},),
            },
        )

        result = self.extract((failed, succeeded))

        self.assertEqual("resolved", result.errors[0].status)
        self.assertEqual("適用失敗", result.errors[0].details_text)

    def test_patch_stdout_without_stderr_is_preview_only(self) -> None:
        failed = record(
            "file_change",
            "patch_result",
            offset=100,
            attributes={"success": False, "changes": tuple()},
            content=(NormalizedContentPart("stdout", "複合出力", None),),
        )

        result = self.extract((failed,))

        self.assertFalse(result.errors[0].details_complete)
        self.assertIsNone(result.errors[0].details_text)
        self.assertEqual("複合出力", result.errors[0].details_preview)

    def test_interrupted_turn_is_warning_and_rollback_is_retained(self) -> None:
        aborted = record(
            "turn",
            "turn_status",
            offset=100,
            attributes={"status": "aborted", "reason": "interrupted"},
        )

        result = self.extract((aborted,), status=work_status(turn(rolled_back=True)))

        self.assertEqual("warning", result.errors[0].severity)
        self.assertTrue(result.errors[0].rolled_back)
        self.assertEqual("open", result.errors[0].status)

    def test_unknown_abort_reason_is_error(self) -> None:
        aborted = record(
            "turn",
            "turn_status",
            offset=100,
            attributes={"status": "aborted", "reason": "runtime_failure"},
        )

        result = self.extract((aborted,))

        self.assertEqual("error", result.errors[0].severity)

    def test_git_fatal_is_extracted_only_on_nonzero_exit(self) -> None:
        failed = self.extract(
            (
                tool_call("call-1"),
                tool_output("call-1", "fatal: not a git repository\nExit code: 128"),
            )
        )
        succeeded = self.extract(
            (
                tool_call("call-1"),
                tool_output("call-1", "fatal: example shown as text\nExit code: 0"),
            )
        )

        self.assertEqual("git_command_failure", failed.errors[0].kind)
        self.assertEqual(tuple(), succeeded.errors)

    def test_mcp_failure_resolves_with_same_safe_identity(self) -> None:
        failed = record(
            "tool",
            "mcp_tool_completed",
            offset=100,
            attributes={
                "call_id": "call-1",
                "server": "example",
                "tool": "read",
                "success": False,
            },
        )
        succeeded = record(
            "tool",
            "mcp_tool_completed",
            offset=200,
            attributes={
                "call_id": "call-2",
                "server": "example",
                "tool": "read",
                "success": True,
            },
        )

        result = self.extract(
            (failed, succeeded),
            identities={"call-1": "masked request", "call-2": "masked request"},
        )

        self.assertEqual("mcp_failure", result.errors[0].kind)
        self.assertEqual("resolved", result.errors[0].status)

    def test_unseparated_command_output_keeps_only_4000_character_preview(self) -> None:
        output = "通常出力" * 2_000 + "\nExit code: 1"

        result = self.extract((tool_call("call-1"), tool_output("call-1", output)))

        error = result.errors[0]
        self.assertFalse(error.details_complete)
        self.assertIsNone(error.details_text)
        self.assertEqual(4_000, len(error.details_preview or ""))

    def test_missing_record_time_uses_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rollout.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            failed = tool_output("call-1", "Exit code: 1")
            failed = record(
                failed.category,
                failed.subtype,
                offset=failed.start_offset,
                timestamp=None,
                source_payload_type=failed.source_payload_type,
                attributes=failed.attributes,
                content=failed.content,
                path=path,
            )

            result = self.extract((tool_call("call-1"), failed))

        self.assertEqual("file_mtime", result.errors[0].occurred_at_source)
        self.assertIsNotNone(result.errors[0].first_occurred_at)

    def test_missing_all_time_sources_keeps_null(self) -> None:
        failed = record(
            "tool",
            "tool_output",
            offset=200,
            timestamp=None,
            attributes={"call_id": "call-1"},
            content=(NormalizedContentPart("tool_output", "Exit code: 1", None),),
            path=Path("missing-rollout.jsonl"),
        )

        result = self.extract((tool_call("call-1"), failed))

        self.assertIsNone(result.errors[0].first_occurred_at)
        self.assertEqual("missing", result.errors[0].occurred_at_source)


if __name__ == "__main__":
    unittest.main()
