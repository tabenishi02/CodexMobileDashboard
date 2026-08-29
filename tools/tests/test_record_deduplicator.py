import unittest
from dataclasses import replace
from pathlib import Path

from tools.chat_extractor import extract_chat_messages
from tools.record_deduplicator import (
    UnsafeDeduplicationInputError,
    deduplicate_records,
)
from tools.record_normalizer import NormalizedContentPart, NormalizedRecord


def record(
    *,
    source_id=None,
    text="同じ本文",
    offset=100,
    path=Path("rollout-live.jsonl"),
) -> NormalizedRecord:
    return NormalizedRecord(
        timestamp="2026-08-13T10:00:00+09:00",
        category="message",
        subtype="chat_message",
        source_record_type="response_item",
        source_payload_type="message",
        source_id=source_id,
        turn_id="turn-1",
        role="user",
        phase=None,
        content=(NormalizedContentPart("text", text, "input_text"),),
        attributes={},
        source_path=path,
        line_number=offset // 10,
        start_offset=offset,
        end_offset=offset + 10,
    )


class RecordDeduplicatorTests(unittest.TestCase):
    def test_native_id_removes_replayed_record_but_not_same_text(self) -> None:
        first = record(source_id="msg-1", offset=100)
        replayed = replace(first, source_path=Path("rollout-archived.jsonl"))
        intentional_repeat = record(source_id="msg-2", offset=200)

        result = deduplicate_records(
            (first, replayed, intentional_repeat),
            "session-1",
            content_is_masked=True,
        )

        self.assertEqual((first, intentional_repeat), result.records)
        self.assertEqual(1, result.duplicate_count)
        chats = extract_chat_messages(result.records, "session-1")
        self.assertEqual(
            ["msg-1", "msg-2"], [item.message_id for item in chats.messages]
        )

    def test_previous_state_prevents_duplicate_after_restart(self) -> None:
        source = record(source_id="msg-1")
        first = deduplicate_records(
            (source,), "session-1", content_is_masked=True
        )

        resumed = deduplicate_records(
            (source,),
            "session-1",
            content_is_masked=True,
            previous_state=first.state,
        )

        self.assertEqual(tuple(), resumed.records)
        self.assertEqual(1, resumed.duplicate_count)
        self.assertEqual(first.state, resumed.state)

    def test_conflicting_native_id_keeps_first_and_reports_issue(self) -> None:
        first = record(source_id="msg-1", text="first", offset=100)
        conflict = record(source_id="msg-1", text="changed", offset=200)

        result = deduplicate_records(
            (first, conflict), "session-1", content_is_masked=True
        )

        self.assertEqual((first,), result.records)
        self.assertEqual("conflicting_native_id", result.issues[0].kind)

    def test_fallback_identity_handles_overlap_and_moved_file(self) -> None:
        source = record(source_id=None, offset=100)
        first = deduplicate_records(
            (source,), "session-1", content_is_masked=True
        )
        moved = replace(source, source_path=Path("archived/rollout.jsonl"))

        resumed = deduplicate_records(
            (moved,),
            "session-1",
            content_is_masked=True,
            previous_state=first.state,
        )

        self.assertEqual(tuple(), resumed.records)
        self.assertEqual(1, resumed.duplicate_count)

    def test_full_replay_preserves_occurrences_but_does_not_add_them_again(self) -> None:
        first_copy = record(source_id=None, offset=100)
        second_copy = record(source_id=None, offset=200)
        initial = deduplicate_records(
            (first_copy, second_copy), "session-1", content_is_masked=True
        )
        self.assertEqual(2, len(initial.records))

        replayed = deduplicate_records(
            (first_copy, second_copy),
            "session-1",
            content_is_masked=True,
            previous_state=initial.state,
            replay_from_start=True,
        )

        self.assertEqual(tuple(), replayed.records)
        self.assertEqual(2, replayed.duplicate_count)
        self.assertEqual(initial.state, replayed.state)

    def test_rejects_unmasked_input_before_fingerprinting(self) -> None:
        with self.assertRaisesRegex(
            UnsafeDeduplicationInputError, "content_not_masked"
        ):
            deduplicate_records(
                (record(text="token=raw-secret"),),
                "session-1",
                content_is_masked=False,
            )

    def test_state_contains_only_fixed_length_digests_and_counts(self) -> None:
        result = deduplicate_records(
            (record(text="token=[REDACTED:TOKEN]"),),
            "session-1",
            content_is_masked=True,
        )

        identity, fingerprint = result.state.identities[0]
        self.assertEqual(64, len(identity))
        self.assertEqual(64, len(fingerprint))
        self.assertNotIn("REDACTED", repr(result.state))


if __name__ == "__main__":
    unittest.main()
