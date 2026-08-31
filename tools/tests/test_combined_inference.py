import unittest
from tools.combined_inference import assess_combined_turn


class CombinedInferenceEligibilityTests(unittest.TestCase):
    def test_completed_masked_incremental_turn_is_eligible(self):
        value = assess_combined_turn("completed", "turn-1", ("turn-1",), ("turn-1",), True, True)
        self.assertTrue(value.eligible)

    def test_unmasked_or_cross_turn_input_falls_back(self):
        self.assertEqual("inference_input_not_masked", assess_combined_turn("completed", "turn-1", None, ("turn-1",), False, True).fallback_reason)
        self.assertEqual("cross_turn_context", assess_combined_turn("completed", "turn-1", None, ("turn-1", "turn-2"), True, True).fallback_reason)


if __name__ == "__main__":
    unittest.main()