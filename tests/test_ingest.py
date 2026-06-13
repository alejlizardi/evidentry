import json
import tempfile
import unittest
from pathlib import Path

from providence.cli import main as cli_main
from providence.ingest import (
    IngestError,
    ingest_inspect,
    ingest_promptfoo,
    write_ingested,
)

# Shapes pinned to the documented formats: promptfoo's version-3 summary
# (results.results[] rows with testIdx/promptIdx, prompt.raw,
# response.output) wrapped in the OutputFile envelope, and Inspect's JSON
# log (samples[] with id/epoch/input/target and the completion at
# output.choices[0].message.content).

PROMPTFOO_FILE = {
    "evalId": "eval-abc",
    "results": {
        "version": 3,
        "timestamp": "2026-06-11T00:00:00Z",
        "results": [
            {
                "testIdx": 0, "promptIdx": 0,
                "prompt": {"raw": "Summarize: memo A", "label": "p0"},
                "vars": {"doc": "memo A"},
                "response": {"output": "Summary of A"},
                "success": True,
            },
            {
                "testIdx": 1, "promptIdx": 0,
                "prompt": {"raw": "Summarize: memo B", "label": "p0"},
                "vars": {"doc": "memo B"},
                "response": {"output": {"structured": True}},
                "success": False,
            },
            {
                "testIdx": 2, "promptIdx": 0,
                "prompt": {"raw": "Summarize: memo C", "label": "p0"},
                "vars": {"doc": "memo C"},
                "response": {"error": "provider timeout"},
                "success": False,
            },
        ],
        "prompts": [],
        "stats": {},
    },
    "config": {},
}

INSPECT_FILE = {
    "version": 2,
    "status": "success",
    "eval": {"task": "demo"},
    "samples": [
        {
            "id": "s1", "epoch": 1,
            "input": "What is 2+2?",
            "target": "4",
            "output": {
                "model": "m",
                "choices": [{"message": {"role": "assistant", "content": "It is 4."}}],
            },
        },
        {
            "id": "s2", "epoch": 1,
            "input": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": [{"type": "text", "text": "Name the US central bank."}]},
            ],
            "target": ["Federal Reserve", "the Fed"],
            "output": {
                "model": "m",
                "choices": [
                    {"message": {"role": "assistant",
                                 "content": [{"type": "text", "text": "The Federal Reserve."}]}}
                ],
            },
        },
        {
            "id": "s3", "epoch": 1,
            "input": "Errored sample",
            "target": "x",
            "output": {"model": "m", "choices": [], "error": "refused"},
        },
    ],
}


class TestPromptfooIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "results.json"
        self.src.write_text(json.dumps(PROMPTFOO_FILE), encoding="utf-8")

    def test_basic_extraction(self):
        dataset, outputs, info = ingest_promptfoo(self.src)
        self.assertEqual(info["n_items"], 2)
        self.assertEqual(dataset[0], {"id": "test-0", "input": "Summarize: memo A"})
        self.assertEqual(outputs[0], {"id": "test-0", "output": "Summary of A"})

    def test_non_string_output_is_json_dumped(self):
        _, outputs, _ = ingest_promptfoo(self.src)
        self.assertEqual(outputs[1]["output"], '{"structured": true}')

    def test_errored_items_skipped_loudly(self):
        _, _, info = ingest_promptfoo(self.src)
        self.assertEqual(info["skipped_errored"], ["test-2"])

    def test_bare_summary_without_envelope(self):
        self.src.write_text(json.dumps(PROMPTFOO_FILE["results"]), encoding="utf-8")
        _, _, info = ingest_promptfoo(self.src)
        self.assertEqual(info["n_items"], 2)

    def test_unsupported_version_rejected(self):
        bad = json.loads(json.dumps(PROMPTFOO_FILE))
        bad["results"]["version"] = 2
        self.src.write_text(json.dumps(bad), encoding="utf-8")
        with self.assertRaises(IngestError):
            ingest_promptfoo(self.src)

    def test_multi_prompt_requires_choice(self):
        multi = json.loads(json.dumps(PROMPTFOO_FILE))
        extra = json.loads(json.dumps(multi["results"]["results"][0]))
        extra["promptIdx"] = 1
        extra["prompt"]["label"] = "p1"
        multi["results"]["results"].append(extra)
        self.src.write_text(json.dumps(multi), encoding="utf-8")
        with self.assertRaises(IngestError):
            ingest_promptfoo(self.src)
        dataset, _, _ = ingest_promptfoo(self.src, prompt_idx=1)
        self.assertEqual(len(dataset), 1)


class TestInspectIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "log.json"
        self.src.write_text(json.dumps(INSPECT_FILE), encoding="utf-8")

    def test_basic_extraction(self):
        dataset, outputs, info = ingest_inspect(self.src)
        self.assertEqual(info["n_items"], 2)
        self.assertEqual(dataset[0], {"id": "s1", "input": "What is 2+2?", "expected": "4"})
        self.assertEqual(outputs[0], {"id": "s1", "output": "It is 4."})

    def test_message_input_and_content_parts(self):
        dataset, outputs, _ = ingest_inspect(self.src)
        self.assertIn("system: Be brief.", dataset[1]["input"])
        self.assertIn("user: Name the US central bank.", dataset[1]["input"])
        self.assertEqual(outputs[1]["output"], "The Federal Reserve.")

    def test_list_target_carried_with_warning(self):
        dataset, _, info = ingest_inspect(self.src)
        self.assertEqual(dataset[1]["expected"], ["Federal Reserve", "the Fed"])
        self.assertTrue(any("list targets" in n for n in info["notes"]))

    def test_errored_sample_skipped(self):
        _, _, info = ingest_inspect(self.src)
        self.assertEqual(info["skipped_errored"], ["s3"])

    def test_multi_epoch_ids_suffixed(self):
        multi = json.loads(json.dumps(INSPECT_FILE))
        clone = json.loads(json.dumps(multi["samples"][0]))
        clone["epoch"] = 2
        multi["samples"].append(clone)
        self.src.write_text(json.dumps(multi), encoding="utf-8")
        dataset, _, info = ingest_inspect(self.src)
        ids = [r["id"] for r in dataset]
        self.assertIn("s1#e1", ids)
        self.assertIn("s1#e2", ids)
        self.assertTrue(any("epoch" in n for n in info["notes"]))

    def test_eval_binary_refused_with_dump_hint(self):
        with self.assertRaises(IngestError) as ctx:
            ingest_inspect(self.tmp / "log.eval")
        self.assertIn("inspect log dump", str(ctx.exception))

    def test_missing_samples_rejected(self):
        self.src.write_text(json.dumps({"version": 2, "status": "success"}), encoding="utf-8")
        with self.assertRaises(IngestError):
            ingest_inspect(self.src)


class TestWriteAndEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_refuses_overwrite_without_force(self):
        dataset = [{"id": "1", "input": "x"}]
        outputs = [{"id": "1", "output": "y"}]
        write_ingested(dataset, outputs, self.tmp)
        with self.assertRaises(IngestError):
            write_ingested(dataset, outputs, self.tmp)
        write_ingested(dataset, outputs, self.tmp, force=True)

    def test_cli_ingest_then_run_builds_a_pack(self):
        src = self.tmp / "log.json"
        src.write_text(json.dumps(INSPECT_FILE), encoding="utf-8")
        rc = cli_main(["ingest", "inspect", str(src), "-o", str(self.tmp)])
        self.assertEqual(rc, 0)
        config = f"""\
model:
  name: ingested-model
  version: "1.0"
  use_case: testing
  owner: tests
  materiality_tier: 3
  tier_rationale: synthetic
provider:
  type: external
  results_file: outputs.jsonl
suites:
  - name: ingested
    dataset: dataset.jsonl
    metric: contains
    threshold: 0.5
report:
  mappings: [sr-26-2]
  out_dir: evidence
"""
        # drop the list-target item: `contains` requires ALL substrings and
        # that judgment belongs to the user, as the ingest note says
        rows = [
            json.loads(line)
            for line in (self.tmp / "dataset.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        kept = [r for r in rows if not isinstance(r.get("expected"), list)]
        with (self.tmp / "dataset.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
            for r in kept:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        outputs_rows = [
            json.loads(line)
            for line in (self.tmp / "outputs.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual({r["id"] for r in outputs_rows} >= {r["id"] for r in kept}, True)
        (self.tmp / "providence.yaml").write_text(config, encoding="utf-8")
        rc = cli_main(["run", "-c", str(self.tmp / "providence.yaml")])
        self.assertIn(rc, (0, 2))
        packs = list((self.tmp / "evidence").iterdir())
        self.assertEqual(len(packs), 1)


if __name__ == "__main__":
    unittest.main()
