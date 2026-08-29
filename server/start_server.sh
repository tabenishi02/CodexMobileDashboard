#!/data/data/com.termux/files/usr/bin/sh
# Start the HTTPS dashboard server with an external INI file.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEFAULT_CONFIG="$HOME/.config/codex-mobile-dashboard/server.ini"

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

exec python "$SCRIPT_DIR/server.py" --config "$CONFIG_FILE"