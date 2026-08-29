#!/data/data/com.termux/files/usr/bin/sh
# Start the HTTPS dashboard server with an external INI file.
set -eu
umask 077

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEFAULT_CONFIG="$HOME/.config/codex-mobile-dashboard/server.ini"
RUNTIME_DIRECTORY="$HOME/.cache/codex-mobile-dashboard"
PID_FILE="$RUNTIME_DIRECTORY/server.pid"

if [ "$#" -gt 1 ]; then
    echo "usage: $0 [server.ini]" >&2
    exit 64
fi

CONFIG_FILE=${1:-$DEFAULT_CONFIG}
if [ ! -f "$CONFIG_FILE" ]; then
    echo "server_config_not_found" >&2
    exit 66
fi
if [ ! -r "$CONFIG_FILE" ]; then
    echo "server_config_unreadable" >&2
    exit 77
fi

mkdir -p "$RUNTIME_DIRECTORY"
if [ -e "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    case "$PID" in
        *[!0-9]*|'') echo "server_pid_invalid" >&2; exit 65 ;;
    esac
    if kill -0 "$PID" 2>/dev/null; then
        echo "server_already_running" >&2
        exit 73
    fi
    rm -f "$PID_FILE"
fi
printf '%s\n' "$$" > "$PID_FILE"
exec python "$SCRIPT_DIR/server.py" --config "$CONFIG_FILE"