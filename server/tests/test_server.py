from __future__ import annotations

import http.client
import hmac
import io
import threading
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
import unittest
from unittest.mock import patch

from server.server import create_server, load_server_settings, main, safe_static_path


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
            (public / "workspace-1" / "dashboard.json").write_bytes(b"{}")
            server = create_server("127.0.0.1", 0, str(static), str(public))
            thread = threading.Thread(target=server.serve_forever); thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port); connection.request("GET", "/data/workspace-1/dashboard.json")
                response = connection.getresponse(); self.assertEqual(200, response.status); self.assertEqual("application/json; charset=utf-8", response.getheader("Content-Type")); self.assertEqual(b"{}", response.read())
            finally:
                server.shutdown(); thread.join(); server.server_close()

    def test_load_server_settings_reads_external_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            token_file = root / "server.token"
            token_file.write_text("test-token-value\n", encoding="utf-8")
            config_file = root / "server.ini"
            config_file.write_text(
                "[server]\nhost = 127.0.0.1\nport = 8765\n"
                "certificate_file = ~/tls/server.crt\nprivate_key_file = ~/tls/server.key\n"
                "static_directory = .\npublic_directory = .\n\n"
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
                "static_directory = .\npublic_directory = .\n\n"
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
                "static_directory = .\npublic_directory = .\n\n"
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
        connection.request("POST", "/api/v1/snapshots/example", headers={"Authorization": "Bearer wrong"})
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
            connection.request("POST", "/api/v1/snapshots/example", headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            response.read()
        self.assertEqual(404, response.status)
        compare.assert_called_once_with(token, token)
if __name__ == "__main__":
    unittest.main()
