from __future__ import annotations

from pathlib import Path
import unittest


class StartServerScriptTests(unittest.TestCase):
    def test_script_uses_external_config_and_executes_server(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "start_server.sh").read_text(encoding="utf-8")
        self.assertTrue(script.startswith("#!/data/data/com.termux/files/usr/bin/sh\n"))
        self.assertIn('DEFAULT_CONFIG="$HOME/.config/codex-mobile-dashboard/server.ini"', script)
        self.assertIn('exec python "$SCRIPT_DIR/server.py" --config "$CONFIG_FILE"', script)
        self.assertNotIn("server.token", script)


if __name__ == "__main__":
    unittest.main()