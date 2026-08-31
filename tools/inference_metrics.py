"""Safe aggregate inference telemetry without prompts or identifiers."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Dict, Iterator

LOGGER = logging.getLogger("collector")
KINDS = ("change_summary", "decision", "next_task", "combined_turn")
EVENTS = (
    "execution",
    "cache_hit",
    "skipped",
    "limit_reached",
    "success",
    "failure",
    "fallback",
)


@dataclass
class InferenceRunMetrics:
    counts: Dict[str, int] = field(
        default_factory=lambda: {event: 0 for event in EVENTS}
    )
    input_bytes: int = 0
    pending_remaining: int = 0


_CURRENT: ContextVar[InferenceRunMetrics | None] = ContextVar(
    "inference_run_metrics", default=None
)


def record(kind: str, event: str, input_bytes: int = 0) -> None:
    if kind not in KINDS:
        raise ValueError("inference_metric_kind_invalid")
    if event not in EVENTS:
        raise ValueError("inference_metric_event_invalid")
    if input_bytes < 0:
        raise ValueError("inference_metric_input_bytes_invalid")
    current = _CURRENT.get()
    if current is not None:
        current.counts[event] += 1
        if event == "execution":
            current.input_bytes += input_bytes
    LOGGER.info("inference_metric kind=%s event=%s input_bytes=%d", kind, event, input_bytes)


@contextmanager
def collect_run() -> Iterator[InferenceRunMetrics]:
    metrics = InferenceRunMetrics()
    token = _CURRENT.set(metrics)
    try:
        yield metrics
    finally:
        LOGGER.info(
            "inference_run_metrics executions=%d cache_hits=%d skipped=%d "
            "limit_reached=%d successes=%d failures=%d fallbacks=%d "
            "input_bytes=%d pending_remaining=%d",
            metrics.counts["execution"],
            metrics.counts["cache_hit"],
            metrics.counts["skipped"],
            metrics.counts["limit_reached"],
            metrics.counts["success"],
            metrics.counts["failure"],
            metrics.counts["fallback"],
            metrics.input_bytes,
            metrics.pending_remaining,
        )
        _CURRENT.reset(token)
