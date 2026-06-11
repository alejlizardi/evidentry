import tempfile
import unittest
from pathlib import Path

from evidentry.config import ConfigError, load_config

VALID = """\
model:
  name: m
  version: "1.0"
  use_case: u
  owner: o
  materiality_tier: 2
  tier_rationale: r
provider:
  type: mock
suites:
  - name: s1
    dataset: d.jsonl
    metric: contains
    threshold: 0.9
report:
  mappings: [sr-26-2]
"""


class TestConfig(unittest.TestCase):
    def _write(self, text: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        path = tmp / "evidentry.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_valid_config_loads(self):
        cfg = load_config(self._write(VALID))
        self.assertEqual(cfg.model.name, "m")
        self.assertEqual(cfg.suites[0].metric, "contains")
        self.assertEqual(cfg.mappings, ["sr-26-2"])
        self.assertEqual(cfg.out_dir, "evidence")

    def test_canonical_hash_is_stable(self):
        a = load_config(self._write(VALID)).canonical_hash()
        b = load_config(self._write(VALID)).canonical_hash()
        self.assertEqual(a, b)

    def test_hash_changes_with_content(self):
        a = load_config(self._write(VALID)).canonical_hash()
        b = load_config(self._write(VALID.replace('"1.0"', '"1.1"'))).canonical_hash()
        self.assertNotEqual(a, b)

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            load_config("does-not-exist.yaml")

    def test_bad_tier(self):
        with self.assertRaises(ConfigError):
            load_config(self._write(VALID.replace("materiality_tier: 2", "materiality_tier: 5")))

    def test_bad_metric(self):
        with self.assertRaises(ConfigError):
            load_config(self._write(VALID.replace("metric: contains", "metric: vibes")))

    def test_bad_threshold(self):
        with self.assertRaises(ConfigError):
            load_config(self._write(VALID.replace("threshold: 0.9", "threshold: 1.5")))

    def test_external_requires_results_file(self):
        with self.assertRaises(ConfigError):
            load_config(self._write(VALID.replace("type: mock", "type: external")))

    def test_duplicate_suite_names(self):
        dup = VALID + """\
  - name: s1
    dataset: d2.jsonl
    metric: contains
    threshold: 0.5
"""
        # YAML list continuation: rebuild with two suites of the same name.
        text = VALID.replace(
            "suites:\n  - name: s1",
            "suites:\n  - name: s1\n    dataset: d0.jsonl\n    metric: contains\n    threshold: 0.5\n  - name: s1",
        )
        with self.assertRaises(ConfigError):
            load_config(self._write(text))
        del dup


if __name__ == "__main__":
    unittest.main()
