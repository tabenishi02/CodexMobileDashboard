from __future__ import annotations

from pathlib import Path
import unittest


class ServerLifecycleScriptTests(unittest.TestCase):
    def test_start_script_records_pid_and_refuses_active_instance(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "start_server.sh").read_text(encoding="utf-8")
        self.assertIn('PID_FILE="$RUNTIME_DIRECTORY/server.pid"', script)
        self.assertIn('if kill -0 "$PID" 2>/dev/null; then', script)
        self.assertIn('server_already_running', script)
        self.assertIn("printf '%s\\n' \"$$\" > \"$PID_FILE\"", script)

    def test_stop_script_validates_recorded_process_before_signalling(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "stop_server.sh").read_text(encoding="utf-8")
        self.assertIn('tr \'\\000\' \' \' < "/proc/$PID/cmdline"', script)
        self.assertIn('server_process_mismatch', script)
        self.assertIn('kill -TERM "$PID"', script)
        self.assertIn('server_stop_timeout', script)
        self.assertNotIn('kill -KILL', script)

    def test_restart_stops_before_starting(self) -> None:
        script = (Path(__file__).resolve().parents[1] / "restart_server.sh").read_text(encoding="utf-8")
        self.assertIn('"$SCRIPT_DIR/stop_server.sh"', script)
        self.assertIn('exec "$SCRIPT_DIR/start_server.sh" "$@"', script)


if __name__ == "__main__":
    unittest.main()