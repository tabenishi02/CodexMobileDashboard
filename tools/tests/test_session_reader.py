import json
import tempfile
import unittest
from pathlib import Path

from tools.session_reader import (
    DiscoveredSessionFile,
    JsonlReadStream,
    build_session_index,
    discover_session_files,
    inspect_session_file,
)


def jsonl_line(timestamp: str, record_type: str, payload: object) -> bytes:
    value = {"timestamp": timestamp, "type": record_type, "payload": payload}
    return (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")


class JsonlReadStreamTests(unittest.TestCase):
    def test_reads_unicode_records_and_reports_byte_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "rollout-test.jsonl"
            first = jsonl_line("2026-08-08T01:00:00Z", "event_msg", {"message": "日本語😀"})
            second = jsonl_line("2026-08-08T01:00:01Z", "world_state", {"full": True})
            path.write_bytes(first + second)

            with JsonlReadStream(path) as reader:
                records = list(reader)

            self.assertEqual(2, len(records))
            self.assertEqual(0, records[0].start_offset)
            self.assertEqual(len(first), records[0].end_offset)
            self.assertEqual(len(first + second), reader.next_offset)
            self.assertFalse(reader.has_incomplete_tail)

    def test_defers_incomplete_trailing_line_until_next_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "rollout-test.jsonl"
            first = jsonl_line("2026-08-08T01:00:00Z", "event_msg", {"type": "task_started"})
            partial = b'{"timestamp":"2026-08-08T01:00:01Z"'
            path.write_bytes(first + partial)

            with JsonlReadStream(path) as reader:
                records = list(reader)
            self.assertEqual(1, len(records))
            self.assertEqual(len(first), reader.next_offset)
            self.assertTrue(reader.has_incomplete_tail)

            completed = b',"type":"event_msg","payload":{"type":"task_complete"}}\n'
            with path.open("ab") as stream:
                stream.write(completed)
            with JsonlReadStream(path, reader.next_offset) as resumed_reader:
                resumed_records = list(resumed_reader)
            self.assertEqual(1, len(resumed_records))
            self.assertEqual("task_complete", resumed_records[0].payload["type"])

    def test_skips_bad_middle_lines_and_unknown_record_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "rollout-test.jsonl"
            data = b"".join(
                [
                    jsonl_line("2026-08-08T01:00:00Z", "event_msg", {}),
                    b"not-json\n",
                    jsonl_line("2026-08-08T01:00:01Z", "future_type", {}),
                    jsonl_line("2026-08-08T01:00:02Z", "turn_context", {}),
                ]
            )
            path.write_bytes(data)

            with JsonlReadStream(path) as reader:
                records = list(reader)

            self.assertEqual(["event_msg", "turn_context"], [item.record_type for item in records])
            self.assertEqual(
                ["malformed_json", "unknown_record_type"],
                [issue.kind for issue in reader.issues],
            )
            self.assertEqual(len(data), reader.next_offset)

    def test_restarts_when_saved_offset_is_beyond_file_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "rollout-test.jsonl"
            path.write_bytes(jsonl_line("2026-08-08T01:00:00Z", "event_msg", {}))

            with JsonlReadStream(path, 10000) as reader:
                records = list(reader)

            self.assertEqual(1, len(records))
            self.assertEqual("file_truncated", reader.issues[0].kind)

    def test_finished_reader_does_not_restart_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "rollout-test.jsonl"
            path.write_bytes(jsonl_line("2026-08-08T01:00:00Z", "event_msg", {}))

            with JsonlReadStream(path) as reader:
                self.assertEqual(1, len(list(reader)))
                self.assertEqual([], list(reader))

    def test_resumed_reader_keeps_physical_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "rollout-test.jsonl"
            first = jsonl_line("2026-08-08T01:00:00Z", "event_msg", {})
            path.write_bytes(
                first + jsonl_line("2026-08-08T01:00:01Z", "turn_context", {})
            )

            with JsonlReadStream(path, len(first), 1) as reader:
                records = list(reader)

            self.assertEqual(2, records[0].line_number)


class SessionDiscoveryTests(unittest.TestCase):
    def test_discovers_live_and_archived_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sessions = root / "sessions"
            archives = root / "archived_sessions"
            live_file = sessions / "2026" / "08" / "08" / "rollout-live.jsonl"
            archive_file = archives / "rollout-archive.jsonl"
            unrelated_file = sessions / "unrelated.jsonl"
            live_file.parent.mkdir(parents=True)
            archives.mkdir()
            live_file.write_bytes(b"")
            archive_file.write_bytes(b"")
            unrelated_file.write_bytes(b"")

            found = discover_session_files(sessions, archives, True)

            self.assertEqual(2, len(found))
            self.assertEqual(
                {"sessions", "archived_sessions"}, {item.location for item in found}
            )

    def test_inspects_metadata_and_workspace_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            session_id = "019fc727-eea9-7a70-976e-c7a5f7f23690"
            path = Path(temporary_directory) / f"rollout-date-{session_id}.jsonl"
            path.write_bytes(
                jsonl_line(
                    "2026-08-08T01:00:00Z",
                    "session_meta",
                    {"session_id": session_id, "cwd": r"C:\codex\One"},
                )
                + jsonl_line(
                    "2026-08-08T02:00:00Z",
                    "turn_context",
                    {
                        "cwd": r"C:\codex\Two",
                        "workspace_roots": [r"C:\codex\One"],
                    },
                )
            )

            descriptor = inspect_session_file(DiscoveredSessionFile(path, "sessions"))

            self.assertEqual(session_id, descriptor.session_id)
            self.assertEqual("2026-08-08T02:00:00Z", descriptor.last_timestamp)
            self.assertEqual((r"C:\codex\One", r"C:\codex\Two"), descriptor.workspace_candidates)
            self.assertEqual(2, descriptor.record_count)

    def test_index_groups_moved_file_by_session_id_and_selects_newest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            session_id = "019fc727-eea9-7a70-976e-c7a5f7f23690"
            old_path = root / f"rollout-old-{session_id}.jsonl"
            new_path = root / f"rollout-new-{session_id}.jsonl"
            old_path.write_bytes(jsonl_line("2026-08-08T01:00:00Z", "event_msg", {}))
            new_path.write_bytes(jsonl_line("2026-08-08T02:00:00Z", "event_msg", {}))

            index = build_session_index(
                [
                    DiscoveredSessionFile(old_path, "archived_sessions"),
                    DiscoveredSessionFile(new_path, "sessions"),
                ]
            )

            self.assertEqual(1, len(index))
            self.assertEqual(new_path, index[session_id].current_file.path)
            self.assertEqual(2, len(index[session_id].files))


if __name__ == "__main__":
    unittest.main()
