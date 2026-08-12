import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.json_converter import JsonSnapshot, encode_json
from tools.json_writer import UnsafeJsonPathError, save_json_snapshot


def snapshot(extra_path: str = "messages/pages/page-000001.json") -> JsonSnapshot:
    common = {
        "schema_version": "1.0",
        "snapshot_id": "snapshot-1",
        "generated_at": "2026-08-13T12:00:00+09:00",
        "workspace_id": "sample-project",
        "session_id": "session-1",
        "warnings": [],
    }
    nested = dict(common)
    nested.update(
        {
            "data_type": "messages_page",
            "page": 1,
            "messages": [{"text": "日本語📱"}],
        }
    )
    metadata = dict(common)
    metadata.update(
        {
            "data_type": "metadata",
            "collector": {},
            "snapshot": {"state": "complete", "files": []},
        }
    )
    return JsonSnapshot({extra_path: nested, "metadata.json": metadata})


class JsonWriterTests(unittest.TestCase):
    def test_saves_all_documents_under_workspace_as_utf8_without_bom(self) -> None:
        source = snapshot()
        with tempfile.TemporaryDirectory() as directory:
            result = save_json_snapshot(source, Path(directory) / "new-output")

            expected_root = (Path(directory) / "new-output" / "sample-project").resolve()
            self.assertEqual(expected_root, result.workspace_directory)
            self.assertEqual(2, len(result.files))
            for saved in result.files:
                data = (expected_root / Path(saved.path)).read_bytes()
                self.assertEqual(encode_json(source.documents[saved.path]), data)
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
                self.assertTrue(data.endswith(b"\n"))
                self.assertEqual(len(data), saved.byte_size)
                self.assertEqual(hashlib.sha256(data).hexdigest(), saved.sha256)
            nested = (
                expected_root / "messages" / "pages" / "page-000001.json"
            ).read_bytes()
            self.assertIn("日本語📱".encode("utf-8"), nested)
            self.assertNotIn(b"\\u65e5", nested)

    def test_rejects_parent_traversal_before_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with self.assertRaises(UnsafeJsonPathError):
                save_json_snapshot(snapshot("../escape.json"), output)

            self.assertFalse(output.exists())
            self.assertFalse((Path(directory) / "escape.json").exists())

    def test_rejects_absolute_backslash_and_non_json_paths(self) -> None:
        invalid_paths = (
            "/absolute.json",
            "messages\\page.json",
            "messages/page.txt",
        )
        with tempfile.TemporaryDirectory() as directory:
            for invalid_path in invalid_paths:
                with self.subTest(path=invalid_path):
                    with self.assertRaises(UnsafeJsonPathError):
                        save_json_snapshot(snapshot(invalid_path), Path(directory))

    def test_rejects_unsafe_workspace_id(self) -> None:
        source = snapshot()
        metadata = dict(source.documents["metadata.json"])
        metadata["workspace_id"] = "../outside"
        source = JsonSnapshot(
            {
                "messages/pages/page-000001.json": source.documents[
                    "messages/pages/page-000001.json"
                ],
                "metadata.json": metadata,
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UnsafeJsonPathError):
                save_json_snapshot(source, Path(directory) / "output")

    def test_rejects_mixed_snapshot_identity_before_creating_output(self) -> None:
        source = snapshot()
        page = dict(source.documents["messages/pages/page-000001.json"])
        page["snapshot_id"] = "different-snapshot"
        source = JsonSnapshot(
            {
                "messages/pages/page-000001.json": page,
                "metadata.json": source.documents["metadata.json"],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            with self.assertRaisesRegex(ValueError, "snapshot_identity_mismatch"):
                save_json_snapshot(source, output)

            self.assertFalse(output.exists())
    def test_rejects_case_insensitive_duplicate_targets(self) -> None:
        source = snapshot()
        first = source.documents["messages/pages/page-000001.json"]
        metadata = source.documents["metadata.json"]
        source = JsonSnapshot(
            {
                "messages/Page.json": first,
                "messages/page.json": first,
                "metadata.json": metadata,
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(UnsafeJsonPathError):
                save_json_snapshot(source, Path(directory) / "output")


if __name__ == "__main__":
    unittest.main()
