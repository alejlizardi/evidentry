import math
import unittest

from providence.stats import (
    cluster_diagnostics,
    drift_test,
    fisher_exact_pvalue,
    holm_adjust,
    sample_size_certificate,
    t_quantile,
    threshold_verdict,
    wilson_interval,
)


class TestWilson(unittest.TestCase):
    def test_known_value(self):
        # 8/10 at 95%: Wilson interval is approximately (0.490, 0.943).
        low, high = wilson_interval(8, 10)
        self.assertAlmostEqual(low, 0.490, places=2)
        self.assertAlmostEqual(high, 0.943, places=2)

    def test_zero_n(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 1.0))

    def test_bounds_stay_in_unit_interval(self):
        low, high = wilson_interval(0, 5)
        self.assertGreaterEqual(low, 0.0)
        low, high = wilson_interval(5, 5)
        self.assertLessEqual(high, 1.0)

    def test_invalid_successes(self):
        with self.assertRaises(ValueError):
            wilson_interval(6, 5)


class TestFisherExact(unittest.TestCase):
    def test_audit_case_5v5(self):
        # The 2026-06-11 audit's exhibit: 5/5 vs 2/5. The z-test called this
        # significant (p=.038); the exact answer is 1/6 either tail = 0.1667.
        p = fisher_exact_pvalue(5, 5, 2, 5)
        self.assertAlmostEqual(p, 20 / 120, places=6)  # 10/120 per tail
        self.assertGreater(p, 0.05)

    def test_audit_case_10v10(self):
        # 10/10 vs 7/10: z-test p=.060, exact p = 2*120/1140 = 0.2105.
        p = fisher_exact_pvalue(10, 10, 7, 10)
        self.assertAlmostEqual(p, 240 / 1140, places=6)

    def test_identical_rates_p_one(self):
        self.assertAlmostEqual(fisher_exact_pvalue(90, 100, 90, 100), 1.0, places=6)

    def test_extreme_difference_significant(self):
        self.assertLess(fisher_exact_pvalue(95, 100, 50, 100), 1e-6)

    def test_symmetry(self):
        self.assertAlmostEqual(
            fisher_exact_pvalue(8, 10, 3, 12), fisher_exact_pvalue(3, 12, 8, 10), places=12
        )

    def test_empty_run_raises(self):
        with self.assertRaises(ValueError):
            fisher_exact_pvalue(0, 0, 5, 10)


class TestDrift(unittest.TestCase):
    def test_no_drift_identical(self):
        d = drift_test(90, 100, 90, 100)
        self.assertFalse(d.significant)
        self.assertAlmostEqual(d.p_value, 1.0, places=6)

    def test_degenerate_all_pass(self):
        d = drift_test(10, 10, 10, 10)
        self.assertFalse(d.significant)

    def test_large_drop_is_significant(self):
        d = drift_test(95, 100, 70, 100)
        self.assertTrue(d.significant)
        self.assertLess(d.p_value, 0.05)
        self.assertGreater(d.rate_a, d.rate_b)

    def test_small_n_drop_not_significant(self):
        # The defect this replaces: at n=5 a 100%->40% drop is NOT exact-
        # significant, and the tool must not report spurious drift.
        d = drift_test(5, 5, 2, 5)
        self.assertFalse(d.significant)

    def test_method_recorded(self):
        self.assertEqual(drift_test(5, 10, 5, 10).method, "fisher_exact")


class TestHolm(unittest.TestCase):
    def test_single_p_unchanged(self):
        self.assertEqual(holm_adjust([0.03]), [0.03])

    def test_known_adjustment(self):
        # Classic example: p = (0.01, 0.04, 0.03) with m=3:
        # sorted (0.01, 0.03, 0.04) -> 3*0.01=0.03, 2*0.03=0.06, 1*0.04 -> max(0.06)
        adj = holm_adjust([0.01, 0.04, 0.03])
        self.assertAlmostEqual(adj[0], 0.03, places=10)
        self.assertAlmostEqual(adj[2], 0.06, places=10)
        self.assertAlmostEqual(adj[1], 0.06, places=10)  # monotonicity enforcement

    def test_caps_at_one(self):
        self.assertTrue(all(p <= 1.0 for p in holm_adjust([0.5, 0.9, 0.7])))

    def test_controls_family(self):
        # Ten null-ish p-values around .05: unadjusted would reject several;
        # Holm rejects none.
        ps = [0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13]
        adj = holm_adjust(ps)
        self.assertFalse(any(a < 0.05 for a in adj))


