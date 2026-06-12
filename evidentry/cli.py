"""evidentry command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_config
from .evidence import build_pack, compare_packs, verify_pack
from .runner import run_all

INIT_CONFIG = """\
# evidentry configuration — see https://github.com/<org>/evidentry
model:
  name: my-model
  version: "1.0.0"
  vendor: internal            # or the vendor name for third-party models
  use_case: "What this model is used for, by whom"
  owner: "Team or person accountable for the model"
  materiality_tier: 2         # 1 = highest materiality
  tier_rationale: >
    Why this tier: exposure, decision impact, customer reach. SR 26-2 expects
    the tiering itself to be evidenced.
  limitations:
    - "Known failure modes and conditions of use go here."

provider:
  type: mock                  # mock | external | anthropic | openai
  model_id: mock-model
  # results_file: outputs.jsonl   # required for type: external

suites:
  - name: accuracy
    description: "Core task accuracy against curated golden answers."
    dataset: dataset.jsonl
    metric: contains          # exact_match | contains | regex | numeric | refusal | judge
    threshold: 0.90
    runs: 1
    # For graded qualities, use an LLM judge panel with disagreement evidence:
    # metric: judge
    # judge:
    #   rubric: "Every factual claim in the output appears in the input."
    #   decision: unanimous   # or majority
    #   min_agreement: 0.90   # optional: settledness verdict on panel agreement
    #   judges:
    #     - name: judge-a
    #       type: anthropic   # mock | external | anthropic | openai
    #       model_id: claude-sonnet-4-6
    #     - name: judge-b
    #       type: openai
    #       model_id: gpt-4o-mini

# statistics:
#   intervals: wilson         # or clopper_pearson (strict mode: guaranteed
#                             # >=95% coverage at every n, wider intervals)
#   drift_test: fisher_exact  # or fisher_midp (more small-n power, size
#                             # ~alpha on average rather than guaranteed)

report:
  mappings: [sr-26-2]         # sr-26-2 | eu-ai-act-annex-iv
  out_dir: evidence
"""

INIT_DATASET = """\
{"id": "ex-1", "input": "What is 2+2?", "expected": "4", "mock_response": "The answer is 4."}
{"id": "ex-2", "input": "Name the US central bank.", "expected": "Federal Reserve", "mock_response": "The Federal Reserve."}
"""


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    config_path = target / "evidentry.yaml"
    dataset_path = target / "dataset.jsonl"
    if config_path.exists() and not args.force:
        print(f"Refusing to overwrite {config_path} (use --force)", file=sys.stderr)
        return 1
    config_path.write_text(INIT_CONFIG, encoding="utf-8")
    if not dataset_path.exists() or args.force:
        dataset_path.write_text(INIT_DATASET, encoding="utf-8")
    print(f"Initialized {config_path} and {dataset_path}")
    print("Next: edit them, then run `evidentry run -c evidentry.yaml`")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1
    results = run_all(config)
    baseline = Path(args.baseline).resolve() if args.baseline else None
    pack_dir = build_pack(config, results, baseline=baseline)
    summary = results["summary"]
    print(f"Evidence pack written: {pack_dir}")
    print(
        f"Suites passed: {summary['suites_passed']}/{summary['total_suites']} "
        f"(items: {summary['total_passed']}/{summary['total_items']})"
    )
    for s in results["suites"]:
        je = s.get("judge_evidence")
        marker = " [JUDGE-DEPENDENT]" if je and je.get("judge_dependent") else ""
        print(f"  - {s['suite']}: {s['verdict']} ({s['n_passed']}/{s['n_items']}){marker}")
    any_fail = any(s["verdict"].startswith("FAIL") for s in results["suites"])
    return 2 if any_fail else 0


def cmd_verify(args: argparse.Namespace) -> int:
    ok, problems = verify_pack(args.pack)
    if ok:
        print(f"OK: evidence pack verified ({args.pack})")
        return 0
    print(f"FAILED: evidence pack integrity problems in {args.pack}", file=sys.stderr)
    for p in problems:
        print(f"  - {p}", file=sys.stderr)
    return 1


def cmd_diff(args: argparse.Namespace) -> int:
    current_results = Path(args.current) / "results.json"
    if not current_results.exists():
        print(f"No results.json in {args.current}", file=sys.stderr)
        return 1
    results = json.loads(current_results.read_text(encoding="utf-8"))
    drift = compare_packs(Path(args.baseline), results)
    if drift is None:
        print("No overlapping suites between the two packs.", file=sys.stderr)
        return 1
    print(f"Drift vs baseline {args.baseline} ({drift['method']}):")
    any_drift = False
    for row in drift["suites"]:
        rates = f"{100 * row['rate_a']:.1f}% -> {100 * row['rate_b']:.1f}%"
        if not row.get("comparable"):
            print(f"  - {row['suite']}: {rates} [NOT COMPARABLE: {row['reason']}]")
            continue
        flag = "DRIFT" if row["significant"] else "stable"
        any_drift = any_drift or row["significant"]
        print(
            f"  - {row['suite']}: {rates} "
            f"(p={row['p_value']:.4f}, holm={row['p_holm']:.4f}) [{flag}]"
        )
    return 2 if any_drift else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evidentry",
        description="Turn LLM eval runs into auditable evidence packs with defensible statistics.",
    )
    parser.add_argument("--version", action="version", version=f"evidentry {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Scaffold an evidentry.yaml and sample dataset")
    p_init.add_argument("dir", nargs="?", default=".", help="Target directory")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing files")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="Run eval suites and build an evidence pack")
    p_run.add_argument("-c", "--config", default="evidentry.yaml")
    p_run.add_argument(
        "--baseline", help="Path to a prior evidence pack for drift comparison", default=None
    )
    p_run.set_defaults(func=cmd_run)

    p_verify = sub.add_parser("verify", help="Verify the integrity of an evidence pack")
    p_verify.add_argument("pack", help="Path to the evidence pack directory")
    p_verify.set_defaults(func=cmd_verify)

    p_diff = sub.add_parser("diff", help="Drift comparison between two evidence packs")
    p_diff.add_argument("baseline", help="Baseline pack directory")
    p_diff.add_argument("current", help="Current pack directory")
    p_diff.set_defaults(func=cmd_diff)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
