"""Eligibility rules for one-call combined turn inference."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class CombinedTurnEligibility:
    eligible: bool
    fallback_reason: Optional[str]


def assess_combined_turn(turn_status: str, turn_id: str, eligible_turn_ids: Optional[Iterable[str]], message_turn_ids: Iterable[Optional[str]], inputs_masked: bool, has_next_task_candidate: bool) -> CombinedTurnEligibility:
    if turn_status == "in_progress":
        return CombinedTurnEligibility(False, "turn_in_progress")
    if eligible_turn_ids is not None and turn_id not in set(eligible_turn_ids):
        return CombinedTurnEligibility(False, "turn_not_incremental")
    if not inputs_masked:
        return CombinedTurnEligibility(False, "inference_input_not_masked")
    if not has_next_task_candidate:
        return CombinedTurnEligibility(False, "next_task_context_missing")
    if any(value not in (None, turn_id) for value in message_turn_ids):
        return CombinedTurnEligibility(False, "cross_turn_context")
    return CombinedTurnEligibility(True, None)