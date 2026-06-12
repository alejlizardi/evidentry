"""Run eval suites against a provider and produce structured results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import Config, SuiteConfig
from .judges import consensus, judge_evidence, make_judges
from .metrics import score
from .providers import Provider, make_provider
from .stats import sample_size_certificate, threshold_verdict


def load_dataset(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if "id" not in item or "input" not in item:
                raise ValueError(f"{path}:{lineno}: every item needs 'id' and 'input'")
            item_id = str(item["id"])
            if item_id in seen_ids:
                raise ValueError(f"{path}:{lineno}: duplicate item id '{item_id}'")
            seen_ids.add(item_id)
            items.append(item)
    if not items:
        raise ValueError(f"Dataset is empty: {path}")
    return items


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_suite(suite: SuiteConfig, provider: Provider, base_dir: Path) -> dict[str, Any]:
    dataset_path = base_dir / suite.dataset
    items = load_dataset(dataset_path)
    judges = make_judges(suite.judge, base_dir) if suite.judge is not None else None

    item_results: list[dict[str, Any]] = []
    passes = 0
    for item in items:
        runs: list[dict[str, Any]] = []
        run_passes = 0
        for run_idx in range(suite.runs):
            output = provider.complete(item)
            if judges is not None:
                # Every judge's verdict and raw response goes into the pack:
                # the judges are part of the measurement instrument, and a
                # reviewer must be able to audit them, not just the judged.
                judged = []
                for judge in judges:
                    verdict, raw = judge.judge(item, output, suite.judge.rubric)
                    judged.append(
                        {"judge": judge.spec.name, "verdict": verdict, "response": raw}
                    )
                passed = consensus([j["verdict"] for j in judged], suite.judge.decision)
                detail = (
                    f"{suite.judge.decision}: "
                    + ", ".join(f"{j['judge']}={j['verdict']}" for j in judged)
                )
                run_row = {
                    "run": run_idx + 1,
                    "output": output,
                    "passed": passed,
                    "detail": detail,
                    "judges": judged,
                }
            else:
                passed, detail = score(
                    suite.metric, output, item.get("expected"), suite.metric_options
                )
                run_row = {"run": run_idx + 1, "output": output, "passed": passed, "detail": detail}
            run_passes += int(passed)
            runs.append(run_row)
        # An item passes only if every repetition passes: consistency is part
        # of the evidence when runs > 1.
        item_passed = run_passes == suite.runs
        passes += int(item_passed)
        result_row = {
            "id": str(item["id"]),
            "input": str(item["input"]),
            "expected": item.get("expected"),
            "passed": item_passed,
            "runs": runs,
        }
        if "cluster" in item:
            result_row["cluster"] = str(item["cluster"])
        item_results.append(result_row)

    # If any item declares a cluster (items sharing a source document,
    # scenario, or template), the interval and verdict must respect that
    # correlation. Items without a cluster count as their own cluster.
    passed_flags = [it["passed"] for it in item_results]
    clusters = None
    if any("cluster" in it for it in item_results):
        clusters = [it.get("cluster", f"__solo_{it['id']}") for it in item_results]
    verdict = threshold_verdict(
        passes, len(items), suite.threshold, item_passed=passed_flags, clusters=clusters
    )
    if verdict["verdict"].endswith("(point)"):
        # Unsettled verdict: attach the exact-binomial sample-size
        # certificate. Under clustering it is computed on the effective
        # sample size, so it is approximate (rounded to whole items).
        if clusters is not None:
            n_plan = max(1, round(verdict["n_eff"]))
            s_plan = min(n_plan, round(verdict["pass_rate"] * n_plan))
            cert = sample_size_certificate(s_plan, n_plan, suite.threshold)
            cert["approximate_under_clustering"] = True
        else:
            cert = sample_size_certificate(passes, len(items), suite.threshold)
        verdict["sample_size_certificate"] = cert
    extra: dict[str, Any] = {}
    if suite.judge is not None:
        extra["judge_evidence"] = judge_evidence(
            suite.judge, item_results, suite.threshold, clusters=clusters
        )
    return {
        "suite": suite.name,
        "description": suite.description,
        "metric": suite.metric,
        "metric_options": suite.metric_options,
        "runs_per_item": suite.runs,
        "requirement_ids": suite.requirement_ids,
        "dataset": suite.dataset,
        "dataset_sha256": file_sha256(dataset_path),
        "n_items": len(items),
        "n_passed": passes,
        **verdict,
        **extra,
        "items": item_results,
    }


def run_all(config: Config) -> dict[str, Any]:
    provider = make_provider(config.provider, config.base_dir)
    suites = [run_suite(s, provider, config.base_dir) for s in config.suites]
    return {
        "model": config.model.to_dict(),
        "provider": provider.describe(),
        "config_sha256": config.canonical_hash(),
        "suites": suites,
        "summary": {
            "total_suites": len(suites),
            "suites_passed": sum(1 for s in suites if s["verdict"].startswith("PASS")),
            "total_items": sum(s["n_items"] for s in suites),
            "total_passed": sum(s["n_passed"] for s in suites),
        },
    }
