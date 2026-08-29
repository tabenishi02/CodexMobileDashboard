"""Minimal Python 3.10-compatible server foundation for Termux."""

from __future__ import annotations

import argparse
import json
import ssl
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Sequence, Type


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Expose only a non-sensitive health endpoint until API tasks are added."""

    server_version = "CodexMobileDashboard/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._serve_index()
            return
        if self.path != "/health":
            self.send_error(404)
            return
        body = b'{"status":"ok"}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_index(self) -> None:
        target = self.server.static_directory / "index.html"
        if not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Avoid logging request bodies; detailed logging is added later."""
        return


def create_server(host: str = "0.0.0.0", port: int = 8765, static_directory: str = ".") -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("port_invalid")
    root = Path(static_directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("static_directory_invalid")
    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    server.static_directory = root
    return server

def create_https_server(certificate_file: str, private_key_file: str, host: str = "0.0.0.0", port: int = 8765) -> ThreadingHTTPServer:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate_file, private_key_file)
    server = create_server(host, port)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Codex Mobile Dashboard server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--static-dir", default=".")
    arguments = parser.parse_args(argv)
    if bool(arguments.cert) != bool(arguments.key):
        parser.error("--cert and --key must be specified together")
    server = create_https_server(arguments.cert, arguments.key, arguments.host, arguments.port) if arguments.cert else create_server(arguments.host, arguments.port, arguments.static_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
