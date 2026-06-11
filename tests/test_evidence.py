import json
import shutil
import tempfile
import unittest
from pathlib import Path

from evidentry.config import load_config
from evidentry.evidence import build_pack, compare_packs, verify_pack
from evidentry.runner import run_all

EXAMPLE = Path(__file__).parent.parent / "examples" / "credit_memo_summarizer"


class TestEvidencePack(unittest.TestCase):
    def setUp(self):
        # Copy the example into a temp dir so packs don't pollute the repo.
        self.tmp = Path(tempfile.mkdtemp())
        for f in EXAMPLE.iterdir():
            if f.is_file():
                shutil.copy(f, self.tmp / f.name)
        self.config = load_config(self.tmp / "evidentry.yaml")
        self.results = run_all(self.config)

    def test_build_and_verify_roundtrip(self):
        pack = build_pack(self.config, self.results)
        self.assertTrue((pack / "manifest.json").exists())
        self.assertTrue((pack / "report.md").exists())
        self.assertTrue((pack / "results.json").exists())
        ok, problems = verify_pack(pack)
        self.assertTrue(ok, problems)

    def test_tamper_detection(self):
        pack = build_pack(self.config, self.results)
        results_file = pack / "results.json"
        data = json.loads(results_file.read_text(encoding="utf-8"))
        # Forge a suite that genuinely has failures, so the file content
        # really changes (a suite already at n_passed == n_items would make
        # this edit a byte-identical no-op). newline="\n" keeps the bytes
        # platform-independent: the hash must catch the forgery itself, not
        # an accidental CRLF conversion.
        suite = next(s for s in data["suites"] if s["n_passed"] < s["n_items"])
        suite["n_passed"] = suite["n_items"]
        results_file.write_text(
            json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
        )
        ok, problems = verify_pack(pack)
        self.assertFalse(ok)
        self.assertTrue(any("results.json" in p for p in problems))
        self.assertTrue(any("pack_id" in p for p in problems))

    def test_missing_file_detection(self):
        pack = build_pack(self.config, self.results)
        (pack / "report.md").unlink()
        ok, problems = verify_pack(pack)
        self.assertFalse(ok)
        self.assertTrue(any("missing file" in p for p in problems))

    def test_drift_comparison(self):
        pack_a = build_pack(self.config, self.results)
        drift = compare_packs(pack_a, self.results)
        self.assertIsNotNone(drift)
        # Same results vs themselves: comparable, exact p of 1, no drift.
        for row in drift["suites"]:
            self.assertTrue(row["comparable"])
            self.assertFalse(row["significant"])
            self.assertIsNotNone(row["p_holm"])
        self.assertEqual(drift["method"], "fisher_exact, holm_adjusted")

    def test_drift_refuses_changed_dataset(self):
        # A baseline whose dataset bytes differ must yield NOT COMPARABLE,
        # not a p-value across two different exams (2026-06-11 audit).
        pack_a = build_pack(self.config, self.results)
        results_b = json.loads(json.dumps(self.results))  # deep copy
        results_b["suites"][0]["dataset_sha256"] = "0" * 64
        drift = compare_packs(pack_a, results_b)
        changed = next(r for r in drift["suites"] if r["suite"] == results_b["suites"][0]["suite"])
        self.assertFalse(changed["comparable"])
        self.assertIn("dataset changed", changed["reason"])
        self.assertIsNone(changed["p_value"])
        self.assertFalse(changed["significant"])
        # The untouched suites are still tested.
        self.assertTrue(any(r["comparable"] for r in drift["suites"]))

    def test_example_has_expected_verdicts(self):
        by_name = {s["suite"]: s for s in self.results["suites"]}
        self.assertEqual(by_name["factual_accuracy"]["n_passed"], 8)
        self.assertEqual(by_name["numeric_extraction"]["n_passed"], 3)  # num-04 fails
        self.assertEqual(by_name["out_of_scope_refusal"]["n_passed"], 3)  # ref-04 fails
        self.assertTrue(by_name["numeric_extraction"]["verdict"].startswith("FAIL"))


if __name__ == "__main__":
    unittest.main()
