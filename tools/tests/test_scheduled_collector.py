import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"), "Windows PowerShell required")
class ScheduledCollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="collector runner ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(__file__).resolve().parents[2]
        self.fake = Path(self.directory.name) / "python.cmd"
        self.fake.write_text('@echo off\necho invoked %*\nif "%5"=="retry-queued" exit /b 0\nexit /b 7\n', encoding="ascii")
        self.config = Path(self.directory.name) / "collector config.ini"
        self.mutex_name = r"Local\CodexMobileDashboard-Test-" + str(os.getpid()) + "-" + Path(self.directory.name).name.replace(" ", "_")
        self.env = dict(os.environ, PATH=self.directory.name + os.pathsep + os.environ["PATH"])

    def run_script(self, name):
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(self.root / "scripts" / name), "-ConfigPath", str(self.config),
             "-MutexName", self.mutex_name],
            cwd=self.directory.name, env=self.env, capture_output=True, text=True, timeout=20,
        )

    def test_runner_passes_config_incremental_mode_and_failure_exit_code(self):
        result = self.run_script("run_collector.ps1")
        self.assertEqual(7, result.returncode, result.stderr)
        self.assertIn(str(self.config), result.stdout)
        self.assertIn("-m tools.collector", result.stdout)
        self.assertIn("collect-once --incremental", result.stdout)
        self.assertNotIn("backfill-ai", result.stdout)
        again = self.run_script("run_collector.ps1")
        self.assertEqual(7, again.returncode, again.stderr)

    def test_retry_failure_stops_collection_and_next_run_recovers(self):
        self.fake.write_text("@echo off\necho invoked %*\nexit /b 2\n", encoding="ascii")
        failed = self.run_script("run_collector.ps1")
        self.assertEqual(2, failed.returncode, failed.stderr)
        self.assertIn("retry-queued --all", failed.stdout)
        self.assertNotIn("collect-once", failed.stdout)
        self.fake.write_text("@echo off\necho invoked %*\nexit /b 0\n", encoding="ascii")
        recovered = self.run_script("run_collector.ps1")
        self.assertEqual(0, recovered.returncode, recovered.stderr)
        self.assertLess(recovered.stdout.index("retry-queued"), recovered.stdout.index("collect-once"))
        self.assertEqual(0, self.run_script("run_pending_queue_retry.ps1").returncode)

    def test_collector_and_retry_skip_while_shared_mutex_is_held(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateMutexW(None, True, self.mutex_name)
        self.assertTrue(handle)
        try:
            for name in ("run_collector.ps1", "run_pending_queue_retry.ps1"):
                with self.subTest(name=name):
                    result = self.run_script(name)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertNotIn("invoked", result.stdout)
        finally:
            kernel.ReleaseMutex(handle)
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    unittest.main()
