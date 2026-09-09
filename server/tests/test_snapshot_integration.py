"""Loopback integration tests. TLS cases require OpenSSL on PATH."""
import hashlib
import http.client
import json
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from server.server import create_server, create_https_server, store_commit_receipt
from tools.https_sender import HttpsSnapshotSender, SnapshotUpload, SenderError, SendErrorKind, new_delivery_id


def snapshot_files(snapshot):
    return {name + ".json": json.dumps({
        "schema_version": "1.0", "data_type": name,
        "workspace_id": "workspace-1", "snapshot_id": snapshot,
        "generated_at": "2026-09-10T00:00:00Z", "session_id": "00000000-0000-7000-8000-000000000001",
        "warnings": [], "marker": snapshot,
    }).encode() for name in ("dashboard", "metadata")}


class SnapshotServerFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("static", "public", "staging"):
            (self.root / name).mkdir()

    def start_server(self, tls=False):
        options = dict(host="127.0.0.1", port=0,
                       static_directory=str(self.root / "static"),
                       public_directory=str(self.root / "public"),
                       staging_directory=str(self.root / "staging"), minimum_free_bytes=0)
        if tls:
            self.server = create_https_server(str(self.root / "server.crt"), str(self.root / "server.key"), **options)
        else:
            self.server = create_server(**options)
        self.server.bearer_token = "integration-test-token"
        thread = threading.Thread(target=self.server.serve_forever)
        thread.start()
        def stop():
            self.server.shutdown()
            thread.join(timeout=10)
            self.server.server_close()
        self.addCleanup(stop)

    def request(self, method, path, body=None, delivery=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            headers = {"Authorization": "Bearer integration-test-token"}
            if delivery:
                headers["X-Delivery-Id"] = delivery
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            body = response.read()
            return response.status, json.loads(body) if body else None
        finally:
            connection.close()

    def upload(self, snapshot):
        files = snapshot_files(snapshot)
        for name, body in files.items():
            status, result = self.request("POST", f"/api/v1/snapshots/workspace-1/{snapshot}/{name}", body, new_delivery_id())
            self.assertEqual(200, status)
            self.assertEqual("stored", result["status"])
        return json.dumps({"files": [{"path": name, "byte_size": len(body),
                           "sha256": hashlib.sha256(body).hexdigest()} for name, body in files.items()]}).encode()


class SnapshotIntegrationTests(SnapshotServerFixture):
    def test_parallel_snapshots_publish_complete_files(self):
        self.start_server()
        barrier = threading.Barrier(2)
        def update(snapshot):
            barrier.wait(timeout=10)
            manifest = self.upload(snapshot)
            barrier.wait(timeout=10)
            return self.request("POST", f"/api/v1/snapshots/workspace-1/{snapshot}/commit", manifest, new_delivery_id())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ("snapshot-a", "snapshot-b")))
        for status, result in results:
            self.assertEqual(200, status)
            self.assertEqual("committed", result["status"])
        workspace = self.root / "public" / "workspace-1"
        for snapshot in ("snapshot-a", "snapshot-b"):
            for name, body in snapshot_files(snapshot).items():
                self.assertEqual(body, (workspace / "snapshots" / snapshot / name).read_bytes())
        current = json.loads((workspace / "current.json").read_text())["snapshot_id"]
        self.assertIn(current, ("snapshot-a", "snapshot-b"))
        for name in snapshot_files(current):
            status, document = self.request("GET", f"/data/workspace-1/{name}")
            self.assertEqual(200, status)
            self.assertEqual(current, document["snapshot_id"])
            self.assertEqual(current, document["marker"])

    def test_read_during_publication_keeps_complete_previous_snapshot(self):
        self.start_server()
        for snapshot in ("snapshot-old", "snapshot-new"):
            manifest = self.upload(snapshot)
            if snapshot == "snapshot-old":
                self.assertEqual(200, self.request("POST", f"/api/v1/snapshots/workspace-1/{snapshot}/commit", manifest, new_delivery_id())[0])
        copying = threading.Event()
        resume = threading.Event()
        copyfile = shutil.copyfile
        def paused_copy(source, target):
            result = copyfile(source, target)
            copying.set()
            if not resume.wait(timeout=10):
                raise TimeoutError("publication was not resumed")
            return result
        with patch("server.server.shutil.copyfile", side_effect=paused_copy):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.request, "POST", "/api/v1/snapshots/workspace-1/snapshot-new/commit", manifest, new_delivery_id())
                try:
                    self.assertTrue(copying.wait(timeout=10))
                    for name in snapshot_files("snapshot-old"):
                        status, document = self.request("GET", f"/data/workspace-1/{name}")
                        self.assertEqual(200, status)
                        self.assertEqual("snapshot-old", document["snapshot_id"])
                finally:
                    resume.set()
                self.assertEqual(200, future.result(timeout=10)[0])
        for name in snapshot_files("snapshot-new"):
            status, document = self.request("GET", f"/data/workspace-1/{name}")
            self.assertEqual(200, status)
            self.assertEqual("snapshot-new", document["snapshot_id"])

    def test_parallel_duplicate_commit_stores_one_receipt(self):
        self.start_server()
        manifest = self.upload("snapshot-1")
        delivery = new_delivery_id()
        barrier = threading.Barrier(2)
        def commit(_):
            barrier.wait(timeout=10)
            return self.request("POST", "/api/v1/snapshots/workspace-1/snapshot-1/commit", manifest, delivery)
        with patch("server.server.store_commit_receipt", wraps=store_commit_receipt) as receipt:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(commit, range(2)))
            self.assertEqual(1, receipt.call_count)
        self.assertEqual(results[0], results[1])
        self.assertEqual(200, results[0][0])
        self.assertEqual("committed", results[0][1]["status"])