class TestSampleSizeCertificate(unittest.TestCase):
    def test_already_settled(self):
        cert = sample_size_certificate(98, 100, 0.85)
        self.assertEqual(cert["status"], "already_settled")
        self.assertEqual(cert["additional_items"], 0)

    def test_point_pass_gets_finite_certificate(self):
        # 9/10 against a 0.8 threshold: point pass, settles with more items.
        cert = sample_size_certificate(9, 10, 0.80)
        self.assertEqual(cert["status"], "ok")
        self.assertEqual(cert["target_verdict"], "PASS")
        self.assertGreater(cert["n_required"], 10)
        self.assertLess(cert["n_required"], 2000)
        self.assertGreaterEqual(cert["achieved_power"], 0.80)
        self.assertEqual(cert["additional_items"], cert["n_required"] - 10)

    def test_perfect_rate_certificate(self):
        # 8/8 at threshold .85: needs n with wilson_low(n, n) >= .85 -> 22.
        cert = sample_size_certificate(8, 8, 0.85)
        self.assertEqual(cert["status"], "ok")
        self.assertEqual(cert["n_required"], 22)

    def test_fail_direction(self):
        cert = sample_size_certificate(3, 4, 0.90)
        self.assertEqual(cert["target_verdict"], "FAIL")
        self.assertEqual(cert["status"], "ok")
        self.assertGreater(cert["n_required"], 4)

    def test_rate_on_threshold_unreachable(self):
        cert = sample_size_certificate(3, 4, 0.75)
        self.assertEqual(cert["status"], "unreachable")

    def test_certificate_power_is_real(self):
        # The reported n_required must actually deliver the power it claims:
        # brute-force check the binomial tail at the certified n.
        cert = sample_size_certificate(9, 10, 0.80)
        n, p1 = cert["n_required"], 0.9
        k_star = next(
            k for k in range(n + 1) if wilson_interval(k, n)[0] >= 0.80
        )
        power = sum(
            math.comb(n, k) * p1**k * (1 - p1) ** (n - k) for k in range(k_star, n + 1)
        )
        self.assertGreaterEqual(power, 0.80)
        self.assertAlmostEqual(power, cert["achieved_power"], places=6)


class TestClusterDiagnostics(unittest.TestCase):
    def test_perfectly_correlated_clusters(self):
        # Two clusters, each internally unanimous: the effective sample size
        # collapses toward the number of clusters, not the number of items.
        passed = [True] * 5 + [False] * 5
        clusters = ["a"] * 5 + ["b"] * 5
        d = cluster_diagnostics(passed, clusters)
        self.assertEqual(d["n_clusters"], 2)
        self.assertAlmostEqual(d["deff"], 10.0, places=6)
        self.assertAlmostEqual(d["n_eff"], 1.0, places=6)

    def test_singleton_clusters_no_adjustment(self):
        passed = [True, False, True, True]
        clusters = ["w", "x", "y", "z"]
        d = cluster_diagnostics(passed, clusters)
        self.assertEqual(d["deff"], 1.0)
        self.assertEqual(d["n_eff"], 4)

    def test_negative_icc_never_narrows(self):
        # Within-cluster mix (negative ICC) can push DEFF below 1; n_eff
        # must still be capped at n so the interval never narrows.
        passed = [True, False] * 4
        clusters = ["a", "a", "b", "b", "c", "c", "d", "d"]
        d = cluster_diagnostics(passed, clusters)
        self.assertLessEqual(d["n_eff"], len(passed))

    def test_clustered_verdict_wider_than_iid(self):
        passed = [True] * 8 + [False] * 2
        clusters = ["a"] * 5 + ["b"] * 5
        plain = threshold_verdict(8, 10, 0.5)
        clustered = threshold_verdict(8, 10, 0.5, item_passed=passed, clusters=clusters)
        width_plain = plain["ci95_high"] - plain["ci95_low"]
        width_clustered = clustered["ci95_high"] - clustered["ci95_low"]
        self.assertGreater(width_clustered, width_plain)
        self.assertEqual(clustered["ci_method"], "wilson_cluster_adjusted")


class TestTQuantile(unittest.TestCase):
    def test_against_standard_tables(self):
        # t_{df, 0.975} from standard tables.
        self.assertAlmostEqual(t_quantile(0.975, 1), 12.7062, places=3)
        self.assertAlmostEqual(t_quantile(0.975, 4), 2.7764, places=3)
        self.assertAlmostEqual(t_quantile(0.975, 9), 2.2622, places=3)
        self.assertAlmostEqual(t_quantile(0.975, 30), 2.0423, places=3)

    def test_converges_to_normal(self):
        self.assertAlmostEqual(t_quantile(0.975, 100000), 1.95996, places=3)

    def test_clustered_verdict_uses_t_critical_value(self):
        passed = [True] * 8 + [False] * 2
        clusters = ["a", "a", "a", "b", "b", "b", "c", "c", "c", "d"]
        v = threshold_verdict(8, 10, 0.5, item_passed=passed, clusters=clusters)
        self.assertAlmostEqual(v["critical_value"], t_quantile(0.975, 3), places=10)


class TestThresholdVerdict(unittest.TestCase):
    def test_clear_pass_lower_bound_above_threshold(self):
        v = threshold_verdict(98, 100, 0.85)
        self.assertEqual(v["verdict"], "PASS")

    def test_point_pass_small_sample(self):
        # 3/4 = 0.75 meets a 0.75 threshold, but the interval doesn't.
        v = threshold_verdict(3, 4, 0.75)
        self.assertEqual(v["verdict"], "PASS (point)")

    def test_clear_fail(self):
        v = threshold_verdict(10, 100, 0.85)
        self.assertEqual(v["verdict"], "FAIL")

    def test_point_fail_with_uncertainty(self):
        # 3/4 = 0.75 misses a 0.90 threshold, but the upper bound reaches it.
        v = threshold_verdict(3, 4, 0.90)
        self.assertEqual(v["verdict"], "FAIL (point)")


if __name__ == "__main__":
    unittest.main()
