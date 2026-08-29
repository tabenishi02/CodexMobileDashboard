#!/data/data/com.termux/files/usr/bin/sh
# Gracefully stop only the dashboard server recorded by start_server.sh.
set -eu

PID_FILE="$HOME/.cache/codex-mobile-dashboard/server.pid"
STOP_TIMEOUT_SECONDS=20

if [ "$#" -ne 0 ]; then
    echo "usage: $0" >&2
    exit 64
fi
if [ ! -e "$PID_FILE" ]; then
    echo "server_not_running"
    exit 0
fi

PID=$(cat "$PID_FILE")
case "$PID" in
    *[!0-9]*|'') echo "server_pid_invalid" >&2; exit 65 ;;
esac
if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "server_not_running"
    exit 0
fi
if [ ! -r "/proc/$PID/cmdline" ] || ! tr '\000' ' ' < "/proc/$PID/cmdline" | grep -F -q 'server.py --config'; then
    echo "server_process_mismatch" >&2
    exit 65
fi

kill -TERM "$PID"
COUNT=0
while kill -0 "$PID" 2>/dev/null; do
    if [ "$COUNT" -ge "$STOP_TIMEOUT_SECONDS" ]; then
        echo "server_stop_timeout" >&2
        exit 75
    fi
    sleep 1
    COUNT=$((COUNT + 1))
done
rm -f "$PID_FILE"
echo "server_stopped"