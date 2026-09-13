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
class BackupTaskLifecycleTests(unittest.TestCase):
    def test_install_status_run_and_uninstall(self):
        root = Path(__file__).resolve().parents[2]
        name = "CodexMobileDashboard-Backup-Test-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="dashboard task ") as directory:
            scripts = Path(directory) / "scripts"
            scripts.mkdir()
            for script in ("install_backup_task.ps1", "uninstall_backup_task.ps1", "get_backup_task_status.ps1", "run_backup_hidden.pyw"):
                shutil.copyfile(root / "scripts" / script, scripts / script)
            marker = Path(directory) / "ran.txt"
            tools = Path(directory) / "tools"
            tools.mkdir()
            (tools / "__init__.py").write_text("")
            (tools / "backup_pair.py").write_text(
                "from pathlib import Path\nPath('ran.txt').write_text('ok')\n", encoding="utf-8")
            config = Path(directory) / "backup.ini"
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
                return json.loads(invoke("get_backup_task_status.ps1", "| ConvertTo-Json -Compress"))

            self.assertFalse(status()["Registered"])
            try:
                args = "-ConfigPath '" + str(config).replace("'", "''") + "'"
                invoke("install_backup_task.ps1", args + " -Preview")
                self.assertFalse(status()["Registered"])
                invoke("install_backup_task.ps1", args)
                action = ps("(Get-ScheduledTask -TaskName '" + name + "').Actions.Execute")
                self.assertTrue(action.strip().lower().endswith("pythonw.exe"))
                registered = status()
                self.assertTrue(registered["Enabled"])
                self.assertEqual(1, registered["WeeksInterval"])
                self.assertEqual(1, registered["DaysOfWeek"])
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
                invoke("uninstall_backup_task.ps1", "-Preview")
                self.assertTrue(status()["Registered"])
                invoke("uninstall_backup_task.ps1")
                self.assertFalse(status()["Registered"])
            finally:
                ps("$t = Get-ScheduledTask -TaskPath '\\' | Where-Object TaskName -eq '" + name +
                   "'; if ($t) { Stop-ScheduledTask -InputObject $t; Unregister-ScheduledTask -InputObject $t -Confirm:$false }")


if __name__ == "__main__":
    unittest.main()
