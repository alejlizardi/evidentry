import json
import tempfile
import unittest
from pathlib import Path

from evidentry.config import ConfigError, load_config
from evidentry.evidence import compare_packs
from evidentry.report import render_markdown
from evidentry.runner import run_all
from evidentry.stats import (
    _binom_cdf,
    _binom_sf,
    clopper_pearson_interval,
    drift_test,
    fisher_exact_pvalue,
    fisher_midp_pvalue,
    sample_size_certificate,
    threshold_verdict,
    wilson_interval,
)

BASE_CONFIG = """\
model:
  name: test-model
  version: "0.1"
  use_case: testing
  owner: tests
  materiality_tier: 3
  tier_rationale: synthetic
provider:
  type: mock
suites:
  - name: basic
    dataset: data.jsonl
    metric: contains
    threshold: 0.5
report:
  mappings: [sr-26-2]
  out_dir: out
"""

DATA = (
    '{"id": "1", "input": "q1", "expected": "yes", "mock_response": "yes indeed"}\n'
    '{"id": "2", "input": "q2", "expected": "no", "mock_response": "yes"}\n'
)


class TestMidP(unittest.TestCase):
    def test_audit_exhibit_hand_value(self):
        # 5/5 vs 2/5: plain Fisher gives exactly 1/6 (the 0.2.0 audit
        # exhibit). The only tables are k=2,3,4,5 with probabilities
        # 10,50,50,10 /120; mid-p halves the equally-probable class
        # {k=2, k=5}: 0 + 0.5 * 20/120 = 1/12.
        self.assertAlmostEqual(fisher_exact_pvalue(5, 5, 2, 5), 1 / 6)
        self.assertAlmostEqual(fisher_midp_pvalue(5, 5, 2, 5), 1 / 12)

    def test_midp_never_exceeds_fisher(self):
        for n_a, n_b in ((4, 4), (5, 5), (8, 6), (20, 20)):
            for s_a in range(n_a + 1):
                for s_b in range(n_b + 1):
                    self.assertLessEqual(
                        fisher_midp_pvalue(s_a, n_a, s_b, n_b),
                        fisher_exact_pvalue(s_a, n_a, s_b, n_b) + 1e-12,
                    )

    def test_symmetric_in_runs(self):
        self.assertAlmostEqual(
            fisher_midp_pvalue(5, 5, 2, 5), fisher_midp_pvalue(2, 5, 5, 5)
        )

    def test_drift_test_method_plumbing(self):
        exact = drift_test(5, 5, 2, 5)
        midp = drift_test(5, 5, 2, 5, method="fisher_midp")
        self.assertEqual(exact.method, "fisher_exact")
        self.assertEqual(midp.method, "fisher_midp")
        self.assertLess(midp.p_value, exact.p_value)
        with self.assertRaises(ValueError):
            drift_test(5, 5, 2, 5, method="boschloo")


class TestClopperPearson(unittest.TestCase):
    def test_defining_tail_identity(self):
        # CP bounds are defined by exact binomial tails: at the lower bound
        # P[X >= s] = alpha/2, at the upper bound P[X <= s] = alpha/2.
        # _binom_sf/_binom_cdf are independent implementations, so this
        # cross-checks the beta-quantile inversion.
        for s, n in ((8, 10), (3, 4), (45, 50), (1, 30)):
            low, high = clopper_pearson_interval(s, n)
            self.assertAlmostEqual(_binom_sf(s, n, low), 0.025, places=6)
            self.assertAlmostEqual(_binom_cdf(s, n, high), 0.025, places=6)

    def test_boundary_closed_forms(self):
        # S=0: upper = 1 - (alpha/2)^(1/n); S=n: lower = (alpha/2)^(1/n).
        low, high = clopper_pearson_interval(0, 10)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 1 - 0.025 ** (1 / 10), places=9)
        low, high = clopper_pearson_interval(10, 10)
        self.assertEqual(high, 1.0)
        self.assertAlmostEqual(low, 0.025 ** (1 / 10), places=9)

    def test_guaranteed_coverage_at_n10(self):
        # The strict-mode promise, checked exactly: coverage >= 95% at every
        # p on a dense grid (no Monte Carlo).
        n = 10
        bounds = [clopper_pearson_interval(k, n) for k in range(n + 1)]
        for i in range(1, 400):
            p = i / 400
            cover = 0.0
            for k in range(n + 1):
                if bounds[k][0] <= p <= bounds[k][1]:
                    # binomial pmf via the independent cdf helpers
                    cover += _binom_cdf(k, n, p) - _binom_cdf(k - 1, n, p)
            self.assertGreaterEqual(cover, 0.95 - 1e-9, msg=f"p={p}")

    def test_wider_than_wilson(self):
        for s, n in ((8, 10), (3, 4), (18, 20), (45, 50)):
            w = wilson_interval(s, n)
            cp = clopper_pearson_interval(s, n)
            self.assertLessEqual(cp[0], w[0] + 1e-12)
            self.assertGreaterEqual(cp[1], w[1] - 1e-12)


