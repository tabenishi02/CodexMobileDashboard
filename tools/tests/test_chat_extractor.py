import unittest
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

from tools.chat_extractor import extract_chat_messages
from tools.record_normalizer import NormalizedContentPart, NormalizedRecord


def normalized_record(
    *,
    category: str = "message",
    subtype: str = "chat_message",
    role: str = "user",
    phase: Optional[str] = None,
    text: str = "message",
    source_id: Optional[str] = None,
    offset: int = 100,
    attributes: Optional[Dict[str, object]] = None,
) -> NormalizedRecord:
    return NormalizedRecord(
        timestamp="2026-08-08T01:00:00+00:00",
        category=category,
        subtype=subtype,
        source_record_type="response_item" if subtype != "chat_message_event" else "event_msg",
        source_payload_type="message",
        source_id=source_id,
        turn_id=None,
        role=role,
        phase=phase,
        content=(NormalizedContentPart("text", text, "input_text"),),
        attributes=attributes or {},
        source_path=Path("rollout-test.jsonl"),
        line_number=offset // 10,
        start_offset=offset,
        end_offset=offset + 10,
    )


class ChatExtractorTests(unittest.TestCase):
    def test_response_message_wins_over_duplicate_event_message(self) -> None:
        event = normalized_record(subtype="chat_message_event", offset=100)
        response = normalized_record(source_id="msg-native", offset=200)

        result = extract_chat_messages([event, response], "session-1")

        self.assertEqual(1, len(result.messages))
        self.assertEqual("msg-native", result.messages[0].message_id)

    def test_repeated_primary_messages_are_not_deduplicated_by_text(self) -> None:
        records = [
            normalized_record(source_id="msg-1", offset=100),
            normalized_record(source_id="msg-2", offset=200),
        ]

        result = extract_chat_messages(records, "session-1")

        self.assertEqual(2, len(result.messages))

    def test_commentary_is_collapsed_and_final_answer_is_expanded(self) -> None:
        records = [
            normalized_record(role="assistant", phase="commentary", offset=100),
            normalized_record(role="assistant", phase="final_answer", offset=200),
        ]

        result = extract_chat_messages(records, "session-1")

        self.assertEqual("collapsed", result.messages[0].display_mode)
        self.assertEqual("expanded", result.messages[1].display_mode)

    def test_developer_instruction_repeats_use_a_reference(self) -> None:
        records = [
            normalized_record(role="developer", text="same", offset=100),
            normalized_record(role="developer", text="same", offset=200),
            normalized_record(role="developer", text="different", offset=300),
        ]

        result = extract_chat_messages(records, "session-1")

        first, reference, different = result.messages
        self.assertEqual("developer_instruction", first.message_type)
        self.assertEqual(2, first.occurrence_count)
        self.assertEqual("developer_instruction_reference", reference.message_type)
        self.assertEqual(first.message_id, reference.duplicate_of)
        self.assertEqual(tuple(), reference.content)
        self.assertIsNone(different.duplicate_of)

    def test_removes_only_complete_known_automatic_prefixes(self) -> None:
        text = (
            "<recommended_plugins>plugin</recommended_plugins>\n"
            "<environment_context><cwd>C:\\codex</cwd></environment_context>\n"
            "ユーザー本文"
        )
        record = normalized_record(text=text)

        result = extract_chat_messages([record], "session-1")

        message = result.messages[0]
        self.assertEqual("ユーザー本文", message.content[0].text)
        self.assertEqual(
            ("recommended_plugins", "environment_context"),
            message.removed_automatic_contexts,
        )

    def test_preserves_malformed_or_nonleading_context_examples(self) -> None:
        malformed = normalized_record(
            text="<environment_context>missing close\nユーザー本文", offset=100
        )
        nonleading = normalized_record(
            text="次の例です。\n<environment_context>x</environment_context>", offset=200
        )

        result = extract_chat_messages([malformed, nonleading], "session-1")

        self.assertIn("<environment_context>", result.messages[0].content[0].text)
        self.assertIn("<environment_context>", result.messages[1].content[0].text)

    def test_removes_automatic_blocks_split_across_content_parts(self) -> None:
        record = normalized_record(text="unused")
        record = replace(
            record,
            content=(
                NormalizedContentPart(
                    "text",
                    "<recommended_plugins>x</recommended_plugins>",
                    "input_text",
                ),
                NormalizedContentPart(
                    "text",
                    "# AGENTS.md instructions\n<INSTRUCTIONS>x</INSTRUCTIONS>",
                    "input_text",
                ),
                NormalizedContentPart("text", "ユーザー本文", "input_text"),
            ),
        )

        result = extract_chat_messages([record], "session-1")

        self.assertEqual("ユーザー本文", result.messages[0].content[0].text)
        self.assertEqual(
            ("recommended_plugins", "agents_instructions"),
            result.messages[0].removed_automatic_contexts,
        )

    def test_automatic_only_message_is_recorded_but_not_displayed(self) -> None:
        record = normalized_record(
            text="<environment_context><cwd>C:\\codex</cwd></environment_context>"
        )

        result = extract_chat_messages([record], "session-1")

        self.assertEqual(tuple(), result.messages)
        self.assertEqual(1, len(result.automatic_context_removals))
        self.assertEqual(
            ("environment_context",),
            result.automatic_context_removals[0].context_types,
        )

    def test_tool_summary_does_not_include_input_or_output_body(self) -> None:
        call = normalized_record(
            category="tool",
            subtype="tool_call",
            role="tool",
            text="secret argument",
            offset=100,
            attributes={"call_id": "call-1", "name": "shell_command"},
        )
        output = normalized_record(
            category="tool",
            subtype="tool_output",
            role="tool",
            text="secret output",
            offset=200,
            attributes={"call_id": "call-1"},
        )

        result = extract_chat_messages([call, output], "session-1")

        self.assertEqual(1, len(result.messages))
        self.assertEqual("tool_summary", result.messages[0].message_type)
        self.assertNotIn("secret", repr(result.messages[0]))
        self.assertIn("shell_command", repr(result.messages[0]))

    def test_missing_native_id_gets_stable_fallback(self) -> None:
        record = normalized_record(source_id=None, offset=123)

        first = extract_chat_messages([record], "session-1").messages[0]
        second = extract_chat_messages([record], "session-1").messages[0]

        self.assertEqual(first.message_id, second.message_id)
        self.assertTrue(first.message_id.startswith("msg_"))


if __name__ == "__main__":
    unittest.main()
