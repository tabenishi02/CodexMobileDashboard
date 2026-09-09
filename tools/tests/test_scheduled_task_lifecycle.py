import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid


@unittest.skipUnless(os.name == "nt" and os.environ.get("CODEX_DASHBOARD_TASK_TEST") == "1",
                     "Set CODEX_DASHBOARD_TASK_TEST=1 for isolated Windows task integration")
class ScheduledTaskLifecycleTests(unittest.TestCase):
    def test_install_status_run_and_uninstall(self):
        root = Path(__file__).resolve().parents[2]
        name = "CodexMobileDashboard-Test-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="dashboard task ") as directory:
            scripts = Path(directory) / "scripts"
            scripts.mkdir()
            for script in ("install_collector_task.ps1", "uninstall_collector_task.ps1", "get_collector_task_status.ps1"):
                shutil.copyfile(root / "scripts" / script, scripts / script)
            marker = Path(directory) / "ran.txt"
            (scripts / "run_collector.ps1").write_text(
                "param([string]$ConfigPath, [string]$PythonPath)\n"
                "[System.IO.File]::WriteAllText((Join-Path (Split-Path $PSScriptRoot -Parent) 'ran.txt'), 'ok')\nexit 0\n",
                encoding="utf-8",
            )
            config = Path(directory) / "collector.ini"
            config.write_text("[test]\n", encoding="ascii")

            def ps(command):
                result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                         "-Command", command], capture_output=True, text=True, timeout=30)
                self.assertEqual(0, result.returncode, result.stderr)
                return result.stdout

            def invoke(script, extra=""):
                path = str(scripts / script).replace("'", "''")
                return ps("& '" + path + "' -TaskName '" + name + "' " + extra)

            def status():
                return json.loads(invoke("get_collector_task_status.ps1", "| ConvertTo-Json -Compress"))

            self.assertFalse(status()["Registered"])
            try:
                args = "-ConfigPath '" + str(config).replace("'", "''") + "'"
                invoke("install_collector_task.ps1", args + " -Preview")
                self.assertFalse(status()["Registered"])
                invoke("install_collector_task.ps1", args)
                registered = status()
                self.assertTrue(registered["Enabled"])
                self.assertEqual("PT1M", registered["Interval"])
                self.assertEqual("Interactive", registered["LogonType"])
                self.assertTrue(registered["StartWhenAvailable"])
                self.assertEqual("IgnoreNew", registered["MultipleInstances"])
                ps("Start-ScheduledTask -TaskName '" + name + "'")
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    current = status()
                    if marker.exists() and current["State"] != "Running":
                        break
                    time.sleep(0.5)
                self.assertTrue(marker.exists())
                self.assertEqual(0, current["LastTaskResult"])
                invoke("uninstall_collector_task.ps1", "-Preview")
                self.assertTrue(status()["Registered"])
                invoke("uninstall_collector_task.ps1")
                self.assertFalse(status()["Registered"])
            finally:
                ps("$t = Get-ScheduledTask -TaskPath '\\' | Where-Object TaskName -eq '" + name +
                   "'; if ($t) { Stop-ScheduledTask -InputObject $t; Unregister-ScheduledTask -InputObject $t -Confirm:$false }")


if __name__ == "__main__":
    unittest.main()
