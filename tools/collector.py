"""Manual PC-side commands for inspecting and replaying pending snapshots."""

from __future__ import annotations

import argparse
import configparser
import json
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

from tools.collector_runtime import CollectorRuntimeSettings, run_once
from tools.https_sender import HttpsSnapshotSender, SenderError, read_bearer_token
from tools.logging_setup import configure_component_logging
from tools.pending_snapshot_queue import InvalidPendingSnapshotError, PendingSnapshotQueue


LOGGER = logging.getLogger("sender")
COLLECTOR_LOGGER = logging.getLogger("collector")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Codex Mobile Dashboardの未送信Snapshotを手動で確認・再送します。"
    )
    parser.add_argument("--config", required=True, type=Path, help="UTF-8のcollector.ini")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("queue-status", help="未送信Snapshotの件数と安全な要約を表示")
    retry = commands.add_parser("retry-queued", help="最古の未送信Snapshotを再送")
    retry.add_argument("--all", action="store_true", help="成功する限りキューを順に再送")
    collect = commands.add_parser("collect-once", help="表示用Snapshotを1回生成")
    backfill = commands.add_parser("backfill-ai", help="過去の未処理turnを上限付きでAI補完")
    backfill.add_argument("--no-send", action="store_true", help="HTTPS送信を行わない")
    collect.add_argument("--no-send", action="store_true", help="HTTPS送信を行わない")
    arguments = parser.parse_args(argv)

    try:
        settings = _load_settings(arguments.config)
        if arguments.command in ("collect-once", "backfill-ai"):
            _configure_logging(settings)
            sender = None if arguments.no_send else _sender(settings)
            runtime_settings = _runtime_settings(arguments.config, settings)
            if arguments.command == "backfill-ai":
                runtime_settings = replace(runtime_settings, ai_inference_mode="backfill")
            count = run_once(runtime_settings, sender)
            print(json.dumps({"processed_workspaces": count}, sort_keys=True))
            return 0
        queue = PendingSnapshotQueue(settings["queue_dir"])
        if arguments.command == "queue-status":
            _print_queue_status(queue)
            return 0
        _configure_logging(settings)
        sender = _sender(settings)
        return _retry_queued(queue, sender, arguments.all)
    except (OSError, ValueError, configparser.Error, InvalidPendingSnapshotError) as error:
        error_kind = _safe_error_kind(error)
        if arguments.command in ("collect-once", "backfill-ai") and COLLECTOR_LOGGER.handlers:
            COLLECTOR_LOGGER.error("manual_collection_failed kind=%s", error_kind)
        print(f"manual_command_failed kind={error_kind}", file=sys.stderr)
        return 2
    except SenderError as error:
        LOGGER.error(
            "manual_retry_failed kind=%s operation=%s status_code=%s",
            error.kind.value,
            error.operation,
            error.status_code,
        )
        print(f"manual_command_failed kind={error.kind.value}", file=sys.stderr)
        return 2


def _load_settings(config_path: Path) -> dict:
    parser = configparser.ConfigParser(interpolation=None)
    with Path(config_path).open("r", encoding="utf-8") as stream:
        parser.read_file(stream)
    queue_dir = _path_value(parser, "storage", "queue_dir")
    settings = {"queue_dir": queue_dir}
    if parser.has_section("logging"):
        settings["log_directory"] = _path_value(parser, "logging", "directory")
        settings["log_level"] = parser.get("logging", "level", fallback="INFO")
        settings["retention_days"] = parser.getint("logging", "retention_days", fallback=7)
    if parser.has_section("sender"):
        for key in ("base_url", "token_file", "ca_file"):
            settings[key] = _path_value(parser, "sender", key) if key != "base_url" else parser.get("sender", key)
        settings["timeout_seconds"] = parser.getfloat("sender", "timeout_seconds", fallback=10)
        settings["request_max_bytes"] = parser.getint("sender", "request_max_bytes", fallback=1024 * 1024)
        settings["max_attempts"] = parser.getint("sender", "max_attempts", fallback=5)
        settings["backoff_initial_seconds"] = parser.getfloat("sender", "backoff_initial_seconds", fallback=1)
        settings["backoff_max_seconds"] = parser.getfloat("sender", "backoff_max_seconds", fallback=16)
    mode = parser.get("ai_inference", "mode", fallback="incremental").strip().lower()
    if mode not in ("off", "incremental", "backfill"):
        raise ValueError("ai_inference_mode_invalid")
    settings["ai_inference_mode"] = mode
    settings["max_calls_per_run"] = parser.getint("ai_inference", "max_calls_per_run", fallback=3)
    if settings["max_calls_per_run"] < 0: raise ValueError("ai_inference_limit_invalid")
    return settings


