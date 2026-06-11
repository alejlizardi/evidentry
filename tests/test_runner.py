import json
import tempfile
import unittest
from pathlib import Path

from evidentry.config import load_config
from evidentry.runner import load_dataset, run_all

CONFIG = """\
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


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "evidentry.yaml").write_text(CONFIG, encoding="utf-8")
        (self.tmp / "data.jsonl").write_text(DATA, encoding="utf-8")

    def test_run_all_shapes_and_counts(self):
        results = run_all(load_config(self.tmp / "evidentry.yaml"))
        self.assertEqual(results["summary"]["total_items"], 2)
        suite = results["suites"][0]
        self.assertEqual(suite["n_passed"], 1)  # item 2 fails: 'no' not in 'yes'
        self.assertEqual(suite["pass_rate"], 0.5)
        self.assertIn("config_sha256", results)
        self.assertEqual(len(suite["dataset_sha256"]), 64)

    def test_external_provider(self):
        outputs = self.tmp / "outputs.jsonl"
        outputs.write_text(
            '{"id": "1", "output": "yes"}\n{"id": "2", "output": "no"}\n', encoding="utf-8"
        )
        cfg_text = CONFIG.replace(
            "provider:\n  type: mock",
            "provider:\n  type: external\n  results_file: outputs.jsonl",
        )
        (self.tmp / "evidentry.yaml").write_text(cfg_text, encoding="utf-8")
        results = run_all(load_config(self.tmp / "evidentry.yaml"))
        self.assertEqual(results["suites"][0]["n_passed"], 2)
        self.assertEqual(results["provider"]["type"], "external")

    def test_duplicate_ids_rejected(self):
        (self.tmp / "data.jsonl").write_text(
            '{"id": "1", "input": "a"}\n{"id": "1", "input": "b"}\n', encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            load_dataset(self.tmp / "data.jsonl")

    def test_empty_dataset_rejected(self):
        (self.tmp / "data.jsonl").write_text("\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_dataset(self.tmp / "data.jsonl")

    def test_multi_run_consistency_required(self):
        # With runs: 2 and a deterministic mock, results are stable; the
        # structure should reflect both runs.
        cfg_text = CONFIG.replace("threshold: 0.5", "threshold: 0.5\n    runs: 2")
        (self.tmp / "evidentry.yaml").write_text(cfg_text, encoding="utf-8")
        results = run_all(load_config(self.tmp / "evidentry.yaml"))
        item = results["suites"][0]["items"][0]
        self.assertEqual(len(item["runs"]), 2)
        self.assertTrue(item["passed"])


if __name__ == "__main__":
    unittest.main()
