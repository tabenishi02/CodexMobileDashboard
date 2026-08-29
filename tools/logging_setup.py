"""Configure safe, rotating component logs for the PC-side tools."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Iterable


COMPONENTS = ("collector", "converter", "sender")
_HANDLER_MARKER = "codex_mobile_dashboard_handler"


class Iso8601Formatter(logging.Formatter):
    """Format each log entry with a timezone-aware ISO 8601 timestamp."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, timezone.utc).astimezone().isoformat(
            timespec="seconds"
        )


def configure_component_logging(
    directory: Path,
    *,
    level: str = "INFO",
    retention_days: int = 7,
    components: Iterable[str] = COMPONENTS,
) -> None:
    """Add UTF-8 daily rotating file and console handlers to named components."""

    numeric_level = _logging_level(level)
    if not 1 <= retention_days <= 90:
        raise ValueError("retention_days_invalid")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    formatter = Iso8601Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    for component in components:
        if component not in COMPONENTS:
            raise ValueError("logging_component_invalid")
        logger = logging.getLogger(component)
        logger.setLevel(numeric_level)
        logger.propagate = False
        _remove_managed_handlers(logger)
        file_handler = TimedRotatingFileHandler(
            directory / f"{component}.log",
            when="midnight",
            interval=1,
            backupCount=retention_days,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        setattr(file_handler, _HANDLER_MARKER, True)
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        setattr(console_handler, _HANDLER_MARKER, True)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)


def _logging_level(value: str) -> int:
    name = value.upper()
    if name not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("logging_level_invalid")
    return getattr(logging, name)


def _remove_managed_handlers(logger: logging.Logger) -> None:
    for handler in tuple(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()