def _path_value(parser: configparser.ConfigParser, section: str, key: str) -> Path:
    if not parser.has_option(section, key):
        raise ValueError("configuration_missing")
    value = os.path.expandvars(parser.get(section, key))
    if not value or "%" in value:
        raise ValueError("configuration_path_invalid")
    return Path(value)


def _runtime_settings(config_path: Path, settings: dict) -> CollectorRuntimeSettings:
    parser = configparser.ConfigParser(interpolation=None)
    with Path(config_path).open("r", encoding="utf-8") as stream:
        parser.read_file(stream)
    roots = tuple(Path(os.path.expandvars(value.strip())).resolve(strict=False) for value in parser.get("discovery", "allowed_roots").splitlines() if value.strip())
    if not roots:
        raise ValueError("allowed_roots_missing")
    history_value = parser.get("storage", "history_file", fallback="").strip()
    history_file = Path(os.path.expandvars(history_value)) if history_value else settings["queue_dir"].parent / "state" / "collector-history.json"
    return CollectorRuntimeSettings(roots, _path_value(parser, "discovery", "sessions_dir"), _path_value(parser, "discovery", "archived_sessions_dir"), parser.getboolean("discovery", "scan_archived_sessions", fallback=True), _path_value(parser, "storage", "state_file"), history_file, _path_value(parser, "storage", "output_dir"), settings["queue_dir"], settings["ai_inference_mode"], _path_value(parser, "storage", "inference_ledger_file"), settings["max_calls_per_run"])
def _configure_logging(settings: dict) -> None:
    directory = settings.get("log_directory")
    if directory is None:
        return
    configure_component_logging(
        directory,
        level=settings.get("log_level", "INFO"),
        retention_days=settings.get("retention_days", 7),
        components=("collector", "converter", "sender"),
    )


def _sender(settings: dict) -> HttpsSnapshotSender:
    required = ("base_url", "token_file", "ca_file")
    if any(key not in settings for key in required):
        raise ValueError("sender_configuration_missing")
    return HttpsSnapshotSender(
        settings["base_url"],
        read_bearer_token(settings["token_file"]),
        settings["ca_file"],
        timeout_seconds=settings["timeout_seconds"],
        request_max_bytes=settings["request_max_bytes"],
        max_attempts=settings["max_attempts"],
        backoff_initial_seconds=settings["backoff_initial_seconds"],
        backoff_max_seconds=settings["backoff_max_seconds"],
    )


def _print_queue_status(queue: PendingSnapshotQueue) -> None:
    pending = queue.pending()
    value = {
        "pending_snapshots": len(pending),
        "items": [
            {
                "sequence": item.sequence,
                "workspace_id": item.workspace_id,
                "snapshot_id": item.snapshot_id,
                "file_count": len(item.uploads),
                "last_error_kind": item.last_error_kind,
                "last_error_status": item.last_error_status,
            }
            for item in pending
        ],
    }
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _retry_queued(queue: PendingSnapshotQueue, sender: HttpsSnapshotSender, all_items: bool) -> int:
    sent = 0
    while True:
        result = queue.send_next(sender)
        if result is None:
            print(json.dumps({"sent_snapshots": sent, "queue_empty": True}, sort_keys=True))
            return 0
        sent += 1
        if not all_items:
            print(json.dumps({"sent_snapshots": sent, "queue_empty": not queue.pending()}, sort_keys=True))
            return 0


def _safe_error_kind(error: BaseException) -> str:
    if isinstance(error, InvalidPendingSnapshotError):
        return "queue_invalid"
    if isinstance(error, configparser.Error):
        return "configuration_invalid"
    return "configuration_or_io"


if __name__ == "__main__":
    raise SystemExit(main())

