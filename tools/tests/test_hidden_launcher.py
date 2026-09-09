import os
from pathlib import Path
import runpy
import subprocess
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(os.name == "nt", "Windows console flags required")
class HiddenLauncherTests(unittest.TestCase):
    def test_waits_without_console_and_preserves_exit_code(self):
        entry = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/run_collector_hidden.pyw"))
        args = ["launcher", "--powershell", "powershell.exe", "--python", "python.exe", "--config", "config with spaces.ini"]
        with patch("sys.argv", args), patch("subprocess.run", return_value=Mock(returncode=7)) as run:
            self.assertEqual(7, entry["main"]())
        self.assertEqual(subprocess.CREATE_NO_WINDOW, run.call_args.kwargs["creationflags"])
        self.assertEqual(subprocess.DEVNULL, run.call_args.kwargs["stdin"])
        self.assertIn("config with spaces.ini", run.call_args.args[0])

    def test_start_failure_returns_error(self):
        entry = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/run_collector_hidden.pyw"))
        with patch("sys.argv", ["launcher", "--powershell", "missing.exe", "--python", "python.exe", "--config", "test.ini"]), patch("subprocess.run", side_effect=OSError):
            self.assertEqual(2, entry["main"]())