class TlsIntegrationTests(SnapshotServerFixture):
    def setUp(self):
        super().setUp()
        openssl = shutil.which("openssl")
        if not openssl:
            self.skipTest("OpenSSL is required for ephemeral TLS certificates")
        def run(*args):
            subprocess.run([openssl, *args], cwd=self.root, check=True, capture_output=True,
                           timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for ca in ("ca", "wrong-ca"):
            run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                "-subj", f"/CN=Test-{ca}", "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-keyout", ca + ".key", "-out", ca + ".crt")
        run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
            "-keyout", "server.key", "-out", "server.csr")
        (self.root / "extensions.cnf").write_text(
            "subjectAltName=DNS:localhost\nbasicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
        run("x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key",
            "-CAcreateserial", "-days", "2", "-extfile", "extensions.cnf", "-out", "server.crt")
        self.start_server(tls=True)

    def send(self, host, ca):
        sender = HttpsSnapshotSender(f"https://{host}:{self.server.server_port}",
                                    "integration-test-token", self.root / ca, timeout_seconds=5)
        return sender.send_snapshot("workspace-1", "snapshot-1", [
            SnapshotUpload(name, body, new_delivery_id()) for name, body in snapshot_files("snapshot-1").items()], commit_delivery_id=new_delivery_id())

    def test_correct_ca_uploads_and_commits(self):
        result = self.send("localhost", "ca.crt")
        self.assertEqual("snapshot-1", result.snapshot_id)
        current = self.root / "public" / "workspace-1" / "current.json"
        self.assertEqual("snapshot-1", json.loads(current.read_text())["snapshot_id"])

    def assert_tls_rejected(self, host, ca, hostname=False):
        with self.assertRaises(SenderError) as raised:
            self.send(host, ca)
        self.assertEqual(SendErrorKind.CERTIFICATE_VERIFY, raised.exception.kind)
        self.assertFalse(raised.exception.retryable)
        cause = raised.exception.__cause__
        self.assertIsInstance(cause, ssl.SSLCertVerificationError)
        if hostname:
            self.assertIn("mismatch", cause.verify_message.lower())
        self.assertFalse((self.root / "public" / "workspace-1").exists())
        self.assertFalse((self.root / "staging" / "workspace-1").exists())

    def test_wrong_ca_rejects_before_upload(self):
        self.assert_tls_rejected("localhost", "wrong-ca.crt")

    def test_hostname_mismatch_rejects_before_upload(self):
        self.assert_tls_rejected("127.0.0.1", "ca.crt", hostname=True)
