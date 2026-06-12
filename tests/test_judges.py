import json
import tempfile
import unittest
from pathlib import Path

from evidentry.config import ConfigError, JudgeConfig, JudgeSpec, load_config
from evidentry.judges import (
    ExternalJudge,
    MockJudge,
    build_judge_prompt,
    consensus,
    judge_evidence,
    parse_verdict,
)
from evidentry.runner import run_all
from evidentry.stats import cohen_kappa

JUDGE_CONFIG = """\
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
  - name: judged
    dataset: data.jsonl
    metric: judge
    threshold: 0.75
    judge:
      rubric: "Output is faithful to the input."
      decision: unanimous
      min_agreement: 0.85
      judges:
        - name: a
          type: mock
        - name: b
          type: mock
report:
  mappings: [sr-26-2]
  out_dir: out
"""

# 10 items mirroring the worked example: 6 agreed-pass, 1 agreed-fail,
# 2 disagreements, 1 invalid. Judge a passes 9/10, judge b passes 6/10,
# so with threshold .75 the verdict direction flips between judges.
DATA_ROWS = []
for i in range(1, 7):
    DATA_ROWS.append(
        {"id": f"i{i}", "input": "x", "mock_response": "y",
         "mock_judges": {"a": "pass", "b": "pass"}}
    )
DATA_ROWS.append({"id": "i7", "input": "x", "mock_response": "y",
                  "mock_judges": {"a": "pass", "b": "fail"}})
DATA_ROWS.append({"id": "i8", "input": "x", "mock_response": "y",
                  "mock_judges": {"a": "pass", "b": "hmm, needs review"}})
DATA_ROWS.append({"id": "i9", "input": "x", "mock_response": "y",
                  "mock_judges": {"a": "fail", "b": "fail"}})
DATA_ROWS.append({"id": "i10", "input": "x", "mock_response": "y",
                  "mock_judges": {"a": "pass", "b": "fail"}})
DATA = "\n".join(json.dumps(r) for r in DATA_ROWS) + "\n"


class TestVerdictParsing(unittest.TestCase):
    def test_plain_verdicts(self):
        self.assertEqual(parse_verdict("VERDICT: PASS"), "pass")
        self.assertEqual(parse_verdict("verdict: fail"), "fail")

    def test_last_verdict_wins(self):
        text = "If taken literally VERDICT: FAIL, but on reflection...\nVERDICT: PASS"
        self.assertEqual(parse_verdict(text), "pass")

    def test_deliberation_then_verdict(self):
        text = "The summary preserves all figures. Nothing is added.\n\nVERDICT: PASS"
        self.assertEqual(parse_verdict(text), "pass")

    def test_unparseable_is_invalid_never_a_guess(self):
        for text in ("", "Looks fine to me.", "PASS", "VERDICT: MAYBE", None):
            self.assertEqual(parse_verdict(text), "invalid")

    def test_prompt_contains_rubric_input_output_and_reference(self):
        item = {"id": "1", "input": "the memo", "expected": "ref summary"}
        prompt = build_judge_prompt("be faithful", item, "the summary")
        for fragment in ("be faithful", "the memo", "the summary", "ref summary"):
            self.assertIn(fragment, prompt)
        no_ref = build_judge_prompt("be faithful", {"id": "1", "input": "x"}, "y")
        self.assertNotIn("Reference", no_ref)


class TestConsensus(unittest.TestCase):
    def test_unanimous(self):
        self.assertTrue(consensus(["pass", "pass"], "unanimous"))
        self.assertFalse(consensus(["pass", "fail"], "unanimous"))
        self.assertFalse(consensus(["pass", "invalid"], "unanimous"))

    def test_majority_strict_ties_fail(self):
        self.assertTrue(consensus(["pass", "pass", "fail"], "majority"))
        self.assertFalse(consensus(["pass", "fail"], "majority"))  # tie
        self.assertFalse(consensus(["pass", "invalid", "fail"], "majority"))
        self.assertTrue(consensus(["pass", "pass", "invalid"], "majority"))

    def test_unknown_rule_raises(self):
        with self.assertRaises(ValueError):
            consensus(["pass"], "plurality")


