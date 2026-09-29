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
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from server.server import configure_server_logging, DEFAULT_MINIMUM_FREE_BYTES, has_storage_capacity, MAX_REQUEST_BODY_BYTES, RequestBodyLengthError, SnapshotJsonValidationError, MaintenanceBusyError, create_server, load_server_settings, main, parse_snapshot_json_request_path, safe_static_path, store_snapshot_json, validate_request_content_length, validate_snapshot_json_body


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
        health = json.loads(response.read())
        self.assertEqual("ok", health["status"])
        self.assertEqual("CodexMobileDashboard/0.1", health["server_version"])
        self.assertIsInstance(health["uptime_seconds"], int)
        self.assertTrue(health["public_available"])
        self.assertFalse(health["staging_available"])
        self.assertTrue(health["logging_available"])
        self.assertIsNone(health["workspace_id"])
        self.assertIsNone(health["current_snapshot_id"])
        self.assertIsNone(health["last_received_at"])

    def test_health_reports_current_snapshot_for_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static"
            public = root / "public"
            static.mkdir()
            workspace = public / "workspace-1"
            workspace.mkdir(parents=True)
            (workspace / "current.json").write_text(
                json.dumps({"snapshot_id": "snapshot-1", "received_at": "2026-08-30T12:00:00+09:00"}),
                encoding="utf-8",
            )
            server = create_server("127.0.0.1", 0, str(static), str(public))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/health?workspace_id=workspace-1")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                health = json.loads(response.read())
                self.assertEqual("workspace-1", health["workspace_id"])
                self.assertEqual("snapshot-1", health["current_snapshot_id"])
                self.assertEqual("2026-08-30T12:00:00+09:00", health["last_received_at"])
                (workspace / "current.json").write_text(
                    json.dumps({"snapshot_id": "legacy-snapshot"}), encoding="utf-8"
                )
                connection.request("GET", "/health?workspace_id=workspace-1")
                legacy_health = json.loads(connection.getresponse().read())
                self.assertEqual("legacy-snapshot", legacy_health["current_snapshot_id"])
                self.assertIsNone(legacy_health["last_received_at"])
                connection.request("GET", "/health?workspace_id=workspace-1&extra=value")
                self.assertEqual(400, connection.getresponse().status)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_workspaces_lists_only_valid_current_public_dashboards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static"
            public = root / "public"
            staging = root / "staging"
            static.mkdir()
            public.mkdir()
            staging.mkdir()

            def write_workspace(
                workspace_id: str,
                snapshot_id: str,
                project_name: str,
                received_at: str,
                *,
                dashboard_workspace_id: str | None = None,
                dashboard_snapshot_id: str | None = None,
            ) -> None:
                workspace = public / workspace_id
                snapshot = workspace / "snapshots" / snapshot_id
                snapshot.mkdir(parents=True)
                (workspace / "current.json").write_text(
                    json.dumps({"snapshot_id": snapshot_id, "received_at": received_at}),
                    encoding="utf-8",
                )
                dashboard = {
                    "schema_version": "1.0",
                    "data_type": "dashboard",
                    "snapshot_id": dashboard_snapshot_id or snapshot_id,
                    "generated_at": "2026-09-29T12:00:00+09:00",
                    "workspace_id": dashboard_workspace_id or workspace_id,
                    "session_id": "00000000-0000-7000-8000-000000000001",
                    "warnings": [],
                    "project": {"name": project_name},
                }
                (snapshot / "dashboard.json").write_text(
                    json.dumps(dashboard, ensure_ascii=False),
                    encoding="utf-8",
                )

            write_workspace(
                "workspace-b", "snapshot-b", "Zulu Project", "2026-09-29T03:00:00+00:00"
            )
            write_workspace(
                "workspace-a", "snapshot-a", "Alpha Project", "2026-09-29T02:00:00+00:00"
            )
            write_workspace(
                "workspace-mismatch", "snapshot-x", "Should Not Appear",
                "2026-09-29T01:00:00+00:00", dashboard_workspace_id="other-workspace",
            )

            broken = public / "workspace-broken"
            broken.mkdir()
            (broken / "current.json").write_text("{broken", encoding="utf-8")

            missing_dashboard = public / "workspace-missing" / "snapshots" / "snapshot-missing"
            missing_dashboard.mkdir(parents=True)
            (public / "workspace-missing" / "current.json").write_text(
                json.dumps({"snapshot_id": "snapshot-missing"}), encoding="utf-8"
            )

            staged_dashboard = staging / "workspace-staged" / "snapshot-staged" / "dashboard.json"
            staged_dashboard.parent.mkdir(parents=True)
            staged_dashboard.write_text("{}", encoding="utf-8")

            server = create_server(
                "127.0.0.1", 0, str(static), str(public), str(staging)
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/workspaces")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                self.assertEqual("application/json; charset=utf-8", response.getheader("Content-Type"))
                etag = response.getheader("ETag")
                self.assertIsNotNone(etag)
                document = json.loads(response.read())
                self.assertEqual(
                    [
                        {
                            "workspace_id": "workspace-a",
                            "project_name": "Alpha Project",
                            "last_received_at": "2026-09-29T02:00:00+00:00",
                        },
                        {
                            "workspace_id": "workspace-b",
                            "project_name": "Zulu Project",
                            "last_received_at": "2026-09-29T03:00:00+00:00",
                        },
                    ],
                    document["workspaces"],
                )
                serialized = json.dumps(document, ensure_ascii=False)
                self.assertNotIn(str(public), serialized)
                self.assertNotIn("workspace-staged", serialized)
                self.assertNotIn("snapshot-a", serialized)

                connection.request("GET", "/workspaces", headers={"If-None-Match": etag})
                not_modified = connection.getresponse()
                self.assertEqual(304, not_modified.status)
                self.assertEqual(etag, not_modified.getheader("ETag"))

                connection.request("GET", "/workspaces?unexpected=1")
                self.assertEqual(400, connection.getresponse().status)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_workspaces_returns_empty_list_when_no_public_workspace_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static"
            public = root / "public"
            static.mkdir()
            public.mkdir()
            server = create_server("127.0.0.1", 0, str(static), str(public))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/workspaces")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                self.assertEqual({"workspaces": []}, json.loads(response.read()))
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

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

    def test_root_with_workspace_query_serves_static_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_bytes(b"<h1>Dashboard</h1>")
            server = create_server("127.0.0.1", 0, str(root))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/?workspace_id=workspace-1")
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                self.assertEqual("text/html; charset=utf-8", response.getheader("Content-Type"))
                self.assertEqual(b"<h1>Dashboard</h1>", response.read())
            finally:
                server.shutdown()
                thread.join()
                server.server_close()

    def test_static_css_javascript_and_vendor_files_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            vendor = root / "vendor"
            vendor.mkdir()
            (root / "styles.css").write_bytes(b"body {}")
            (root / "app.js").write_bytes(b"void 0;")
            (vendor / "library.js").write_bytes(b"void 0;")
            (vendor / "theme.css").write_bytes(b".code { background: #0d1117; }")
            server = create_server("127.0.0.1", 0, str(root))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for path, content_type, body in (
                    ("/styles.css", "text/css; charset=utf-8", b"body {}"),
                    ("/app.js", "text/javascript; charset=utf-8", b"void 0;"),
                    ("/vendor/library.js", "text/javascript; charset=utf-8", b"void 0;"),
                    (
                        "/vendor/theme.css",
                        "text/css; charset=utf-8",
                        b".code { background: #0d1117; }",
                    ),
                ):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                    connection.request("GET", path)
                    response = connection.getresponse()
                    self.assertEqual(200, response.status)
                    self.assertEqual(content_type, response.getheader("Content-Type"))
                    self.assertEqual(body, response.read())
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
            self.assertEqual(Path.home() / ".cache/codex-mobile-dashboard/backup.lock", settings["maintenance_lock_file"])

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
    def test_server_logs_rotate_daily_and_keep_seven_generations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            access, error = configure_server_logging(Path(directory) / "logs")
            try:
                for logger in (access, error):
                    self.assertEqual(1, len(logger.handlers))
                    handler = logger.handlers[0]
                    self.assertEqual("MIDNIGHT", handler.when)
                    self.assertEqual(7, handler.backupCount)
                    self.assertEqual("utf-8", handler.encoding)
            finally:
                for logger in (access, error):
                    for handler in logger.handlers:
                        logger.removeHandler(handler)
                        handler.close()
    def test_access_and_error_logs_exclude_token_and_body(self) -> None:
        token = "test-secret-token"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static = root / "static"
            public = root / "public"
            staging = root / "staging"
            logs = root / "logs"
            static.mkdir()
            public.mkdir()
            staging.mkdir()
            server = create_server("127.0.0.1", 0, str(static), str(public), str(staging), str(logs))
            server.bearer_token = token
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", "/unknown?token=" + token)
                self.assertEqual(404, connection.getresponse().status)
                body = json.dumps(self._valid_snapshot_json()).encode("utf-8")
                headers = {"Authorization": "Bearer " + token, "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174000"}
                with patch("server.server.store_snapshot_json", side_effect=OSError("disk error")):
                    connection.request("POST", "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json", body=body, headers=headers)
                    self.assertEqual(500, connection.getresponse().status)
            finally:
                server.shutdown()
                thread.join()
                server.server_close()
                for logger in (server.access_logger, server.error_logger):
                    for handler in logger.handlers:
                        handler.close()
            access = (logs / "server.access.log").read_text(encoding="utf-8")
            error = (logs / "server.error.log").read_text(encoding="utf-8")
            self.assertIn("http_access method=GET path=/unknown status=404", access)
            self.assertIn("server_error event=snapshot_store_failed", error)
            self.assertNotIn(token, access + error)
            self.assertNotIn('"schema_version"', access + error)
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
    def test_maintenance_lock_rejects_receive_and_publish_without_mutation(self) -> None:
        self.server.bearer_token = "test-secret-token"
        file_delivery = "123e4567-e89b-12d3-a456-426614174000"
        commit_delivery = "123e4567-e89b-12d3-a456-426614174001"
        snapshot_body = json.dumps(self._valid_snapshot_json(), separators=(",", ":")).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            staging = root / "staging"
            public = root / "public"
            staging.mkdir()
            public.mkdir()
            self.server.staging_directory = staging
            self.server.public_directory = public
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            with patch("server.server.storage_write_lock", side_effect=MaintenanceBusyError("busy")):
                connection.request(
                    "POST", "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json",
                    body=snapshot_body,
                    headers={"Authorization": "Bearer test-secret-token", "X-Delivery-Id": file_delivery},
                )
                response = connection.getresponse()
                self.assertEqual(503, response.status)
                self.assertEqual("1", response.getheader("Retry-After"))
                manifest = json.dumps({"files": [{
                    "path": "metadata.json", "byte_size": len(snapshot_body),
                    "sha256": hashlib.sha256(snapshot_body).hexdigest(),
                }]}).encode("utf-8")
                connection.request(
                    "POST", "/api/v1/snapshots/workspace-1/snapshot-1/commit",
                    body=manifest,
                    headers={"Authorization": "Bearer test-secret-token", "X-Delivery-Id": commit_delivery},
                )
                self.assertEqual(503, connection.getresponse().status)
            self.assertFalse((staging / "workspace-1").exists())
            self.assertFalse((public / "workspace-1").exists())

    def test_api_post_rejects_invalid_json_body_over_http(self) -> None:
        self.server.bearer_token = "test-secret-token"
        headers = {
            "Authorization": "Bearer test-secret-token",
            "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174000",
        }
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(
            "POST", "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json",
            body=b'{"broken":', headers=headers,
        )
        self.assertEqual(400, connection.getresponse().status)

    def test_api_commit_returns_conflict_when_staging_does_not_match_manifest(self) -> None:
        self.server.bearer_token = "test-secret-token"
        body = json.dumps({"files": [{"path": "metadata.json", "byte_size": 1, "sha256": "a" * 64}]}).encode("utf-8")
        headers = {
            "Authorization": "Bearer test-secret-token",
            "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174002",
        }
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            self.server.staging_directory = staging
            connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
            connection.request(
                "POST", "/api/v1/snapshots/workspace-1/snapshot-1/commit",
                body=body, headers=headers,
            )
            self.assertEqual(409, connection.getresponse().status)
    def test_api_commit_requires_bearer_token_and_rejects_unsafe_url(self) -> None:
        self.server.bearer_token = "test-secret-token"
        delivery_id = "123e4567-e89b-12d3-a456-426614174001"
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        connection.request(
            "POST", "/api/v1/snapshots/workspace-1/snapshot-1/commit", body=b"{}",
            headers={"Authorization": "Bearer wrong", "X-Delivery-Id": delivery_id},
        )
        self.assertEqual(401, connection.getresponse().status)
        connection.request(
            "POST", "/api/v1/snapshots/workspace%2Fchild/snapshot-1/commit", body=b"{}",
            headers={"Authorization": "Bearer test-secret-token", "X-Delivery-Id": delivery_id},
        )
        self.assertEqual(404, connection.getresponse().status)
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
            current_file = self.server.public_directory / "workspace-1" / "current.json"
            current = json.loads(current_file.read_text(encoding="utf-8"))
            self.assertEqual("snapshot-1", current["snapshot_id"])
            self.assertIsInstance(current["received_at"], str)
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
    def test_storage_capacity_reserves_configured_free_space(self) -> None:
        with patch("server.server.shutil.disk_usage", return_value=SimpleNamespace(free=100)):
            self.assertTrue(has_storage_capacity(Path("."), 90, 10))
            self.assertFalse(has_storage_capacity(Path("."), 90, 11))
        with patch("server.server.shutil.disk_usage", side_effect=OSError("unavailable")):
            self.assertFalse(has_storage_capacity(Path("."), 0))

    def test_api_post_stops_new_storage_when_capacity_is_below_reserve(self) -> None:
        self.server.bearer_token = "test-secret-token"
        body = json.dumps(self._valid_snapshot_json()).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            staging.mkdir()
            self.server.staging_directory = staging
            with patch("server.server.shutil.disk_usage", return_value=SimpleNamespace(free=DEFAULT_MINIMUM_FREE_BYTES + len(body) - 1)):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
                connection.request("POST", "/api/v1/snapshots/workspace-1/snapshot-1/metadata.json", body=body, headers={"Authorization": "Bearer test-secret-token", "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174000"})
                self.assertEqual(507, connection.getresponse().status)
            self.assertFalse((staging / "workspace-1" / "snapshot-1" / "metadata.json").exists())

    def test_api_commit_stops_publication_when_capacity_is_below_reserve(self) -> None:
        self.server.bearer_token = "test-secret-token"
        snapshot_body = json.dumps(self._valid_snapshot_json(), separators=(",", ":")).encode("utf-8")
        body = json.dumps({"files": [{"path": "metadata.json", "byte_size": len(snapshot_body), "sha256": hashlib.sha256(snapshot_body).hexdigest()}]}).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory) / "staging"
            public = Path(directory) / "public"
            staging.mkdir()
            public.mkdir()
            target = staging / "workspace-1" / "snapshot-1" / "metadata.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(snapshot_body)
            self.server.staging_directory = staging
            self.server.public_directory = public
            with patch("server.server.shutil.disk_usage", return_value=SimpleNamespace(free=DEFAULT_MINIMUM_FREE_BYTES + len(snapshot_body) - 1)):
                connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
                connection.request("POST", "/api/v1/snapshots/workspace-1/snapshot-1/commit", body=body, headers={"Authorization": "Bearer test-secret-token", "X-Delivery-Id": "123e4567-e89b-12d3-a456-426614174001"})
                self.assertEqual(507, connection.getresponse().status)
            self.assertFalse((public / "workspace-1" / "current.json").exists())
if __name__ == "__main__":
    unittest.main()
