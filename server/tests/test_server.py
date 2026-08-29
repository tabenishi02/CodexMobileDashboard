from __future__ import annotations

import http.client
import threading
import tempfile
from pathlib import Path
import unittest

from server.server import create_server, safe_static_path


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

if __name__ == "__main__":
    unittest.main()
