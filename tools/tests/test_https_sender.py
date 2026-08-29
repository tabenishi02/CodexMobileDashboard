import json
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.https_sender import (
    HttpsSnapshotSender,
    SendErrorKind,
    SenderError,
    SnapshotUpload,
    build_ssl_context,
    new_delivery_id,
    normalize_relative_json_path,
    prepare_snapshot_uploads,
)


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self, amount):
        return self._body


class FakeConnection:
    def __init__(self, response=None, request_error=None, response_error=None):
        self.response = response
        self.request_error = request_error
        self.response_error = response_error
        self.requests = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        if self.request_error:
            raise self.request_error
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        if self.response_error:
            raise self.response_error
        return self.response

    def close(self):
        self.closed = True


class FakeConnectionFactory:
    def __init__(self, connections):
        self.connections = list(connections)
        self.created = []

    def __call__(self, *args, **kwargs):
        connection = self.connections.pop(0)
        self.created.append((args, kwargs, connection))
        return connection


def response(status, delivery_id, snapshot_id):
    return FakeResponse(
        200,
        json.dumps(
            {"status": status, "delivery_id": delivery_id, "snapshot_id": snapshot_id}
        ).encode("utf-8"),
    )


class HttpsSnapshotSenderTests(unittest.TestCase):
    def sender(self, factory):
        return HttpsSnapshotSender(
            "https://dashboard.example.test:8765",
            "secret-token",
            Path("unused-ca.crt"),
            connection_factory=factory,
            ssl_context=object(),
        )

    def test_posts_utf8_file_with_auth_delivery_and_then_commits(self):
        file_delivery = new_delivery_id()
        commit_delivery = new_delivery_id()
        factory = FakeConnectionFactory(
            [FakeConnection(response("stored", file_delivery, "snapshot-1")), FakeConnection(response("committed", commit_delivery, "snapshot-1"))]
        )
        body = '{"message":"日本語"}\n'.encode("utf-8")

        result = self.sender(factory).send_snapshot(
            "workspace-1", "snapshot-1", [SnapshotUpload("messages\\page.json", body, file_delivery)], commit_delivery_id=commit_delivery
        )

        self.assertEqual("snapshot-1", result.snapshot_id)
        method, path, sent_body, headers = factory.created[0][2].requests[0]
        self.assertEqual("POST", method)
        self.assertEqual("/api/v1/snapshots/workspace-1/snapshot-1/messages/page.json", path)
        self.assertEqual(body, sent_body)
        self.assertEqual("Bearer secret-token", headers["Authorization"])
        self.assertEqual(file_delivery, headers["X-Delivery-Id"])
        self.assertEqual("application/json", headers["Content-Type"])
        self.assertEqual("/api/v1/snapshots/workspace-1/snapshot-1/commit", factory.created[1][2].requests[0][1])
        self.assertIn(b'"messages/page.json"', factory.created[1][2].requests[0][2])

    def test_file_failure_does_not_call_commit(self):
        delivery = new_delivery_id()
        factory = FakeConnectionFactory([FakeConnection(FakeResponse(413, b"{}"))])

        with self.assertRaises(SenderError) as raised:
            self.sender(factory).send_snapshot(
                "workspace-1", "snapshot-1", [SnapshotUpload("data.json", b"{}", delivery)], commit_delivery_id=new_delivery_id()
            )

        self.assertEqual(SendErrorKind.HTTP_413, raised.exception.kind)
        self.assertEqual(1, len(factory.created))

    def test_http_errors_have_queue_useful_classification(self):
        cases = ((401, SendErrorKind.HTTP_401_403, False), (404, SendErrorKind.HTTP_404, False), (409, SendErrorKind.HTTP_409, False), (429, SendErrorKind.HTTP_429, True), (503, SendErrorKind.HTTP_5XX, True))
        for status, expected_kind, retryable in cases:
            with self.subTest(status=status):
                factory = FakeConnectionFactory([FakeConnection(FakeResponse(status, b"{}"))])
                with self.assertRaises(SenderError) as raised:
                    self.sender(factory).send_snapshot("workspace-1", "snapshot-1", [SnapshotUpload("data.json", b"{}", new_delivery_id())], commit_delivery_id=new_delivery_id())
                self.assertEqual(expected_kind, raised.exception.kind)
                self.assertEqual(retryable, raised.exception.retryable)

    def test_connect_and_read_timeouts_are_distinct(self):
        for request_error, response_error, expected in ((socket.timeout(), None, SendErrorKind.CONNECT_TIMEOUT), (None, socket.timeout(), SendErrorKind.READ_TIMEOUT)):
            with self.subTest(expected=expected):
                factory = FakeConnectionFactory([FakeConnection(request_error=request_error, response_error=response_error)])
                with self.assertRaises(SenderError) as raised:
                    self.sender(factory).send_snapshot("workspace-1", "snapshot-1", [SnapshotUpload("data.json", b"{}", new_delivery_id())], commit_delivery_id=new_delivery_id())
                self.assertEqual(expected, raised.exception.kind)
                self.assertTrue(raised.exception.retryable)

    def test_invalid_response_and_delivery_mismatch_are_rejected(self):
        delivery = new_delivery_id()
        factory = FakeConnectionFactory([FakeConnection(FakeResponse(200, b"not-json"))])
        with self.assertRaises(SenderError) as raised:
            self.sender(factory).send_snapshot("workspace-1", "snapshot-1", [SnapshotUpload("data.json", b"{}", delivery)], commit_delivery_id=new_delivery_id())
        self.assertEqual(SendErrorKind.INVALID_JSON_RESPONSE, raised.exception.kind)

        factory = FakeConnectionFactory([FakeConnection(response("stored", new_delivery_id(), "snapshot-1"))])
        with self.assertRaises(SenderError) as raised:
            self.sender(factory).send_snapshot("workspace-1", "snapshot-1", [SnapshotUpload("data.json", b"{}", delivery)], commit_delivery_id=new_delivery_id())
        self.assertEqual(SendErrorKind.DELIVERY_ID_MISMATCH, raised.exception.kind)

    def test_tls_context_uses_explicit_ca(self):
        class FakeTlsContext:
            verify_mode = ssl.CERT_REQUIRED
            check_hostname = True

        with patch("tools.https_sender.ssl.create_default_context", return_value=FakeTlsContext()) as create:
            self.assertIsInstance(build_ssl_context(Path("unused-ca.crt")), FakeTlsContext)
        self.assertEqual(str(Path("unused-ca.crt")), create.call_args.kwargs["cafile"])

    def test_path_validation_and_windows_relative_paths(self):
        self.assertEqual("messages/pages/page.json", normalize_relative_json_path(r"messages\pages\page.json"))
        for unsafe in ("../outside.json", "/absolute.json", r"C:\absolute.json", "data.txt"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(SenderError):
                    normalize_relative_json_path(unsafe)

    def test_prepare_uploads_keeps_relative_order_and_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "messages").mkdir()
            (root / "z.json").write_bytes(b"z")
            (root / "messages" / "a.json").write_bytes(b"a")
            uploads = prepare_snapshot_uploads(root, [r"z.json", r"messages\a.json"])
        self.assertEqual(["messages/a.json", "z.json"], [item.relative_json_path for item in uploads])
        self.assertEqual([b"a", b"z"], [item.body for item in uploads])

    def test_token_is_not_in_sender_error_or_log_message(self):
        token = "very-secret-token"
        failed_factory = FakeConnectionFactory([FakeConnection(request_error=socket.gaierror())])
        failed_sender = HttpsSnapshotSender("https://dashboard.example.test", token, Path("unused-ca.crt"), connection_factory=failed_factory, ssl_context=object())
        with self.assertRaises(SenderError) as raised:
            failed_sender.send_snapshot("workspace-1", "snapshot-1", [SnapshotUpload("data.json", b"{}", new_delivery_id())], commit_delivery_id=new_delivery_id())
        self.assertNotIn(token, str(raised.exception))

        file_delivery = new_delivery_id()
        commit_delivery = new_delivery_id()
        success_factory = FakeConnectionFactory([
            FakeConnection(response("stored", file_delivery, "snapshot-1")),
            FakeConnection(response("committed", commit_delivery, "snapshot-1")),
        ])
        success_sender = HttpsSnapshotSender("https://dashboard.example.test", token, Path("unused-ca.crt"), connection_factory=success_factory, ssl_context=object())
        with self.assertLogs("sender", level="INFO") as captured:
            success_sender.send_snapshot("workspace-1", "snapshot-1", [SnapshotUpload("data.json", b"{}", file_delivery)], commit_delivery_id=commit_delivery)
        self.assertNotIn(token, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
