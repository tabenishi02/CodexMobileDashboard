import hashlib
import logging
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Optional

from tools.change_summary_generator import (
    ChangeSummary,
    ChangeSummaryGenerationResult,
    SummaryEvidenceItem,
)
from tools.chat_extractor import (
    ChatContentPart,
    ChatExtractionResult,
    ChatRedaction,
    ExtractedChatMessage,
)
from tools.decision_extractor import DecisionExtractionResult, ExtractedDecision
from tools.error_extractor import ErrorExtractionResult, ExtractedDevelopmentError
from tools.file_reference_extractor import FileReferenceExtractionResult
from tools.git_change_collector import (
    GitCollectionResult,
    GitFileChange,
    GitFileStatus,
    GitNumstat,
    GitRepositoryState,
)
from tools.json_converter import (
    CollectorMetadata,
    JsonContext,
    MESSAGE_CHUNK_MAX_BYTES,
    ProjectPresentation,
    UnsafeJsonInputError,
    build_json_snapshot,
    encode_json,
)
from tools.logging_setup import configure_component_logging
from tools.next_task_extractor import NextTask, NextTaskExtractionResult
from tools.work_status_extractor import CurrentWorkStatus, TurnWorkState


def message(
    sequence: int,
    role: str,
    text: str,
    *,
    phase: Optional[str] = None,
    turn_id: str = "turn-1",
) -> ExtractedChatMessage:
    return ExtractedChatMessage(
        message_id=f"msg-{sequence}",
        source_message_id=f"msg-{sequence}",
        sequence=sequence,
        created_at=f"2026-08-13T10:0{sequence}:00+09:00",
        role=role,
        message_type="chat",
        phase=phase,
        turn_id=turn_id,
        content=(ChatContentPart("text", text),),
        display_mode="expanded",
        duplicate_of=None,
        occurrence_count=1,
        removed_automatic_contexts=tuple(),
        source_path=Path("rollout-sample.jsonl"),
        source_line_number=sequence,
        source_start_offset=sequence * 100,
    )


def turn(*, rolled_back: bool = False) -> TurnWorkState:
    return TurnWorkState(
        turn_id="turn-1",
        turn_id_source="jsonl",
        status="completed",
        started_at="2026-08-13T10:01:00+09:00",
        started_at_source="event_field",
        completed_at="2026-08-13T10:02:00+09:00",
        completed_at_source="event_field",
        duration_ms=60_000,
        reason=None,
        user_message_id="msg-1",
        assistant_message_ids=("msg-2",),
        rolled_back=rolled_back,
    )


def summary(*, rolled_back: bool = False) -> ChangeSummary:
    evidence = SummaryEvidenceItem("12テストが成功", ("msg-2",))
    return ChangeSummary(
        summary_id="summary-1",
        turn_id="turn-1",
        turn_id_source="jsonl",
        status="completed",
        rolled_back=rolled_back,
        title="JSON変換を実装",
        short_summary="各情報をJSONへ変換しました。",
        details="表示用の文書一式をメモリ内で生成しました。",
        highlights=(SummaryEvidenceItem("変換処理を追加", ("msg-2",)),),
        verification=(evidence,),
        origin="explicit",
        confidence="high",
        source_session_ids=("session-1",),
        source_message_ids=("msg-2",),
    )


def development_error() -> ExtractedDevelopmentError:
    return ExtractedDevelopmentError(
        error_id="error-1",
        fingerprint="a" * 64,
        kind="command_failure",
        first_occurred_at="2026-08-13T10:02:00+09:00",
        last_occurred_at="2026-08-13T10:02:00+09:00",
        occurred_at_source="record_timestamp",
        occurrence_count=1,
        severity="error",
        category="command",
        summary="コマンドが失敗しました。",
        details_preview="失敗内容",
        details_text="失敗内容の欠落のない全文",
        details_complete=True,
        source_message_ids=("msg-2",),
        source_path=Path("rollout-sample.jsonl"),
        source_line_number=10,
        source_start_offset=1000,
        status="open",
        resolved_at=None,
        resolution=None,
        rolled_back=False,
        operation_key="operation-1",
    )


