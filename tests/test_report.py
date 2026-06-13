import shutil
import tempfile
import unittest
from pathlib import Path

from providence.config import load_config
from providence.report import gap_analysis, load_mapping, render_markdown
from providence.runner import run_all

EXAMPLE = Path(__file__).parent.parent / "examples" / "credit_memo_summarizer"


class TestReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        for f in EXAMPLE.iterdir():
            if f.is_file():
                shutil.copy(f, cls.tmp / f.name)
        cls.config = load_config(cls.tmp / "providence.yaml")
        cls.results = run_all(cls.config)

    def test_mappings_load(self):
        for name in ("sr-26-2", "eu-ai-act-annex-iv"):
            mapping = load_mapping(name)
            self.assertIn("requirements", mapping)
            ids = [r["id"] for r in mapping["requirements"]]
            self.assertEqual(len(ids), len(set(ids)), "duplicate requirement ids")

    def test_unknown_mapping_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_mapping("sox-404")

    def test_gap_analysis_statuses(self):
        mapping = load_mapping("sr-26-2")
        rows = gap_analysis(self.results, mapping, has_drift=False)
        by_id = {r["id"]: r for r in rows}
        # V.OA maps to factual_accuracy (passing) and numeric_extraction
        # (failing by design): the row must say FAILING, not EVIDENCED.
        self.assertTrue(by_id["SR26-2.V.OA"]["status"].startswith("FAILING"))
        self.assertIn("numeric_extraction", by_id["SR26-2.V.OA"]["failing_suites"])
        # IV.USE maps to the refusal suite, which point-passes.
        self.assertEqual(by_id["SR26-2.IV.USE"]["status"], "EVIDENCED")
        self.assertEqual(by_id["SR26-2.V.MON"]["status"], "NOT EVIDENCED")  # no drift yet
        self.assertEqual(by_id["SR26-2.III.EC"]["status"], "NOT EVIDENCED")  # manual only
        self.assertEqual(by_id["SR26-2.V.CS"]["status"], "PARTIAL (manual input required)")

    def test_gap_analysis_unmapped_suite_results_requirement_sees_all_suites(self):
        # IV.TEST is satisfied_by suite_results but no suite names it, so it
        # falls back to all suites — and one of those is failing.
        mapping = load_mapping("sr-26-2")
        rows = gap_analysis(self.results, mapping, has_drift=False)
        by_id = {r["id"]: r for r in rows}
        self.assertTrue(by_id["SR26-2.IV.TEST"]["status"].startswith("FAILING"))

    def test_gap_analysis_with_drift(self):
        mapping = load_mapping("sr-26-2")
        rows = gap_analysis(self.results, mapping, has_drift=True)
        by_id = {r["id"]: r for r in rows}
        self.assertEqual(by_id["SR26-2.V.MON"]["status"], "EVIDENCED")

    def test_render_contains_key_sections(self):
        text = render_markdown(self.results, ["sr-26-2"])
        self.assertIn("# Model Validation Evidence Report", text)
        self.assertIn("Wilson 95% confidence intervals", text)
        self.assertIn("Requirement coverage", text)
        self.assertIn("NOT EVIDENCED", text)
        self.assertIn("Scope note", text)  # disclaimer always present
        self.assertIn("out_of_scope_refusal", text)

    def test_render_includes_sample_size_certificates(self):
        # factual_accuracy is a point pass (8/8 against 0.85) and must carry
        # a finite certificate; the refusal suite sits exactly on its
        # threshold and must carry the unreachable explanation instead.
        text = render_markdown(self.results, ["sr-26-2"])
        self.assertIn("Sample-size certificate", text)
        self.assertIn("would settle **PASS**", text)
        self.assertIn("too close to the threshold", text)


if __name__ == "__main__":
    unittest.main()