class TestStrictModeVerdicts(unittest.TestCase):
    def test_verdict_can_differ_between_intervals(self):
        s, n = 49, 50
        w_low = wilson_interval(s, n)[0]
        cp_low = clopper_pearson_interval(s, n)[0]
        self.assertLess(cp_low, w_low)
        t = (cp_low + w_low) / 2
        self.assertEqual(threshold_verdict(s, n, t)["verdict"], "PASS")
        strict = threshold_verdict(s, n, t, interval="clopper_pearson")
        self.assertEqual(strict["verdict"], "PASS (point)")
        self.assertEqual(strict["ci_method"], "clopper_pearson")

    def test_unknown_interval_rejected(self):
        with self.assertRaises(ValueError):
            threshold_verdict(5, 10, 0.5, interval="wald")

    def test_clustering_overrides_strict_mode(self):
        flags = [True, True, True, False] * 3
        clusters = ["a", "a", "b", "b", "c", "c", "d", "d", "e", "e", "f", "f"]
        out = threshold_verdict(
            9, 12, 0.5, item_passed=flags, clusters=clusters, interval="clopper_pearson"
        )
        self.assertEqual(out["ci_method"], "wilson_cluster_adjusted")
        self.assertTrue(out["strict_interval_overridden_by_clustering"])

    def test_certificate_needs_more_items_under_strict_intervals(self):
        wilson_cert = sample_size_certificate(17, 20, 0.8)
        strict_cert = sample_size_certificate(17, 20, 0.8, interval="clopper_pearson")
        self.assertEqual(wilson_cert["status"], "ok")
        self.assertEqual(strict_cert["status"], "ok")
        self.assertGreaterEqual(strict_cert["n_required"], wilson_cert["n_required"])
        self.assertGreaterEqual(strict_cert["achieved_power"], 0.80)


class TestConfigAndEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "data.jsonl").write_text(DATA, encoding="utf-8")

    def load(self, cfg_text):
        (self.tmp / "evidentry.yaml").write_text(cfg_text, encoding="utf-8")
        return load_config(self.tmp / "evidentry.yaml")

    def with_stats(self, block):
        return BASE_CONFIG + "statistics:\n" + block

    def test_defaults_and_validation(self):
        cfg = self.load(BASE_CONFIG)
        self.assertEqual(cfg.intervals, "wilson")
        self.assertEqual(cfg.drift_test, "fisher_exact")
        with self.assertRaises(ConfigError):
            self.load(self.with_stats("  intervals: wald\n"))
        with self.assertRaises(ConfigError):
            self.load(self.with_stats("  drift_test: z_test\n"))

    def test_explicit_defaults_keep_config_hash(self):
        h_absent = self.load(BASE_CONFIG).canonical_hash()
        h_explicit = self.load(
            self.with_stats("  intervals: wilson\n  drift_test: fisher_exact\n")
        ).canonical_hash()
        self.assertEqual(h_absent, h_explicit)
        h_strict = self.load(self.with_stats("  intervals: clopper_pearson\n")).canonical_hash()
        self.assertNotEqual(h_absent, h_strict)

    def test_strict_mode_end_to_end(self):
        results = run_all(self.load(self.with_stats("  intervals: clopper_pearson\n")))
        self.assertEqual(results["statistics"]["intervals"], "clopper_pearson")
        suite = results["suites"][0]
        self.assertEqual(suite["ci_method"], "clopper_pearson")
        report = render_markdown(results, ["sr-26-2"])
        self.assertIn("strict mode", report)
        self.assertIn("Clopper-Pearson", report)

    def test_midp_drift_end_to_end(self):
        results = run_all(self.load(self.with_stats("  drift_test: fisher_midp\n")))
        # fabricate a baseline pack with the same dataset/metric/runs
        baseline_dir = self.tmp / "baseline"
        baseline_dir.mkdir()
        baseline = json.loads(json.dumps(results))
        baseline["suites"][0]["n_passed"] = 2
        (baseline_dir / "results.json").write_text(json.dumps(baseline), encoding="utf-8")
        drift = compare_packs(baseline_dir, results)
        row = drift["suites"][0]
        self.assertTrue(row["comparable"])
        self.assertEqual(row["method"], "fisher_midp")
        self.assertEqual(drift["method"], "fisher_midp, holm_adjusted")
        report = render_markdown(results, ["sr-26-2"], drift=drift)
        self.assertIn("mid-p", report)


if __name__ == "__main__":
    unittest.main()
