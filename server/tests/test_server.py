from __future__ import annotations

import hashlib
import http.client
import hmac
import io
import json
import os
import threading
import tempfile
from contextlib import redirect_stderr
from email.message import Message
from pathlib import Path
import unittest
from unittest.mock import patch

from server.server import MAX_REQUEST_BODY_BYTES, RequestBodyLengthError, SnapshotJsonValidationError, create_server, load_server_settings, main, parse_snapshot_json_request_path, safe_static_path, store_snapshot_json, validate_request_content_length, validate_snapshot_json_body


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def test_health(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", "/health")
        response = connection.getresponse()
        self.assertEqual(200, response.status)
        self.assertEqual(b'{"status":"ok"}\n', response.read())

    def test_unknown_path_is_not_found(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("GET", "/unknown")
        self.assertEqual(404, connection.getresponse().status)


    def test_static_path_rejects_unsafe_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(root / "app.css", safe_static_path(root, "/app.css"))
            for value in ("../secret", "/../secret", "C:/secret", "app\\secret", "%2e%2e/secret"):
                with self.assertRaises(ValueError):
                    safe_static_path(root, value)

    def test_directories_and_missing_files_are_not_listed(self) -> None:
        for path in ("/assets/", "/missing.css"):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            connection.request("GET", path)
            self.assertEqual(404, connection.getresponse().status)

    def test_root_serves_static_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_bytes(b"<h1>Dashboard</h1>")
            server = create_server("127.0.0.1", 0, str(root))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                self.assertEqual("text/html; charset=utf-8", response.getheader("Content-Type"))
                self.assertEqual(b"<h1>Dashboard</h1>", response.read())
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_json_get_reads_only_public_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static"; public = root / "public"; (public / "workspace-1").mkdir(parents=True); static.mkdir()
            (public / "workspace-1" / "snapshots" / "snapshot-1").mkdir(parents=True)
            (public / "workspace-1" / "snapshots" / "snapshot-1" / "dashboard.json").write_bytes(b"{}")
            (public / "workspace-1" / "current.json").write_text("{\"snapshot_id\":\"snapshot-1\"}", encoding="utf-8")
            server = create_server("127.0.0.1", 0, str(static), str(public))
            thread = threading.Thread(target=server.serve_forever); thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port); connection.request("GET", "/data/workspace-1/dashboard.json")
                response = connection.getresponse(); self.assertEqual(200, response.status); self.assertEqual("application/json; charset=utf-8", response.getheader("Content-Type")); self.assertEqual(b"{}", response.read())
            finally:
                server.shutdown(); thread.join(); server.server_close()

    def test_json_get_does_not_expose_uncommitted_staging_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static"
            public = root / "public"
            staging = root / "staging"
            static.mkdir()
            public.mkdir()
            staged = staging / "workspace-1" / "snapshot-1" / "dashboard.json"
            staged.parent.mkdir(parents=True)
            staged.write_bytes(b'{"uncommitted":true}')
            server = create_server("127.0.0.1", 0, str(static), str(public), str(staging))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/data/workspace-1/dashboard.json")
                self.assertEqual(404, connection.getresponse().status)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
    def test_load_server_settings_reads_external_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_file = root / "server.token"
            token_file.write_text("test-token-value\n", encoding="utf-8")
            config_file = root / "server.ini"
            config_file.write_text(
                "[server]\nhost = 127.0.0.1\nport = 8765\n"
                "certificate_file = ~/tls/server.crt\nprivate_key_file = ~/tls/server.key\n"
                "static_directory = .\npublic_directory = .\nstaging_directory = .\n\n"
                "[auth]\ntoken_file = " + str(token_file) + "\n",
                encoding="utf-8",
            )
            settings = load_server_settings(str(config_file))
            self.assertEqual("127.0.0.1", settings["host"])
            self.assertEqual(8765, settings["port"])
            self.assertEqual("test-token-value", settings["token"])
            self.assertEqual(token_file, settings["token_file"])

    def test_load_server_settings_rejects_empty_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_file = root / "server.token"
            token_file.write_text("\n", encoding="utf-8")
            config_file = root / "server.ini"
            config_file.write_text(
                "[server]\nhost = 127.0.0.1\nport = 8765\n"
                "certificate_file = server.crt\nprivate_key_file = server.key\n"
                "static_directory = .\npublic_directory = .\nstaging_directory = .\n\n"
                "[auth]\ntoken_file = " + str(token_file) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "^token_file_invalid$"):
                load_server_settings(str(config_file))
    def test_token_is_not_logged_or_returned_in_http_error(self) -> None:
        token = "test-secret-token"
        captured = io.StringIO()
        with redirect_stderr(captured):
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            connection.request(
                "GET", "/unknown?token=" + token,
                headers={"Authorization": "Bearer " + token},
            )
            response = connection.getresponse()
            body = response.read().decode("utf-8")
        self.assertEqual(404, response.status)
        self.assertNotIn(token, body)
        self.assertNotIn(token, captured.getvalue())

    def test_invalid_config_does_not_include_token_in_stderr(self) -> None:
        token = "test-secret-token"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_file = root / "server.token"
            token_file.write_text(token + "\n", encoding="utf-8")
            config_file = root / "server.ini"
            config_file.write_text(
                "[server]\nhost = 127.0.0.1\nport = invalid\n"
                "certificate_file = server.crt\nprivate_key_file = server.key\n"
                "static_directory = .\npublic_directory = .\nstaging_directory = .\n\n"
                "[auth]\ntoken_file = " + str(token_file) + "\n",
                encoding="utf-8",
            )
            captured = io.StringIO()
            with redirect_stderr(captured), self.assertRaises(SystemExit) as raised:
                main(["--config", str(config_file)])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("configuration_invalid", captured.getvalue())
        self.assertNotIn(token, captured.getvalue())
    def test_api_post_requires_bearer_token(self) -> None:
        token = "test-secret-token"
        self.server.bearer_token = token
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request("POST", "/api/v1/snapshots/example", body=b"", headers={"Authorization": "Bearer wrong"})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        self.assertEqual(401, response.status)
        self.assertEqual("Bearer", response.getheader("WWW-Authenticate"))
        self.assertNotIn(token, body)

    def test_api_post_uses_constant_time_token_comparison(self) -> None:
        token = "test-secret-token"
        self.server.bearer_token = token
        with patch("server.server.hmac.compare_digest", wraps=hmac.compare_digest) as compare:
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            connection.request("POST", "/api/v1/snapshots/example", body=b"", headers={"Authorization": "Bearer " + token, "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174000"})
            response = connection.getresponse()
            response.read()
        self.assertEqual(404, response.status)
        compare.assert_called_once_with(token, token)
    def test_snapshot_json_request_path_accepts_safe_components(self) -> None:
        self.assertEqual(
            ("workspace-1", "snapshot-20260829", "messages/chunks/page-0001.json"),
            parse_snapshot_json_request_path(
                "/api/v1/snapshots/workspace-1/snapshot-20260829/messages/chunks/page-0001.json"
            ),
        )

    def test_snapshot_json_request_path_rejects_unsafe_values(self) -> None:
        unsafe_paths = (
            "/api/v1/snapshots//snapshot/metadata.json",
            "/api/v1/snapshots/workspace%2Fchild/snapshot/metadata.json",
            "/api/v1/snapshots/workspace/%2e%2e/metadata.json",
            "/api/v1/snapshots/workspace/snapshot/../metadata.json",
            "/api/v1/snapshots/workspace/snapshot/folder%5Cmetadata.json",
            "/api/v1/snapshots/workspace/snapshot//metadata.json",
            "/api/v1/snapshots/workspace/snapshot/metadata.txt",
            "/api/v1/snapshots/workspace/snapshot/metadata.json?unexpected=1",
        )
        for request_path in unsafe_paths:
            with self.assertRaisesRegex(ValueError, "^snapshot_request_path_invalid$"):
                parse_snapshot_json_request_path(request_path)

    def test_api_post_rejects_invalid_snapshot_path_after_authentication(self) -> None:
        self.server.bearer_token = "test-secret-token"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(
            "POST", "/api/v1/snapshots/workspace/snapshot/%2e%2e/metadata.json", body=b"",
            headers={"Authorization": "Bearer test-secret-token", "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174000"},
        )
        self.assertEqual(404, connection.getresponse().status)
    def test_request_body_length_accepts_one_mebibyte(self) -> None:
        headers = Message()
        headers["Content-Length"] = str(MAX_REQUEST_BODY_BYTES)
        self.assertEqual(MAX_REQUEST_BODY_BYTES, validate_request_content_length(headers))

    def test_request_body_length_rejects_unsafe_headers(self) -> None:
        cases = (({}, 411), ({"Content-Length": "invalid"}, 400), ({"Transfer-Encoding": "chunked"}, 400), ({"Content-Length": str(MAX_REQUEST_BODY_BYTES + 1)}, 413))
        for values, expected_status in cases:
            headers = Message()
            for name, value in values.items():
                headers[name] = value
            with self.assertRaises(RequestBodyLengthError) as raised:
                validate_request_content_length(headers)
            self.assertEqual(expected_status, raised.exception.status)
        duplicate_headers = Message()
        duplicate_headers["Content-Length"] = "1"
        duplicate_headers["Content-Length"] = "1"
        with self.assertRaises(RequestBodyLengthError) as raised:
            validate_request_content_length(duplicate_headers)
        self.assertEqual(400, raised.exception.status)

    def test_api_post_rejects_body_larger_than_one_mebibyte_before_authentication(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(
            "POST", "/api/v1/snapshots/workspace/snapshot/metadata.json",
            body=b"", headers={"Content-Length": str(MAX_REQUEST_BODY_BYTES + 1)},
        )
        response = connection.getresponse()
        self.assertEqual(413, response.status)
        self.assertEqual(b"", response.read())
    def _valid_snapshot_json(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "data_type": "dashboard",
            "snapshot_id": "snapshot-1",
            "generated_at": "2026-08-29T12:00:00+09:00",
            "workspace_id": "workspace-1",
            "session_id": "00000000-0000-7000-8000-000000000001",
            "warnings": [],
        }

    def test_snapshot_json_body_accepts_utf8_common_schema_and_identifiers(self) -> None:
        document = self._valid_snapshot_json()
        document["title"] = "日本語"
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.assertEqual(document, validate_snapshot_json_body(body, "workspace-1", "snapshot-1"))

    def test_snapshot_json_body_rejects_invalid_or_mismatched_content(self) -> None:
        document = self._valid_snapshot_json()
        cases = (
            b"\xff",
            b"{}",
            b'{"schema_version":"1.0","schema_version":"1.0"}',
            json.dumps({**document, "schema_version": "2.0"}).encode("utf-8"),
            json.dumps({**document, "workspace_id": "other-workspace"}).encode("utf-8"),
            json.dumps({**document, "snapshot_id": "other-snapshot"}).encode("utf-8"),
        )
        for body in cases:
            with self.assertRaises(SnapshotJsonValidationError):
                validate_snapshot_json_body(body, "workspace-1", "snapshot-1")

    def test_api_post_rejects_snapshot_identifier_mismatch(self) -> None:
        self.server.bearer_token = "test-secret-token"
        body = json.dumps(self._valid_snapshot_json()).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(
            "POST", "/api/v1/snapshots/workspace-1/other-snapshot/metadata.json",
            body=body, headers={"Authorization": "Bearer test-secret-token", "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174000"},
        )
        self.assertEqual(400, connection.getresponse().status)

    def test_api_post_stores_and_returns_delivery_response(self) -> None:
        self.server.bearer_token = "test-secret-token"
        delivery_id = "123e4567-e89b-12d3-a456-426614174000"
        body = json.dumps(self._valid_snapshot_json()).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            self.server.staging_directory = staging
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            connection.request(
                "POST", "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json",
                body=body,
                headers={
                    "Authorization": "Bearer test-secret-token",
                    "X-Delivery-Id": delivery_id,
                },
            )
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            self.assertEqual("application/json; charset=utf-8", response.getheader("Content-Type"))
            self.assertEqual(
                {"status": "stored", "delivery_id": delivery_id, "snapshot_id": "snapshot-1"},
                json.loads(response.read().decode("utf-8")),
            )
            self.assertEqual(
                body,
                (staging / "workspace-1" / "snapshot-1" / "metadata.json").read_bytes(),
            )

    def test_api_post_reuses_persisted_delivery_receipt_without_storing_again(self) -> None:
        self.server.bearer_token = "test-secret-token"
        delivery_id = "123e4567-e89b-12d3-a456-426614174000"
        body = json.dumps(self._valid_snapshot_json()).encode("utf-8")
        headers = {"Authorization": "Bearer test-secret-token", "X-Delivery-Id": delivery_id}
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            self.server.staging_directory = staging
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            url = "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json"
            connection.request("POST", url, body=body, headers=headers)
            self.assertEqual(200, connection.getresponse().status)
            with patch("server.server.store_snapshot_json") as store:
                connection.request("POST", url, body=body, headers=headers)
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                self.assertEqual(delivery_id, json.loads(response.read())["delivery_id"])
            store.assert_not_called()
            self.assertTrue((staging / ".deliveries" / (delivery_id + ".json")).is_file())

    def test_api_post_rejects_reused_delivery_id_with_different_body(self) -> None:
        self.server.bearer_token = "test-secret-token"
        delivery_id = "123e4567-e89b-12d3-a456-426614174000"
        headers = {"Authorization": "Bearer test-secret-token", "X-Delivery-Id": delivery_id}
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            self.server.staging_directory = staging
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            url = "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json"
            connection.request("POST", url, body=json.dumps(self._valid_snapshot_json()).encode("utf-8"), headers=headers)
            self.assertEqual(200, connection.getresponse().status)
            changed = self._valid_snapshot_json()
            changed["warnings"] = ["changed"]
            connection.request("POST", url, body=json.dumps(changed).encode("utf-8"), headers=headers)
            self.assertEqual(409, connection.getresponse().status)
    def test_api_commit_returns_committed_and_reuses_receipt(self) -> None:
        self.server.bearer_token = "test-secret-token"
        delivery_id = "123e4567-e89b-12d3-a456-426614174001"
        snapshot_body = json.dumps(self._valid_snapshot_json(), separators=(",", ":")).encode("utf-8")
        body = json.dumps({"files": [{"path": "metadata.json", "byte_size": len(snapshot_body), "sha256": hashlib.sha256(snapshot_body).hexdigest()}]}, separators=(",", ":")).encode("utf-8")
        headers = {"Authorization": "Bearer test-secret-token", "X-Delivery-Id": delivery_id}
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            public = Path(directory) / "public"
            public.mkdir()
            self.server.public_directory = public
            target = staging / "workspace-1" / "snapshot-1" / "metadata.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(snapshot_body)
            self.server.staging_directory = staging
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            url = "/api/v1/snapshots/workspace-1/snapshot-1/commit"
            connection.request("POST", url, body=body, headers=headers)
            response = connection.getresponse()
            self.assertEqual(200, response.status)
            self.assertEqual(
                {"status": "committed", "delivery_id": delivery_id, "snapshot_id": "snapshot-1"},
                json.loads(response.read()),
            )
            self.assertTrue((self.server.public_directory / "workspace-1" / "current.json").is_file())
            with patch("server.server.store_commit_receipt") as store:
                connection.request("POST", url, body=body, headers=headers)
                self.assertEqual(200, connection.getresponse().status)
            store.assert_not_called()
            self.assertTrue((staging / ".commits" / (delivery_id + ".json")).is_file())
    def test_api_commit_rejects_invalid_manifest_and_reused_id(self) -> None:
        self.server.bearer_token = "test-secret-token"
        delivery_id = "123e4567-e89b-12d3-a456-426614174001"
        headers = {"Authorization": "Bearer test-secret-token", "X-Delivery-Id": delivery_id}
        snapshot_body = json.dumps(self._valid_snapshot_json(), separators=(",", ":")).encode("utf-8")
        valid = json.dumps({"files": [{"path": "metadata.json", "byte_size": len(snapshot_body), "sha256": hashlib.sha256(snapshot_body).hexdigest()}]}).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            public = Path(directory) / "public"
            public.mkdir()
            self.server.public_directory = public
            target = staging / "workspace-1" / "snapshot-1" / "metadata.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(snapshot_body)
            self.server.staging_directory = staging
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            url = "/api/v1/snapshots/workspace-1/snapshot-1/commit"
            connection.request("POST", url, body=b'{"files":[]}', headers=headers)
            self.assertEqual(400, connection.getresponse().status)
            connection.request("POST", url, body=valid, headers=headers)
            self.assertEqual(200, connection.getresponse().status)
            changed = json.dumps({"files": [{"path": "metadata.json", "byte_size": 2, "sha256": "b" * 64}]}).encode("utf-8")
            connection.request("POST", url, body=changed, headers=headers)
            self.assertEqual(409, connection.getresponse().status)
    def test_api_post_rejects_missing_delivery_id(self) -> None:
        self.server.bearer_token = "test-secret-token"
        body = json.dumps(self._valid_snapshot_json()).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(
            "POST", "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json",
            body=body, headers={"Authorization": "Bearer test-secret-token"},
        )
        self.assertEqual(400, connection.getresponse().status)

    def test_api_post_returns_503_without_staging_configuration(self) -> None:
        self.server.bearer_token = "test-secret-token"
        body = json.dumps(self._valid_snapshot_json()).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(
            "POST", "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json",
            body=body, headers={"Authorization": "Bearer test-secret-token", "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174000"},
        )
        self.assertEqual(503, connection.getresponse().status)

    def test_store_snapshot_json_uses_staging_and_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            with patch("server.server.os.replace", wraps=os.replace) as replace:
                target = store_snapshot_json(
                    str(staging), "workspace-1", "snapshot-1",
                    "messages/page-0001.json", b'{"version":1}',
                )
            self.assertEqual(
                staging / "workspace-1" / "snapshot-1" / "messages" / "page-0001.json",
                target,
            )
            self.assertEqual(b'{"version":1}', target.read_bytes())
            replace.assert_called_once()
            store_snapshot_json(
                str(staging), "workspace-1", "snapshot-1",
                "messages/page-0001.json", b'{"version":2}',
            )
            self.assertEqual(b'{"version":2}', target.read_bytes())
            self.assertEqual([], list(target.parent.glob("*.tmp")))

    def test_store_snapshot_json_rejects_path_outside_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            with self.assertRaises(ValueError):
                store_snapshot_json(
                    str(staging), "workspace-1", "snapshot-1",
                    "../outside.json", b"{}",
                )
            self.assertEqual([], list(staging.iterdir()))
    def test_create_server_rejects_staging_that_overlaps_public(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static"
            public = root / "public"
            static.mkdir()
            public.mkdir()
            with self.assertRaisesRegex(ValueError, "^staging_directory_overlaps_public$"):
                create_server("127.0.0.1", 0, str(static), str(public), str(public))
if __name__ == "__main__":
    unittest.main()
