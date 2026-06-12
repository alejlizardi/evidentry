import json
import tempfile
import unittest
from pathlib import Path

from evidentry.cli import main as cli_main

CONFIG = """\
model:
  name: gate-model
  version: "1.0"
  use_case: testing
  owner: tests
  materiality_tier: 3
  tier_rationale: synthetic
provider:
  type: mock
suites:
  - name: gated
    dataset: data.jsonl
    metric: contains
    threshold: 0.5
report:
  mappings: [sr-26-2]
  out_dir: evidence
"""


def dataset(n_pass: int, n: int) -> str:
    rows = []
    for i in range(n):
        ok = i < n_pass
        rows.append(
            json.dumps(
                {
                    "id": f"i{i}",
                    "input": "q",
                    "expected": "yes",
                    "mock_response": "yes" if ok else "no",
                }
            )
        )
    return "\n".join(rows) + "\n"


class TestRunExitCodes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "evidentry.yaml").write_text(CONFIG, encoding="utf-8")

    def run_cli(self, *extra: str) -> int:
        return cli_main(["run", "-c", str(self.tmp / "evidentry.yaml"), *extra])

    def pack_dirs(self):
        return sorted((self.tmp / "evidence").iterdir())

    def test_pass_is_zero_fail_is_two(self):
        (self.tmp / "data.jsonl").write_text(dataset(20, 20), encoding="utf-8")
        self.assertEqual(self.run_cli(), 0)
        (self.tmp / "data.jsonl").write_text(dataset(2, 20), encoding="utf-8")
        self.assertEqual(self.run_cli(), 2)

    def test_fail_on_drift_requires_baseline(self):
        (self.tmp / "data.jsonl").write_text(dataset(20, 20), encoding="utf-8")
        self.assertEqual(self.run_cli("--fail-on-drift"), 1)

    def test_changed_dataset_is_not_comparable_so_no_drift_exit(self):
        (self.tmp / "data.jsonl").write_text(dataset(20, 20), encoding="utf-8")
        self.assertEqual(self.run_cli(), 0)
        (baseline,) = self.pack_dirs()
        (self.tmp / "data.jsonl").write_text(dataset(12, 20), encoding="utf-8")
        self.assertEqual(self.run_cli("--baseline", str(baseline), "--fail-on-drift"), 0)

    def test_drift_exit_three_only_with_flag(self):
        # Baseline: 20/20 (mock). Current: same dataset bytes, but outputs
        # ingested externally regress to 12/20 — still above the 0.5
        # threshold (so not exit 2), yet a Fisher-significant drift.
        (self.tmp / "data.jsonl").write_text(dataset(20, 20), encoding="utf-8")
        self.assertEqual(self.run_cli(), 0)
        (baseline,) = self.pack_dirs()
        (self.tmp / "outputs.jsonl").write_text(
            "\n".join(
                json.dumps({"id": f"i{i}", "output": "yes" if i < 12 else "no"})
                for i in range(20)
            )
            + "\n",
            encoding="utf-8",
        )
        cfg = CONFIG.replace(
            "provider:\n  type: mock",
            "provider:\n  type: external\n  results_file: outputs.jsonl",
        )
        (self.tmp / "evidentry.yaml").write_text(cfg, encoding="utf-8")
        # without the flag: drift is reported in the pack but exit stays 0
        self.assertEqual(self.run_cli("--baseline", str(baseline)), 0)
        self.assertEqual(
            self.run_cli("--baseline", str(baseline), "--fail-on-drift"), 3
        )

    def test_threshold_failure_outranks_drift_exit(self):
        (self.tmp / "data.jsonl").write_text(dataset(20, 20), encoding="utf-8")
        self.assertEqual(self.run_cli(), 0)
        baseline = self.pack_dirs()[0]
        (self.tmp / "data.jsonl").write_text(dataset(2, 20), encoding="utf-8")
        rc = self.run_cli("--baseline", str(baseline), "--fail-on-drift")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
