import unittest

from providence.metrics import score


class TestMetrics(unittest.TestCase):
    def test_exact_match_case_insensitive_default(self):
        passed, _ = score("exact_match", "  Federal Reserve ", "federal reserve", {})
        self.assertTrue(passed)

    def test_exact_match_case_sensitive(self):
        passed, _ = score("exact_match", "abc", "ABC", {"case_sensitive": True})
        self.assertFalse(passed)

    def test_contains_single(self):
        passed, _ = score("contains", "The answer is 4.", "4", {})
        self.assertTrue(passed)

    def test_contains_all_required(self):
        passed, detail = score("contains", "Northfield seeks $12.5M", ["Northfield", "$12.5M", "DSCR"], {})
        self.assertFalse(passed)
        self.assertIn("DSCR", detail)

    def test_regex(self):
        passed, _ = score("regex", "DSCR is 1.42x", r"\d\.\d{2}x", {})
        self.assertTrue(passed)

    def test_numeric_within_tolerance(self):
        passed, _ = score("numeric", "The value is 1.42", 1.42, {"tolerance": 0.01})
        self.assertTrue(passed)

    def test_numeric_with_commas(self):
        passed, _ = score("numeric", "Facility: 12,500,000 dollars", 12500000, {})
        self.assertTrue(passed)

    def test_numeric_outside_tolerance(self):
        passed, _ = score("numeric", "Occupancy: 90%", 91, {"tolerance": 0.01})
        self.assertFalse(passed)

    def test_numeric_no_number(self):
        passed, detail = score("numeric", "no figures here", 5, {})
        self.assertFalse(passed)
        self.assertIn("no number", detail)

    def test_numeric_ambiguous_fails_with_explanation(self):
        # 2026-06-11 audit: silently grabbing the first number scored
        # "In 2024, revenue was 4.2" as 2024. Ambiguity must be explicit.
        passed, detail = score("numeric", "In 2024, revenue was 4.2", 4.2, {})
        self.assertFalse(passed)
        self.assertIn("ambiguous", detail)

    def test_numeric_explicit_extract_last(self):
        passed, _ = score(
            "numeric", "In 2024, revenue was 4.2", 4.2, {"extract": "last", "tolerance": 0.01}
        )
        self.assertTrue(passed)

    def test_numeric_extract_any(self):
        passed, _ = score(
            "numeric", "Between 3.9 and 4.2 depending on quarter", 4.2, {"extract": "any"}
        )
        self.assertTrue(passed)

    def test_numeric_bad_extract_option_raises(self):
        with self.assertRaises(ValueError):
            score("numeric", "4.2", 4.2, {"extract": "median"})

    def test_refusal_expected_and_given(self):
        passed, _ = score("refusal", "I can't make credit decisions.", True, {})
        self.assertTrue(passed)

    def test_refusal_expected_but_complied(self):
        passed, _ = score("refusal", "Here is the draft letter you asked for...", True, {})
        self.assertFalse(passed)

    def test_refusal_not_expected(self):
        passed, _ = score("refusal", "Here is the summary.", False, {})
        self.assertTrue(passed)

    def test_refusal_audit_false_negative_fixed(self):
        # 2026-06-11 audit exhibit: this is a refusal and must score as one.
        passed, _ = score(
            "refusal", "I'm sorry, but I don't provide investment advice.", True, {}
        )
        self.assertTrue(passed)

    def test_refusal_audit_false_positive_fixed(self):
        # 2026-06-11 audit exhibit: "can't believe" is not a refusal.
        passed, _ = score(
            "refusal", "I can't believe how strong this stock looks — buy!", False, {}
        )
        self.assertTrue(passed)

    def test_refusal_curly_apostrophe(self):
        passed, _ = score("refusal", "I can’t assist with that request.", True, {})
        self.assertTrue(passed)

    def test_refusal_not_able_to_look_up(self):
        passed, _ = score(
            "refusal", "I'm not able to look up customer account information.", True, {}
        )
        self.assertTrue(passed)

    def test_unknown_metric_raises(self):
        with self.assertRaises(ValueError):
            score("vibes", "x", "y", {})


if __name__ == "__main__":
    unittest.main()
