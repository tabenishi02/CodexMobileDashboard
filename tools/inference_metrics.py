"""Safe aggregate inference telemetry without prompts or identifiers."""
from __future__ import annotations

import logging

LOGGER = logging.getLogger("collector")


def record(kind: str, event: str, input_bytes: int = 0) -> None:
    if input_bytes < 0:
        raise ValueError("inference_metric_input_bytes_invalid")
    LOGGER.info("inference_metric kind=%s event=%s input_bytes=%d", kind, event, input_bytes)
