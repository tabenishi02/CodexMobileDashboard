from __future__ import annotations

from pathlib import Path
import unittest


class TermuxBootScriptTests(unittest.TestCase):
    def test_boot_entry_executes_existing_start_script(self) -> None:
        directory = Path(__file__).resolve().parents[1]
        script = (directory / "termux_boot_start_server.sh").read_text(encoding="utf-8")
        self.assertTrue(script.startswith("#!/data/data/com.termux/files/usr/bin/sh\n"))
        self.assertIn('APP_SERVER_DIRECTORY="${CODEX_MOBILE_DASHBOARD_SERVER_DIR:-$HOME/CodexMobileDashboard/app/server}"', script)
        self.assertIn('exec "$APP_SERVER_DIRECTORY/start_server.sh"', script)
        self.assertNotIn("server.token", script)
        self.assertIn("set -eu\n\ntermux-wake-lock\n", script)
        self.assertLess(script.index("termux-wake-lock"), script.index("exec "))
        self.assertNotIn("termux-wake-unlock", (directory / "stop_server.sh").read_text(encoding="utf-8"))

    def test_installer_does_not_overwrite_existing_boot_entry(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "install_termux_boot.sh").read_text(encoding="utf-8")
        self.assertIn('TARGET="$BOOT_DIRECTORY/codex-mobile-dashboard"', script)
        self.assertIn('if [ -e "$TARGET" ]; then', script)
        self.assertIn('boot_entry_already_exists', script)
        self.assertIn('install -m 700 "$SOURCE" "$TARGET"', script)


if __name__ == "__main__":
    unittest.main()