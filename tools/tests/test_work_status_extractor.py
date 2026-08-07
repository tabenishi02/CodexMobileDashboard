import unittest
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

from tools.chat_extractor import ChatContentPart, ExtractedChatMessage
from tools.record_normalizer import NormalizedRecord
from tools.work_status_extractor import extract_current_work_status


def turn_record(
    status: str,
    *,
    turn_id: Optional[str] = "turn-1",
    offset: int = 100,
    attributes: Optional[Dict[str, object]] = None,
) -> NormalizedRecord:
    values: Dict[str, object] = {"status": status}
    values.update(attributes or {})
    return NormalizedRecord(
        timestamp="2026-08-08T01:00:00+00:00",
        category="turn",
        subtype="turn_status",
        source_record_type="event_msg",
        source_payload_type="task_" + status,
        source_id=None,
        turn_id=turn_id,
        role=None,
        phase=None,
        content=tuple(),
        attributes=values,
        source_path=Path("rollout-test.jsonl"),
        line_number=offset // 10,
        start_offset=offset,
        end_offset=offset + 10,
    )


def chat_message(
    role: str,
    text: str,
    *,
    turn_id: str = "turn-1",
    offset: int = 150,
) -> ExtractedChatMessage:
    return ExtractedChatMessage(
        message_id=f"msg-{offset}",
        source_message_id=f"msg-{offset}",
        sequence=1,
        created_at="2026-08-08T01:00:01+00:00",
        role=role,
        message_type="chat",
        phase=None,
        turn_id=turn_id,
        content=(ChatContentPart("text", text),),
        display_mode="expanded",
        duplicate_of=None,
        occurrence_count=1,
        removed_automatic_contexts=tuple(),
        source_path=Path("rollout-test.jsonl"),
        source_line_number=15,
        source_start_offset=offset,
    )


class WorkStatusExtractorTests(unittest.TestCase):
    def test_active_turn_is_working_with_user_instruction(self) -> None:
        records = [turn_record("started")]
        messages = [chat_message("user", "現在の作業を実装してください")]

        result = extract_current_work_status(records, messages, "session-1")

        self.assertEqual("working", result.codex_status)
        self.assertEqual("turn-1", result.active_turn_id)
        self.assertEqual("現在の作業を実装してください", result.current_work)
        self.assertEqual("msg-150", result.current_work_message_id)

    def test_completed_turn_makes_codex_idle(self) -> None:
        records = [
            turn_record("started", offset=100),
            turn_record(
                "completed",
                offset=300,
                attributes={"completed_at_ms": 1_786_147_200_000, "duration_ms": 2000},
            ),
        ]
        messages = [chat_message("user", "作業", offset=150)]

        result = extract_current_work_status(records, messages, "session-1")

        self.assertEqual("idle", result.codex_status)
        self.assertIsNone(result.current_work)
        self.assertEqual("completed", result.latest_turn_status)
        self.assertEqual(2000, result.turns[0].duration_ms)
        self.assertEqual("2026-08-08T00:00:00+00:00", result.turns[0].completed_at)
        self.assertEqual("record_timestamp", result.turns[0].started_at_source)
        self.assertEqual("event_field", result.turns[0].completed_at_source)

    def test_aborted_turn_is_failed(self) -> None:
        records = [turn_record("started"), turn_record("aborted", offset=200)]

        result = extract_current_work_status(records, [], "session-1")

        self.assertEqual("idle", result.codex_status)
        self.assertEqual("failed", result.latest_turn_status)

    def test_no_turn_events_is_unknown(self) -> None:
        result = extract_current_work_status([], [], "session-1")

        self.assertEqual("unknown", result.codex_status)
        self.assertIsNone(result.latest_turn_id)

    def test_new_turn_supersedes_unfinished_old_turn(self) -> None:
        records = [
            turn_record("started", turn_id="turn-1", offset=100),
            turn_record("started", turn_id="turn-2", offset=200),
        ]

        result = extract_current_work_status(records, [], "session-1")

        self.assertEqual("incomplete", result.turns[0].status)
        self.assertEqual("superseded_by_new_turn", result.turns[0].reason)
        self.assertIsNone(result.turns[0].completed_at)
        self.assertEqual("missing", result.turns[0].completed_at_source)
        self.assertEqual("turn-2", result.active_turn_id)
        self.assertEqual("unfinished_turn_superseded", result.issues[0].kind)

    def test_rollback_marks_recent_completed_turn(self) -> None:
        rollback = replace(
            turn_record("completed", offset=300),
            subtype="thread_rolled_back",
            turn_id=None,
            attributes={"num_turns": 1},
            start_offset=400,
        )
        records = [turn_record("started", offset=100), turn_record("completed", offset=300), rollback]

        result = extract_current_work_status(records, [], "session-1")

        self.assertTrue(result.turns[0].rolled_back)
        self.assertIsNone(result.latest_turn_id)

    def test_current_work_preview_has_a_fixed_limit(self) -> None:
        messages = [chat_message("user", "あ" * 600)]

        result = extract_current_work_status(
            [turn_record("started")], messages, "session-1"
        )

        self.assertEqual(500, len(result.current_work or ""))
        self.assertTrue((result.current_work or "").endswith("…"))

    def test_missing_event_times_record_the_fallback_source(self) -> None:
        records = [turn_record("started"), turn_record("completed", offset=200)]

        result = extract_current_work_status(records, [], "session-1")

        self.assertEqual("record_timestamp", result.turns[0].started_at_source)
        self.assertEqual("record_timestamp", result.turns[0].completed_at_source)


if __name__ == "__main__":
    unittest.main()
