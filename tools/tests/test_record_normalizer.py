import unittest
from pathlib import Path

from tools.record_normalizer import normalize_record
from tools.session_reader import SessionRecord


def source_record(record_type: str, payload: dict, timestamp: str = "2026-08-08T01:00:00Z") -> SessionRecord:
    return SessionRecord(
        path=Path("rollout-test.jsonl"),
        line_number=3,
        start_offset=120,
        end_offset=240,
        timestamp=timestamp,
        record_type=record_type,
        payload=payload,
    )


class RecordNormalizerTests(unittest.TestCase):
    def test_normalizes_response_message_with_missing_optional_fields(self) -> None:
        result = normalize_record(
            source_record(
                "response_item",
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "日本語😀"}],
                },
            )
        )

        self.assertEqual(tuple(), result.issues)
        self.assertIsNotNone(result.record)
        assert result.record is not None
        self.assertEqual("message", result.record.category)
        self.assertEqual("chat_message", result.record.subtype)
        self.assertIsNone(result.record.source_id)
        self.assertIsNone(result.record.phase)
        self.assertEqual("日本語😀", result.record.content[0].text)
        self.assertEqual("2026-08-08T01:00:00+00:00", result.record.timestamp)
        self.assertEqual(Path("rollout-test.jsonl"), result.record.source_path)

    def test_keeps_event_message_as_identifiable_fallback_source(self) -> None:
        result = normalize_record(
            source_record(
                "event_msg",
                {"type": "agent_message", "message": "完了しました", "phase": "final_answer"},
            )
        )

        assert result.record is not None
        self.assertEqual("chat_message_event", result.record.subtype)
        self.assertEqual("assistant", result.record.role)
        self.assertTrue(result.record.attributes["fallback_source"])

    def test_normalizes_string_and_list_tool_outputs(self) -> None:
        string_result = normalize_record(
            source_record(
                "response_item",
                {"type": "function_call_output", "call_id": "call-1", "output": "text"},
            )
        )
        list_result = normalize_record(
            source_record(
                "response_item",
                {
                    "type": "custom_tool_call_output",
                    "call_id": "call-2",
                    "output": [
                        {"type": "input_text", "text": "result"},
                        {"type": "input_image", "image_url": "data:image/png;base64,secret"},
                    ],
                },
            )
        )

        assert string_result.record is not None
        assert list_result.record is not None
        self.assertEqual("text", string_result.record.content[0].text)
        self.assertEqual("result", list_result.record.content[0].text)
        self.assertEqual("image_reference", list_result.record.content[1].kind)
        self.assertIsNone(list_result.record.content[1].text)
        self.assertNotIn("secret", repr(list_result.record))

    def test_patch_normalization_excludes_file_content_and_diff(self) -> None:
        result = normalize_record(
            source_record(
                "event_msg",
                {
                    "type": "patch_apply_end",
                    "success": True,
                    "changes": {
                        "src/app.py": {
                            "type": "update",
                            "unified_diff": "secret diff",
                            "content": "secret file",
                            "move_path": None,
                        }
                    },
                },
            )
        )

        assert result.record is not None
        self.assertEqual(
            ({"path": "src/app.py", "type": "update", "move_path": None},),
            result.record.attributes["changes"],
        )
        self.assertNotIn("secret", repr(result.record))

    def test_session_metadata_excludes_base_instructions(self) -> None:
        result = normalize_record(
            source_record(
                "session_meta",
                {
                    "id": "session-1",
                    "cwd": r"C:\codex\Project",
                    "base_instructions": {"text": "private instructions"},
                },
            )
        )

        assert result.record is not None
        self.assertEqual("session-1", result.record.source_id)
        self.assertNotIn("private instructions", repr(result.record))

    def test_normalizes_mcp_completion_without_arguments_or_result_body(self) -> None:
        result = normalize_record(
            source_record(
                "event_msg",
                {
                    "type": "mcp_tool_call_end",
                    "call_id": "call-3",
                    "invocation": {
                        "server": "example-server",
                        "tool": "example-tool",
                        "arguments": {"token": "secret"},
                    },
                    "duration": {"secs": 2, "nanos": 500_000_000},
                    "result": {"Ok": {"body": "secret"}},
                },
            )
        )

        assert result.record is not None
        self.assertEqual("mcp_tool_completed", result.record.subtype)
        self.assertEqual(2.5, result.record.attributes["duration_seconds"])
        self.assertTrue(result.record.attributes["success"])
        self.assertNotIn("secret", repr(result.record))

    def test_invalid_timestamp_becomes_none_with_warning(self) -> None:
        result = normalize_record(
            source_record("world_state", {"full": True}, timestamp="not-a-date")
        )

        assert result.record is not None
        self.assertIsNone(result.record.timestamp)
        self.assertEqual("invalid_timestamp", result.issues[0].kind)

    def test_unknown_payload_subtype_is_not_guessed(self) -> None:
        result = normalize_record(
            source_record("event_msg", {"type": "future_event", "message": "private"})
        )

        self.assertIsNone(result.record)
        self.assertEqual("unsupported_event_message", result.issues[0].kind)
        self.assertNotIn("private", repr(result.issues))

    def test_every_documented_payload_subtype_has_a_normalizer(self) -> None:
        response_payloads = [
            {"type": "message", "content": []},
            {"type": "reasoning"},
            {"type": "function_call", "arguments": ""},
            {"type": "custom_tool_call", "input": ""},
            {"type": "function_call_output", "output": ""},
            {"type": "custom_tool_call_output", "output": []},
        ]
        event_payloads = [
            {"type": "user_message", "message": ""},
            {"type": "agent_message", "message": ""},
            {"type": "task_started"},
            {"type": "task_complete"},
            {"type": "turn_aborted"},
            {"type": "thread_rolled_back"},
            {"type": "thread_settings_applied"},
            {"type": "token_count"},
            {"type": "patch_apply_end"},
            {"type": "web_search_end"},
            {"type": "mcp_tool_call_end"},
            {"type": "context_compacted"},
        ]

        for payload in response_payloads:
            with self.subTest(record_type="response_item", subtype=payload["type"]):
                self.assertIsNotNone(
                    normalize_record(source_record("response_item", payload)).record
                )
        for payload in event_payloads:
            with self.subTest(record_type="event_msg", subtype=payload["type"]):
                self.assertIsNotNone(
                    normalize_record(source_record("event_msg", payload)).record
                )


if __name__ == "__main__":
    unittest.main()
