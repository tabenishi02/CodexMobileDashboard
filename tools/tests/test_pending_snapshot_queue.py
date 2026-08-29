import json
import tempfile
import unittest
from pathlib import Path

from tools.https_sender import SendErrorKind, SenderError, SnapshotUpload, new_delivery_id
from tools.pending_snapshot_queue import PendingSnapshotQueue


class SuccessfulSender:
    def __init__(self):
        self.calls = []

    def send_snapshot_with_retry(self, workspace_id, snapshot_id, uploads, *, commit_delivery_id):
        self.calls.append((workspace_id, snapshot_id, tuple(uploads), commit_delivery_id))
        return "sent"


class FailingSender:
    def send_snapshot_with_retry(self, workspace_id, snapshot_id, uploads, *, commit_delivery_id):
        raise SenderError(SendErrorKind.HTTP_401_403, retryable=False, operation="file")


class PendingSnapshotQueueTests(unittest.TestCase):
    def uploads(self):
        return (
            SnapshotUpload("z.json", b'{"z":1}\n', new_delivery_id()),
            SnapshotUpload("messages/a.json", b'{"a":1}\n', new_delivery_id()),
        )

    def test_enqueue_preserves_bytes_delivery_ids_and_order(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingSnapshotQueue(Path(directory))
            uploads = self.uploads()
            queued = queue.enqueue("workspace-1", "snapshot-1", uploads, commit_delivery_id=new_delivery_id())
            restored = queue.pending()

            self.assertEqual((queued.sequence,), tuple(item.sequence for item in restored))
            self.assertEqual(["messages/a.json", "z.json"], [item.relative_json_path for item in restored[0].uploads])
            by_path = {item.relative_json_path: item for item in restored[0].uploads}
            self.assertEqual(uploads[0].body, by_path["z.json"].body)
            self.assertEqual(uploads[0].delivery_id, by_path["z.json"].delivery_id)
            manifest = (queued.item_directory / "manifest.json").read_text(encoding="utf-8")
            self.assertNotIn("token", manifest.lower())

    def test_keeps_global_enqueue_order_across_workspaces(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingSnapshotQueue(Path(directory))
            queue.enqueue("workspace-b", "snapshot-b", self.uploads(), commit_delivery_id=new_delivery_id())
            queue.enqueue("workspace-a", "snapshot-a", self.uploads(), commit_delivery_id=new_delivery_id())

            self.assertEqual(["snapshot-b", "snapshot-a"], [item.snapshot_id for item in queue.pending()])

    def test_successful_commit_acknowledges_only_oldest_item(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingSnapshotQueue(Path(directory))
            first = queue.enqueue("workspace-1", "snapshot-1", self.uploads(), commit_delivery_id=new_delivery_id())
            queue.enqueue("workspace-1", "snapshot-2", self.uploads(), commit_delivery_id=new_delivery_id())
            sender = SuccessfulSender()

            self.assertEqual("sent", queue.send_next(sender))
            self.assertEqual(["snapshot-2"], [item.snapshot_id for item in queue.pending()])
            self.assertEqual(first.commit_delivery_id, sender.calls[0][3])

    def test_failed_resend_keeps_queue_item(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingSnapshotQueue(Path(directory))
            queued = queue.enqueue("workspace-1", "snapshot-1", self.uploads(), commit_delivery_id=new_delivery_id())

            with self.assertRaises(SenderError):
                queue.send_next(FailingSender())

            self.assertEqual((queued.sequence,), tuple(item.sequence for item in queue.pending()))

    def test_final_failure_is_enqueued_before_it_is_reraised(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingSnapshotQueue(Path(directory))
            commit_delivery_id = new_delivery_id()

            with self.assertRaises(SenderError):
                queue.send_or_enqueue(
                    FailingSender(),
                    "workspace-1",
                    "snapshot-1",
                    self.uploads(),
                    commit_delivery_id=commit_delivery_id,
                )

            pending = queue.pending()
            self.assertEqual(1, len(pending))
            self.assertEqual(commit_delivery_id, pending[0].commit_delivery_id)
            self.assertEqual("http_401_403", pending[0].last_error_kind)
    def test_detects_tampered_queued_file(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = PendingSnapshotQueue(Path(directory))
            queued = queue.enqueue("workspace-1", "snapshot-1", self.uploads(), commit_delivery_id=new_delivery_id())
            (queued.item_directory / "files" / "z.json").write_bytes(b"changed")

            with self.assertRaisesRegex(ValueError, "queue_file_integrity_invalid"):
                queue.pending()


if __name__ == "__main__":
    unittest.main()
