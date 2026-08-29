"""Minimal Python 3.10-compatible server foundation for Termux."""

from __future__ import annotations

import argparse
import json
import ssl
import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional, Sequence, Type


class DashboardRequestHandler(BaseHTTPRequestHandler):
    """Expose only a non-sensitive health endpoint until API tasks are added."""

    server_version = "CodexMobileDashboard/0.1"

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/data/"):
            self._serve_json()
            return
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

    def _serve_json(self) -> None:
        parts = self.path.split("?", 1)[0].split("/")[2:]
        if len(parts) < 2 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", parts[0]):
            self.send_error(404)
            return
        try:
            target = safe_static_path(self.server.public_directory / parts[0], "/".join(parts[1:]))
        except ValueError:
            self.send_error(404)
            return
        if target.suffix != ".json" or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
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
        self.send_header("Content-Type", content_type_for(target))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Avoid logging request bodies; detailed logging is added later."""
        return


def safe_static_path(root: Path, request_path: str) -> Path:
    decoded = unquote(request_path)
    if not decoded or "\\" in decoded or ":" in decoded:
        raise ValueError("static_path_invalid")
    path = PurePosixPath(decoded.lstrip("/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("static_path_invalid")
    target = root.joinpath(*path.parts).resolve(strict=False)
    try:
        target.relative_to(root.resolve(strict=True))
    except ValueError as error:
        raise ValueError("static_path_invalid") from error
    return target

def content_type_for(path: Path) -> str:
    return {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8"}.get(path.suffix.lower(), "application/octet-stream")

def create_server(host: str = "0.0.0.0", port: int = 8765, static_directory: str = ".", public_directory: str = ".") -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise ValueError("port_invalid")
    root = Path(static_directory).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("static_directory_invalid")
    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    server.static_directory = root
    public_root = Path(public_directory).resolve(strict=True)
    if not public_root.is_dir():
        raise ValueError("public_directory_invalid")
    server.public_directory = public_root
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
    parser.add_argument("--public-dir", default=".")
    arguments = parser.parse_args(argv)
    if bool(arguments.cert) != bool(arguments.key):
        parser.error("--cert and --key must be specified together")
    server = create_https_server(arguments.cert, arguments.key, arguments.host, arguments.port) if arguments.cert else create_server(arguments.host, arguments.port, arguments.static_dir, arguments.public_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