def git_result() -> GitCollectionResult:
    not_applicable = GitNumstat("not_applicable", None, None)
    change = GitFileChange(
        change_id="change-1",
        path="tools/json_converter.py",
        old_path=None,
        status=GitFileStatus("modified", "none", "none"),
        staged=GitNumstat("measured", 10, 0),
        unstaged=not_applicable,
        committed_in_session=not_applicable,
        binary=False,
        scopes=("staged",),
    )
    repository = GitRepositoryState(
        collection_status="ok",
        root_name="sample-project",
        branch="main",
        head="a" * 40,
        session_start_commit="b" * 40,
        session_start_source="jsonl",
        clean=False,
    )
    return GitCollectionResult(repository, (change,), tuple(), False)


class JsonConverterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = (
            message(1, "user", "JSON変換を実装してください。"),
            message(
                2,
                "assistant",
                "説明です。\n```python\nprint('ok')\n```\n完了しました。",
                phase="final_answer",
            ),
        )
        self.context = JsonContext(
            snapshot_id="snapshot-1",
            generated_at="2026-08-13T01:05:00+00:00",
            workspace_id="sample-project",
            session_id="session-1",
        )

    def build(
        self,
        *,
        messages=None,
        rolled_back: bool = False,
        content_is_masked: bool = True,
        phase: Optional[str] = "Phase 3",
    ):
        source_messages = tuple(messages) if messages is not None else self.messages
        work = CurrentWorkStatus(
            codex_status="idle",
            current_work=None,
            current_work_message_id=None,
            active_turn_id=None,
            latest_turn_id="turn-1",
            latest_turn_status="completed",
            last_event_at="2026-08-13T10:02:00+09:00",
            turns=(turn(rolled_back=rolled_back),),
            issues=tuple(),
        )
        next_value = NextTask(
            task_id="task-1",
            text="UTF-8でJSONを保存する",
            status="pending",
            origin="explicit",
            confidence="high",
            reason=None,
            source_message_ids=("msg-2",),
        )
        return build_json_snapshot(
            self.context,
            ProjectPresentation("サンプル", phase),
            ChatExtractionResult(source_messages, tuple(), tuple()),
            work,
            NextTaskExtractionResult(
                next_value, tuple(), None, False, 0, False
            ),
            ErrorExtractionResult((development_error(),), tuple()),
            DecisionExtractionResult(
                (
                    ExtractedDecision(
                        decision_id="decision-1",
                        decided_at="2026-08-13T09:00:00+09:00",
                        decided_at_source="message_timestamp",
                        status="adopted",
                        title="推奨案を採用",
                        description="変更要約ページを追加します。",
                        reason=None,
                        source_session_ids=("session-1",),
                        source_message_ids=("msg-1",),
                        supersedes=None,
                        superseded_by=None,
                        topic_key="変更要約保存先",
                    ),
                ),
                tuple(),
            ),
            FileReferenceExtractionResult(tuple(), tuple()),
            git_result(),
            ChangeSummaryGenerationResult(
                (summary(rolled_back=rolled_back),), tuple(), tuple(), 0
            ),
            CollectorMetadata(
                status="ok",
                last_checked_at="2026-08-13T10:05:00+09:00",
                last_data_change_at="2026-08-13T10:02:00+09:00",
                last_acknowledged_snapshot_id=None,
                last_send_succeeded_at=None,
            ),
            content_is_masked=content_is_masked,
        )

    def test_converter_log_records_success_and_safe_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            logger = logging.getLogger("converter")
            secret = "token=converter-secret C:\\private\\source.jsonl"
            secret_message = replace(
                self.messages[1], content=(ChatContentPart("text", secret),)
            )
            try:
                configure_component_logging(Path(directory), components=("converter",))
                self.build(messages=(self.messages[0], secret_message))
                with self.assertRaises(UnsafeJsonInputError):
                    self.build(
                        messages=(self.messages[0], secret_message),
                        content_is_masked=False,
                    )
            finally:
                for handler in tuple(logger.handlers):
                    logger.removeHandler(handler)
                    handler.close()

            content = (Path(directory) / "converter.log").read_text(encoding="utf-8")
            self.assertIn("INFO converter 表示用JSONへ変換:", content)
            self.assertIn("ERROR converter JSON変換を中止: 未マスク入力", content)
            self.assertNotIn("converter-secret", content)
            self.assertNotIn("source.jsonl", content)

    def test_builds_all_documents_and_summary_page_index(self) -> None:
        snapshot = self.build()

        expected = {
            "dashboard.json",
            "recent.json",
            "messages.json",
            "messages/pages/page-000001.json",
            "messages/summaries/summary-page-000001.json",
            "errors.json",
            "decisions.json",
            "files.json",
            "metadata.json",
        }
        self.assertEqual(expected, set(snapshot.documents))
        index = snapshot.document("messages.json")
        self.assertEqual(1, index["total_change_summaries"])
        summary_index = index["summary_pages"][0]
        page_bytes = snapshot.encoded(summary_index["path"])
        self.assertEqual(len(page_bytes), summary_index["byte_size"])
        self.assertEqual(
            hashlib.sha256(page_bytes).hexdigest(), summary_index["sha256"]
        )
        metadata_paths = {
            value["path"]
            for value in snapshot.document("metadata.json")["snapshot"]["files"]
        }
        self.assertNotIn("metadata.json", metadata_paths)
        self.assertIn("messages/summaries/summary-page-000001.json", metadata_paths)
        self.assertEqual(
            "2026-08-13T01:05:00+00:00",
            snapshot.document("dashboard.json")["generated_at"],
        )

    def test_allows_missing_project_phase(self) -> None:
        snapshot = self.build(phase=None)

        self.assertIsNone(snapshot.document("dashboard.json")["project"]["phase"])

    def test_splits_fenced_code_and_preserves_malformed_fence_as_text(self) -> None:
        snapshot = self.build()
        page = snapshot.document("messages/pages/page-000001.json")
        blocks = page["messages"][1]["content"]["blocks"]

        self.assertEqual(["text", "code", "text"], [value["type"] for value in blocks])
        self.assertEqual("python", blocks[1]["language"])
        self.assertEqual("print('ok')\n", blocks[1]["text"])

        malformed = replace(
            self.messages[1], content=(ChatContentPart("text", "```python\nprint(1)"),)
        )
        snapshot = self.build(messages=(self.messages[0], malformed))
        blocks = snapshot.document("messages/pages/page-000001.json")["messages"][1][
            "content"
        ]["blocks"]
        self.assertEqual("text", blocks[0]["type"])
        self.assertEqual("```python\nprint(1)", blocks[0]["text"])

    def test_redactions_reference_the_final_content_block(self) -> None:
        redacted = replace(
            self.messages[1],
            content=(
                ChatContentPart(
                    "text",
                    "説明\n```text\ntoken=[REDACTED:TOKEN]\n```",
                    (ChatRedaction("token", "sensitive_assignment"),),
                ),
            ),
        )

        snapshot = self.build(messages=(self.messages[0], redacted))
        value = snapshot.document("messages/pages/page-000001.json")["messages"][1]

        self.assertEqual(1, len(value["redactions"]))
        redaction = value["redactions"][0]
        code_block = value["content"]["blocks"][1]
        self.assertEqual(code_block["block_id"], redaction["block_id"])
        self.assertEqual("token", redaction["type"])
        self.assertEqual("sensitive_assignment", redaction["detector"])
        encoded = snapshot.encoded("messages/pages/page-000001.json")
        self.assertNotIn("token-value", encoded.decode("utf-8"))

    def test_splits_large_message_without_losing_utf8_text(self) -> None:
        text = ("日本語🙂の長文です。\n" * 40_000) + "末尾です。"
        large = replace(
            self.messages[1],
            message_id="msg/large-日本語",
            source_message_id="msg/large-日本語",
            content=(ChatContentPart("text", text),),
        )

        snapshot = self.build(messages=(self.messages[0], large))
        message_value = snapshot.document("messages/pages/page-000001.json")[
            "messages"
        ][1]
        content = message_value["content"]

        self.assertEqual("chunked_blocks", content["kind"])
        self.assertGreater(content["chunk_count"], 1)
        self.assertEqual(len(text.encode("utf-8")), content["original_byte_size"])
        self.assertEqual(
            hashlib.sha256(text.encode("utf-8")).hexdigest(), content["sha256"]
        )
        restored = []
        for expected_part, reference in enumerate(content["chunks"], start=1):
            self.assertEqual(expected_part, reference["part"])
            self.assertNotIn("/large", reference["path"])
            document = snapshot.document(reference["path"])
            encoded = snapshot.encoded(reference["path"])
            self.assertLessEqual(len(encoded), MESSAGE_CHUNK_MAX_BYTES)
            self.assertEqual(len(encoded), reference["byte_size"])
            self.assertEqual(
                hashlib.sha256(encoded).hexdigest(), reference["sha256"]
            )
            self.assertEqual("message_chunk", document["data_type"])
            self.assertEqual("msg/large-日本語", document["message_id"])
            self.assertEqual(expected_part, document["part"])
            self.assertEqual(content["chunk_count"], document["total_parts"])
            restored.extend(str(block["text"]) for block in document["blocks"])
        self.assertEqual(text, "".join(restored))

        metadata_paths = {
            value["path"]
            for value in snapshot.document("metadata.json")["snapshot"]["files"]
        }
        self.assertTrue(
            {value["path"] for value in content["chunks"]} <= metadata_paths
        )

    def test_preserves_code_block_metadata_across_chunks(self) -> None:
        code = "print('🙂')\n" * 30_000
        source = f"説明です。\n```python\n{code}```\n完了です。"
        large = replace(
            self.messages[1], content=(ChatContentPart("text", source),)
        )

        snapshot = self.build(messages=(self.messages[0], large))
        content = snapshot.document("messages/pages/page-000001.json")["messages"][1][
            "content"
        ]
        restored_blocks = []
        for reference in content["chunks"]:
            for block in snapshot.document(reference["path"])["blocks"]:
                if (
                    restored_blocks
                    and restored_blocks[-1]["block_id"] == block["block_id"]
                ):
                    restored_blocks[-1]["text"] += block["text"]
                else:
                    restored_blocks.append(dict(block))

        self.assertEqual(
            ["text", "code", "text"],
            [block["type"] for block in restored_blocks],
        )
        self.assertEqual("python", restored_blocks[1]["language"])
        self.assertEqual(code, restored_blocks[1]["text"])
        self.assertEqual("説明です。\n", restored_blocks[0]["text"])
        self.assertEqual("完了です。", restored_blocks[2]["text"])

    def test_rejects_content_not_certified_as_masked(self) -> None:
        with self.assertRaises(UnsafeJsonInputError):
            build_json_snapshot(
                self.context,
                ProjectPresentation("サンプル", "Phase 3"),
                ChatExtractionResult(self.messages, tuple(), tuple()),
                CurrentWorkStatus(
                    "unknown", None, None, None, None, None, None, tuple(), tuple()
                ),
                NextTaskExtractionResult(None, tuple(), None, False, 0, False),
                ErrorExtractionResult(tuple(), tuple()),
                DecisionExtractionResult(tuple(), tuple()),
                FileReferenceExtractionResult(tuple(), tuple()),
                git_result(),
                ChangeSummaryGenerationResult(tuple(), tuple(), tuple(), 0),
                CollectorMetadata("ok", self.context.generated_at, None, None, None),
                content_is_masked=False,
            )

    def test_splits_message_pages_at_one_hundred_items(self) -> None:
        messages = tuple(
            message(index, "user", f"message {index}", turn_id=f"turn-{index}")
            for index in range(1, 102)
        )
        snapshot = self.build(messages=messages)
        index = snapshot.document("messages.json")

        self.assertEqual(2, len(index["pages"]))
        self.assertEqual(100, index["pages"][0]["message_count"])
        self.assertEqual(1, index["pages"][1]["message_count"])

    def test_keeps_complete_error_details_when_message_only_has_summary(self) -> None:
        snapshot = self.build()
        error = snapshot.document("errors.json")["errors"][0]

        self.assertEqual("inline", error["detail_storage"])
        self.assertEqual("失敗内容の欠落のない全文", error["details"])

    def test_rolled_back_turn_is_not_latest_or_recent(self) -> None:
        snapshot = self.build(rolled_back=True)

        self.assertEqual([], snapshot.document("recent.json")["turns"])
        self.assertIsNone(snapshot.document("dashboard.json")["latest"]["summary"])
        summary_page = snapshot.document(
            "messages/summaries/summary-page-000001.json"
        )
        self.assertTrue(summary_page["summaries"][0]["rolled_back"])

    def test_encoding_is_utf8_deterministic_and_not_ascii_escaped(self) -> None:
        snapshot = self.build()

        first = encode_json(snapshot.document("dashboard.json"))
        second = encode_json(snapshot.document("dashboard.json"))
        self.assertEqual(first, second)
        self.assertIn("サンプル".encode("utf-8"), first)
        self.assertNotIn(b"\\u30b5", first)


if __name__ == "__main__":
    unittest.main()
