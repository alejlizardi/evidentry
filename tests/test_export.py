import json
import shutil
import tempfile
import unittest
from pathlib import Path

from providence.config import load_config
from providence.evidence import build_pack, export_series
from providence.runner import run_all

EXAMPLE = Path(__file__).parent.parent / "examples" / "credit_memo_summarizer"
MODEL_HISTORY = Path(__file__).parent.parent / "examples" / "model_history"


def _force_suite_failures(results: dict, suite_name: str, n_fail: int) -> dict:
    """Return a deep copy of results with the first n_fail items of one suite
    flipped to failing — used to manufacture a real drift between two packs."""
    results = json.loads(json.dumps(results))
    suite = next(s for s in results["suites"] if s["suite"] == suite_name)
    flipped = 0
    for item in suite["items"]:
        if flipped >= n_fail:
            break
        if item["passed"]:
            item["passed"] = False
            for run in item["runs"]:
                run["passed"] = False
            flipped += 1
    suite["n_passed"] = sum(1 for i in suite["items"] if i["passed"])
    suite["n_items"] = len(suite["items"])
    suite["pass_rate"] = suite["n_passed"] / suite["n_items"]
    results["summary"]["total_passed"] = sum(s["n_passed"] for s in results["suites"])
    return results


class TestExportSeries(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        src = self.tmp / "src"
        src.mkdir()
        for f in EXAMPLE.iterdir():
            if f.is_file():
                shutil.copy(f, src / f.name)
        self.config = load_config(src / "providence.yaml")
        self.results = run_all(self.config)

    def test_export_two_packs(self):
        # A baseline pack and a second pack where factual_accuracy collapses
        # from 8/8 to 2/8 — a large, real, Holm-significant Fisher drift.
        pack_a = build_pack(self.config, self.results)
        drifted = _force_suite_failures(self.results, "factual_accuracy", 6)
        pack_b = build_pack(self.config, drifted)

        out = self.tmp / "site-data"
        bundle = export_series([pack_a, pack_b], out)

        # Static layout exists.
        self.assertTrue((out / "index.json").exists())
        self.assertTrue((out / "drift.json").exists())
        self.assertEqual(len(list((out / "packs").glob("*.json"))), 2)

        # index.json: one ordered entry per pack with the expected fields.
        index = json.loads((out / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(len(index), 2)
        self.assertEqual(index, bundle["index"])
        for entry in index:
            for key in (
                "id", "model_name", "version", "generated_at",
                "suites_passed", "total_suites", "headline_verdict",
            ):
                self.assertIn(key, entry)
        # Each pack file is named for its id and is valid JSON.
        for entry in index:
            pack_file = out / "packs" / f"{entry['id']}.json"
            self.assertTrue(pack_file.exists())
            json.loads(pack_file.read_text(encoding="utf-8"))

        # drift.json: one row-set for the single consecutive pair, and the
        # collapsed suite is flagged significant.
        drift = json.loads((out / "drift.json").read_text(encoding="utf-8"))
        self.assertEqual(len(drift), 1)
        rows = drift[0]["rows"]
        self.assertTrue(rows, "expected drift rows for overlapping suites")
        acc = next(r for r in rows if r["suite"] == "factual_accuracy")
        self.assertTrue(acc["comparable"])
        self.assertTrue(acc["significant"], f"expected significant drift, got {acc}")
        self.assertAlmostEqual(acc["rate_a"], 1.0)
        self.assertAlmostEqual(acc["rate_b"], 0.25)

    def test_headline_verdict_is_worst_suite(self):
        # The example pack has a FAIL (point) suite, so its headline is the
        # worst verdict, not a clean PASS.
        pack = build_pack(self.config, self.results)
        out = self.tmp / "site-data"
        bundle = export_series([pack], out)
        self.assertEqual(bundle["index"][0]["headline_verdict"], "FAIL (point)")


class TestModelHistorySeries(unittest.TestCase):
    """Guards the committed examples/model_history story: one model across four
    versions, exactly one Holm-significant drift event, a borderline suite that
    stays PASS (point) throughout. If anyone perturbs the datasets or the
    statistics, the story breaks here, not silently in the dashboard."""

    VERSIONS = ["v1.0.0", "v1.1.0", "v1.2.0", "v1.3.0"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pack_dirs = []
        for v in self.VERSIONS:
            # Copy each version dir out so generated packs don't pollute the repo.
            src = self.tmp / v
            shutil.copytree(MODEL_HISTORY / v, src)
            # Drop any pre-generated evidence so we build fresh from configs.
            if (src / "evidence").exists():
                shutil.rmtree(src / "evidence")
            config = load_config(src / "providence.yaml")
            results = run_all(config)
            self.pack_dirs.append(build_pack(config, results))

    def test_one_holm_significant_drift_on_factual_accuracy(self):
        out = self.tmp / "site-data"
        bundle = export_series(self.pack_dirs, out)
        drift = bundle["drift"]
        self.assertEqual(len(drift), 3)  # three consecutive pairs

        significant = [
            (pair["from_version"], pair["to_version"], r["suite"])
            for pair in drift
            for r in pair["rows"]
            if r.get("significant")
        ]
        self.assertEqual(
            significant, [("1.1.0", "1.2.0", "factual_accuracy")],
            f"expected exactly one drift event (1.1.0->1.2.0 factual_accuracy), got {significant}",
        )

        # Every stable suite must be comparable across every pair — identical
        # dataset bytes are what make the timeline a real test rather than a
        # string of 'NOT COMPARABLE' rows.
        for pair in drift:
            for r in pair["rows"]:
                self.assertTrue(
                    r.get("comparable"),
                    f"{r['suite']} not comparable {pair['from_version']}->{pair['to_version']}: {r.get('reason')}",
                )

    def test_borderline_suite_is_pass_point_throughout(self):
        # use_limit_refusal carries the landing 'passes but not settled' story.
        for pack_dir in self.pack_dirs:
            results = json.loads((pack_dir / "results.json").read_text(encoding="utf-8"))
            refusal = next(s for s in results["suites"] if s["suite"] == "use_limit_refusal")
            self.assertEqual(refusal["verdict"], "PASS (point)")
            self.assertLess(refusal["ci95_low"], refusal["threshold"])
            self.assertGreaterEqual(refusal["pass_rate"], refusal["threshold"])


if __name__ == "__main__":
    unittest.main()
