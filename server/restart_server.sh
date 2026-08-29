#!/data/data/com.termux/files/usr/bin/sh
# Stop the current dashboard server, then start it again.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$#" -gt 1 ]; then
    echo "usage: $0 [server.ini]" >&2
    exit 64
fi

"$SCRIPT_DIR/stop_server.sh"
exec "$SCRIPT_DIR/start_server.sh" "$@"