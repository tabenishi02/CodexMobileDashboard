#!/data/data/com.termux/files/usr/bin/sh
# Install the Termux:Boot entry point without overwriting an existing file.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BOOT_DIRECTORY="$HOME/.termux/boot"
TARGET="$BOOT_DIRECTORY/codex-mobile-dashboard"
SOURCE="$SCRIPT_DIR/termux_boot_start_server.sh"

if [ "$#" -ne 0 ]; then
    echo "usage: $0" >&2
    exit 64
fi
if [ ! -f "$SOURCE" ]; then
    echo "boot_template_not_found" >&2
    exit 66
fi
if [ -e "$TARGET" ]; then
    echo "boot_entry_already_exists" >&2
    exit 73
fi

mkdir -p "$BOOT_DIRECTORY"
install -m 700 "$SOURCE" "$TARGET"
echo "boot_entry_installed"