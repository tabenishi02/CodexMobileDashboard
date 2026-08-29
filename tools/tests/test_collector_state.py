import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.collector_state import (
    CollectorState,
    InvalidCollectorStateError,
    advance_session_cursor,
    load_collector_state,
    resume_position,
    save_collector_state,
)
from tools.record_deduplicator import RecordDeduplicationState


class CollectorStateTests(unittest.TestCase):
    def test_saves_and_restores_cursor_and_deduplication_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "rollout.jsonl"
            source.write_bytes(b"first\nsecond\n")
            deduplication = RecordDeduplicationState(
                (("a" * 64, "b" * 64),), ("c" * 64,), (("d" * 64, 2),)
            )
            state = advance_session_cursor(
                CollectorState(), "session-1", source, len(b"first\n"), 1, deduplication
            )
            state_path = root / "state" / "collector-state.json"

            save_collector_state(state, state_path)
            restored = load_collector_state(state_path)
            position = resume_position(restored, "session-1", source)

            self.assertFalse(position.replay_from_start)
            self.assertEqual(len(b"first\n"), position.start_offset)
            self.assertEqual(1, position.start_line_number)
            self.assertEqual(deduplication, position.deduplication)
            self.assertFalse(state_path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_appended_file_keeps_incremental_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "rollout.jsonl"
            source.write_bytes(b"first\n")
            state = advance_session_cursor(
                CollectorState(),
                "session-1",
                source,
                len(b"first\n"),
                1,
                RecordDeduplicationState(),
            )
            with source.open("ab") as stream:
                stream.write(b"second\n")

            position = resume_position(state, "session-1", source)

            self.assertEqual(len(b"first\n"), position.start_offset)
            self.assertFalse(position.replay_from_start)

    def test_moved_file_with_same_prefix_keeps_incremental_position(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "sessions" / "rollout.jsonl"
            original.parent.mkdir()
            original.write_bytes(b"first\nsecond\n")
            state = advance_session_cursor(
                CollectorState(),
                "session-1",
                original,
                len(b"first\n"),
                1,
                RecordDeduplicationState(),
            )
            moved = root / "archived" / "rollout.jsonl"
            moved.parent.mkdir()
            original.replace(moved)

            position = resume_position(state, "session-1", moved)

            self.assertEqual(len(b"first\n"), position.start_offset)
            self.assertFalse(position.replay_from_start)

    def test_truncated_or_replaced_file_replays_from_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "rollout.jsonl"
            source.write_bytes(b"first\nsecond\n")
            state = advance_session_cursor(
                CollectorState(),
                "session-1",
                source,
                len(b"first\nsecond\n"),
                2,
                RecordDeduplicationState(),
            )

            source.write_bytes(b"short\n")
            truncated = resume_position(state, "session-1", source)
            self.assertTrue(truncated.replay_from_start)
            self.assertEqual("file_truncated", truncated.reset_reason)

            source.write_bytes(b"other\nsecond\n")
            replaced = resume_position(state, "session-1", source)
            self.assertTrue(replaced.replay_from_start)
            self.assertEqual("file_replaced", replaced.reset_reason)

    def test_invalid_state_does_not_get_silently_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collector-state.json"
            path.write_text("not-json", encoding="utf-8")

            with self.assertRaisesRegex(InvalidCollectorStateError, "state_unreadable"):
                load_collector_state(path)

    def test_failed_replace_keeps_previous_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "rollout.jsonl"
            source.write_bytes(b"first\n")
            path = root / "collector-state.json"
            path.write_bytes(b"previous\n")
            state = advance_session_cursor(
                CollectorState(),
                "session-1",
                source,
                len(b"first\n"),
                1,
                RecordDeduplicationState(),
            )

            with patch(
                "tools.collector_state.os.replace",
                side_effect=PermissionError("replace denied"),
            ):
                with self.assertRaises(PermissionError):
                    save_collector_state(state, path)

            self.assertEqual(b"previous\n", path.read_bytes())
            self.assertEqual([], list(root.glob(".collector-state.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()
