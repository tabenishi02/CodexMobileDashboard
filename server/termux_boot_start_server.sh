#!/data/data/com.termux/files/usr/bin/sh
# Termux:Boot entry point for Codex Mobile Dashboard.
set -eu

APP_SERVER_DIRECTORY="${CODEX_MOBILE_DASHBOARD_SERVER_DIR:-$HOME/CodexMobileDashboard/app/server}"
exec "$APP_SERVER_DIRECTORY/start_server.sh"