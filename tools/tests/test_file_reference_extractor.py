import tempfile
import unittest
from pathlib import Path

from tools.chat_extractor import ChatContentPart, ExtractedChatMessage
from tools.file_reference_extractor import extract_file_references


def chat_message(role: str, text: str, message_id: str) -> ExtractedChatMessage:
    return ExtractedChatMessage(
        message_id=message_id,
        source_message_id=message_id,
        sequence=1,
        created_at="2026-08-08T01:00:00+00:00",
        role=role,
        message_type="chat" if role in ("user", "assistant") else "tool_summary",
        phase="final_answer" if role == "assistant" else None,
        turn_id="turn-1",
        content=(ChatContentPart("text", text),),
        display_mode="expanded",
        duplicate_of=None,
        occurrence_count=1,
        removed_automatic_contexts=tuple(),
        source_path=Path("rollout-test.jsonl"),
        source_line_number=1,
        source_start_offset=10,
    )


class FileReferenceExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.workspace = self.base / "Project"
        self.workspace.mkdir()
        (self.workspace / "tools").mkdir()
        (self.workspace / "tools" / "app.py").write_text("print('ok')\n", encoding="utf-8")
        (self.workspace / "README.md").write_text("readme\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def extract(self, *messages: ExtractedChatMessage):
        return extract_file_references(
            tuple(("session-1", message) for message in messages),
            self.workspace,
            "workspace-1",
        )

    def test_long_workspace_path_preserves_name_and_line_reference(self) -> None:
        relative = "/".join(["long_directory_segment"] * 14 + ["report.py"])
        self.assertGreater(len(relative), 260)
        target = self.workspace / relative
        target.parent.mkdir(parents=True)
        target.write_text("pass\n", encoding="utf-8")
        result = self.extract(chat_message("user", f"`{relative}:123`", "long-path"))
        self.assertEqual(1, len(result.references))
        reference = result.references[0]
        self.assertEqual(relative, reference.path)
        self.assertEqual("report.py", reference.display_name)
        self.assertEqual("workspace", reference.scope)
        self.assertEqual(123, reference.mentions[0].line)
        self.assertNotIn(str(self.workspace), repr(reference))

    def test_workspace_absolute_path_becomes_relative(self) -> None:
        path = self.workspace / "tools" / "app.py"

        result = self.extract(chat_message("user", f'確認対象は"{path}"です。', "msg-1"))

        reference = result.references[0]
        self.assertEqual("workspace", reference.scope)
        self.assertEqual("tools/app.py", reference.path)
        self.assertEqual("app.py", reference.display_name)
        self.assertEqual("file", reference.kind)
        self.assertNotIn(str(self.workspace), repr(reference))

    def test_relative_path_and_locations_are_aggregated(self) -> None:
        result = self.extract(
            chat_message("user", "tools/app.py:10を確認してください。", "msg-1"),
            chat_message("assistant", "`tools/app.py:20:3`を修正しました。", "msg-2"),
        )

        self.assertEqual(1, len(result.references))
        reference = result.references[0]
        self.assertEqual(2, reference.mention_count)
        self.assertEqual(("msg-1", "msg-2"), reference.source_message_ids)
        self.assertEqual((10, 20), tuple(item.line for item in reference.mentions))
        self.assertEqual((None, 3), tuple(item.column for item in reference.mentions))

    def test_bare_filename_and_directory_are_detected(self) -> None:
        result = self.extract(
            chat_message("user", "README.mdとtools/を確認します。", "msg-1")
        )

        by_path = {item.path: item for item in result.references}
        self.assertEqual("file", by_path["README.md"].kind)
        self.assertEqual("directory", by_path["tools"].kind)

    def test_external_attachment_sends_only_filename(self) -> None:
        attachment = self.base / "Outside Folder" / "reference.pdf"
        attachment.parent.mkdir()
        attachment.write_bytes(b"pdf")
        text = (
            "# Files mentioned by the user:\n\n"
            f"## reference.pdf: {attachment}\n\n"
            "## My request for Codex:\n内容を確認してください。"
        )

        result = self.extract(chat_message("user", text, "msg-1"))

        reference = result.references[0]
        self.assertEqual("external", reference.scope)
        self.assertIsNone(reference.path)
        self.assertEqual("reference.pdf", reference.display_name)
        self.assertEqual(".pdf", reference.extension)
        self.assertEqual("file", reference.kind)
        self.assertNotIn(str(attachment.parent), repr(reference))

    def test_external_same_name_in_different_messages_is_not_merged(self) -> None:
        first = self.base / "One" / "same.pdf"
        second = self.base / "Two" / "same.pdf"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_bytes(b"1")
        second.write_bytes(b"2")
        first_text = f"# Files mentioned by the user:\n## same.pdf: {first}\n"
        second_text = f"# Files mentioned by the user:\n## same.pdf: {second}\n"

        result = self.extract(
            chat_message("user", first_text, "msg-1"),
            chat_message("user", second_text, "msg-2"),
        )

        self.assertEqual(2, len(result.references))
        self.assertNotEqual(result.references[0].reference_id, result.references[1].reference_id)

    def test_markdown_local_link_is_detected(self) -> None:
        result = self.extract(
            chat_message("assistant", "[実装](tools/app.py:7)を確認してください。", "msg-1")
        )

        self.assertEqual("tools/app.py", result.references[0].path)
        self.assertEqual(7, result.references[0].mentions[0].line)
        self.assertEqual("markdown_link", result.references[0].mentions[0].origin)

    def test_fenced_code_paths_and_urls_are_ignored(self) -> None:
        text = """URLはhttps://example.com/file.pyです。
```python
path = "tools/app.py"
```
"""

        result = self.extract(chat_message("user", text, "msg-1"))

        self.assertEqual(tuple(), result.references)

    def test_tool_and_developer_messages_are_ignored(self) -> None:
        result = self.extract(
            chat_message("tool", "tools/app.py", "msg-tool"),
            chat_message("developer", "README.md", "msg-dev"),
        )

        self.assertEqual(tuple(), result.references)

    def test_parent_traversal_is_external_and_hides_parent(self) -> None:
        outside = self.base / "secret.txt"
        outside.write_text("secret", encoding="utf-8")

        result = self.extract(chat_message("user", "../secret.txtを参照します。", "msg-1"))

        reference = result.references[0]
        self.assertEqual("external", reference.scope)
        self.assertIsNone(reference.path)
        self.assertEqual("secret.txt", reference.display_name)

    def test_unc_path_is_external_without_existence_probe(self) -> None:
        result = self.extract(
            chat_message("user", r"\\server\share\folder\report.pdfを参照します。", "msg-1")
        )

        reference = result.references[0]
        self.assertEqual("external", reference.scope)
        self.assertEqual("unknown", reference.kind)
        self.assertIsNone(reference.path)
        self.assertEqual("report.pdf", reference.display_name)

    def test_unknown_missing_extension_in_prose_is_ignored(self) -> None:
        result = self.extract(
            chat_message("assistant", "処理結果はpayload.typeです。", "msg-1")
        )

        self.assertEqual(tuple(), result.references)

    def test_explicit_path_with_unknown_extension_is_detected(self) -> None:
        result = self.extract(
            chat_message("user", "schema/payload.typeを確認します。", "msg-1")
        )

        self.assertEqual("schema/payload.type", result.references[0].path)
        self.assertEqual("missing", result.references[0].kind)

    def test_missing_workspace_file_is_reported(self) -> None:
        result = self.extract(chat_message("user", "tools/missing.pyを確認します。", "msg-1"))

        self.assertEqual("missing", result.references[0].kind)

    def test_unclosed_fenced_code_is_ignored_to_end_of_message(self) -> None:
        result = self.extract(
            chat_message("user", "```text\ntools/app.py\nREADME.md", "msg-1")
        )

        self.assertEqual(tuple(), result.references)


if __name__ == "__main__":
    unittest.main()