class TestMockAndExternalJudges(unittest.TestCase):
    def test_mock_judge_reads_item_verdicts(self):
        j = MockJudge(JudgeSpec(name="a", type="mock"))
        item = {"id": "1", "mock_judges": {"a": "pass"}}
        self.assertEqual(j.judge(item, "out", "rubric")[0], "pass")

    def test_mock_judge_missing_or_garbled_is_invalid(self):
        j = MockJudge(JudgeSpec(name="a", type="mock"))
        self.assertEqual(j.judge({"id": "1"}, "out", "r")[0], "invalid")
        item = {"id": "1", "mock_judges": {"a": "perhaps"}}
        self.assertEqual(j.judge(item, "out", "r")[0], "invalid")

    def test_external_judge_reads_file_and_raises_on_missing_id(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "v.jsonl").write_text(
            '{"id": "1", "verdict": "PASS"}\n{"id": "2", "verdict": "garbled"}\n',
            encoding="utf-8",
        )
        j = ExternalJudge(JudgeSpec(name="x", type="external", results_file="v.jsonl"), tmp)
        self.assertEqual(j.judge({"id": "1"}, "o", "r")[0], "pass")
        self.assertEqual(j.judge({"id": "2"}, "o", "r")[0], "invalid")
        with self.assertRaises(KeyError):
            j.judge({"id": "3"}, "o", "r")


class TestCohenKappa(unittest.TestCase):
    def test_hand_computed_value(self):
        # 9 valid pairs: a passes 8, b passes 6, they agree on 7.
        a = ["pass"] * 8 + ["fail"]
        b = ["pass"] * 6 + ["fail"] * 3
        # agreements: first 6 (pass/pass) + last (fail/fail) = 7
        out = cohen_kappa(a, b)
        self.assertEqual(out["n_pairs"], 9)
        self.assertAlmostEqual(out["observed_agreement"], 7 / 9)
        p_e = (8 / 9) * (6 / 9) + (1 / 9) * (3 / 9)
        self.assertAlmostEqual(out["kappa"], (7 / 9 - p_e) / (1 - p_e))

    def test_invalid_pairs_excluded(self):
        a = ["pass", "invalid", "pass"]
        b = ["pass", "pass", "fail"]
        out = cohen_kappa(a, b)
        self.assertEqual(out["n_pairs"], 2)

    def test_kappa_undefined_when_no_variation(self):
        out = cohen_kappa(["pass", "pass"], ["pass", "pass"])
        self.assertIsNone(out["kappa"])
        self.assertEqual(out["observed_agreement"], 1.0)

    def test_no_valid_pairs(self):
        out = cohen_kappa(["invalid"], ["pass"])
        self.assertEqual(out["n_pairs"], 0)
        self.assertIsNone(out["kappa"])

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            cohen_kappa(["pass"], ["pass", "fail"])


class TestJudgeRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "evidentry.yaml").write_text(JUDGE_CONFIG, encoding="utf-8")
        (self.tmp / "data.jsonl").write_text(DATA, encoding="utf-8")

    def run_suite(self):
        results = run_all(load_config(self.tmp / "evidentry.yaml"))
        return results["suites"][0]

    def test_unanimous_consensus_counts(self):
        suite = self.run_suite()
        self.assertEqual(suite["n_passed"], 6)
        self.assertEqual(suite["verdict"], "FAIL (point)")
        self.assertIn("sample_size_certificate", suite)

    def test_judge_evidence_block(self):
        je = self.run_suite()["judge_evidence"]
        self.assertEqual(je["n_judges"], 2)
        self.assertEqual(je["n_invalid_responses"], 1)
        self.assertEqual(je["invalid_by_judge"]["b"], 1)
        # agreement: 6 agreed-pass + 1 agreed-fail = 7/10; the invalid item
        # is not an agreement whatever the other judge said
        self.assertEqual(je["agreement"]["n_agreed"], 7)
        self.assertAlmostEqual(je["agreement"]["rate"], 0.7)
        self.assertEqual(je["agreement"]["min_agreement"], 0.85)
        self.assertIn("verdict", je["agreement"]["settledness"])

    def test_judge_dependence_flagged(self):
        je = self.run_suite()["judge_evidence"]
        rates = {p["name"]: p["pass_rate"] for p in je["per_judge"]}
        self.assertAlmostEqual(rates["a"], 0.9)
        self.assertAlmostEqual(rates["b"], 0.6)
        self.assertTrue(je["judge_dependent"])

    def test_pairwise_kappa_present(self):
        je = self.run_suite()["judge_evidence"]
        (pw,) = je["pairwise"]
        self.assertEqual(pw["n_pairs"], 9)
        self.assertAlmostEqual(pw["observed_agreement"], 7 / 9)

    def test_item_rows_carry_judge_verdicts_and_raw_responses(self):
        suite = self.run_suite()
        by_id = {it["id"]: it for it in suite["items"]}
        judged = by_id["i8"]["runs"][0]["judges"]
        verdicts = {j["judge"]: j["verdict"] for j in judged}
        self.assertEqual(verdicts, {"a": "pass", "b": "invalid"})
        raws = {j["judge"]: j["response"] for j in judged}
        self.assertEqual(raws["b"], "hmm, needs review")

    def test_majority_rule(self):
        cfg = JUDGE_CONFIG.replace("decision: unanimous", "decision: majority")
        (self.tmp / "evidentry.yaml").write_text(cfg, encoding="utf-8")
        suite = self.run_suite()
        # 2-judge majority needs both: same counts as unanimous here
        self.assertEqual(suite["n_passed"], 6)

    def test_single_judge_caveat_no_agreement_stats(self):
        item_results = [
            {"runs": [{"judges": [{"judge": "solo", "verdict": "pass"}]}]},
            {"runs": [{"judges": [{"judge": "solo", "verdict": "fail"}]}]},
        ]
        cfg = JudgeConfig(rubric="r", judges=[JudgeSpec(name="solo", type="mock")])
        je = judge_evidence(cfg, item_results, threshold=0.5)
        self.assertEqual(je["n_judges"], 1)
        self.assertIsNone(je["agreement"])
        self.assertEqual(je["pairwise"], [])
        self.assertFalse(je["judge_dependent"])


class TestJudgeConfigValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "data.jsonl").write_text(DATA, encoding="utf-8")

    def load(self, cfg_text):
        (self.tmp / "evidentry.yaml").write_text(cfg_text, encoding="utf-8")
        return load_config(self.tmp / "evidentry.yaml")

    def test_judge_metric_requires_judge_block(self):
        cfg = JUDGE_CONFIG.replace("""    judge:
      rubric: "Output is faithful to the input."
      decision: unanimous
      min_agreement: 0.85
      judges:
        - name: a
          type: mock
        - name: b
          type: mock
""", "")
        with self.assertRaises(ConfigError):
            self.load(cfg)

    def test_judge_block_invalid_without_judge_metric(self):
        cfg = JUDGE_CONFIG.replace("metric: judge", "metric: contains")
        with self.assertRaises(ConfigError):
            self.load(cfg)

    def test_judge_metric_requires_single_run(self):
        cfg = JUDGE_CONFIG.replace("threshold: 0.75", "threshold: 0.75\n    runs: 2")
        with self.assertRaises(ConfigError):
            self.load(cfg)

    def test_duplicate_judge_names_rejected(self):
        cfg = JUDGE_CONFIG.replace("- name: b", "- name: a")
        with self.assertRaises(ConfigError):
            self.load(cfg)

    def test_bad_decision_rejected(self):
        cfg = JUDGE_CONFIG.replace("decision: unanimous", "decision: plurality")
        with self.assertRaises(ConfigError):
            self.load(cfg)

    def test_external_judge_requires_results_file(self):
        cfg = JUDGE_CONFIG.replace("- name: b\n          type: mock",
                                   "- name: b\n          type: external")
        with self.assertRaises(ConfigError):
            self.load(cfg)

    def test_judge_block_changes_config_hash_only_for_judge_suites(self):
        h1 = self.load(JUDGE_CONFIG).canonical_hash()
        h2 = self.load(JUDGE_CONFIG.replace("min_agreement: 0.85", "min_agreement: 0.9")).canonical_hash()
        self.assertNotEqual(h1, h2)


if __name__ == "__main__":
    unittest.main()
