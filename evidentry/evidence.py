"""Build, verify, and compare hash-pinned evidence packs.

A pack is a directory:

    <out_dir>/<model>-v<version>-<pack_id[:12]>/
        results.json    raw structured results
        report.md       human-readable validation report
        manifest.json   hashes of everything, written last

The pack_id commits to the config hash, every dataset hash, and the results
file hash. `verify` recomputes every hash, which catches accidental
modification and corruption. It is NOT tamper-proof: packs are
self-referential and unsigned, so an adversary who can rewrite the manifest
can re-pin altered contents. Treat `verify` as an integrity check, not a
provenance proof — signing is on the roadmap.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config
from .report import render_markdown
from .runner import file_sha256
from .stats import drift_test, holm_adjust


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_pack_id(config_sha256: str, dataset_hashes: dict[str, str], results_sha256: str) -> str:
    blob = json.dumps(
        {
            "config": config_sha256,
            "datasets": dict(sorted(dataset_hashes.items())),
            "results": results_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256_text(blob)


def compare_packs(
    baseline_dir: Path, results: dict[str, Any], alpha: float = 0.05
) -> dict[str, Any] | None:
    """Drift comparison between a baseline pack and current results.

    A suite is only compared when it is actually comparable: same dataset
    bytes (dataset_sha256), same metric, same runs-per-item. Anything else
    would produce a rigorous-looking p-value on a meaningless comparison,
    so those rows are flagged instead of tested. p-values are Fisher's
    exact (two-sided) and Holm-adjusted across the comparable suites, so
    monitoring many suites at once doesn't manufacture false drift.
    """
    baseline_results_path = Path(baseline_dir) / "results.json"
    if not baseline_results_path.exists():
        raise FileNotFoundError(f"No results.json in baseline pack: {baseline_dir}")
    baseline = json.loads(baseline_results_path.read_text(encoding="utf-8"))
    by_name = {s["suite"]: s for s in baseline["suites"]}
    rows = []
    for s in results["suites"]:
        prior = by_name.get(s["suite"])
        if prior is None:
            continue
        row: dict[str, Any] = {
            "suite": s["suite"],
            "rate_a": (prior["n_passed"] / prior["n_items"]) if prior["n_items"] else 0.0,
            "rate_b": (s["n_passed"] / s["n_items"]) if s["n_items"] else 0.0,
            "p_value": None,
            "p_holm": None,
            "significant": False,
        }
        reasons = []
        if prior.get("dataset_sha256") != s.get("dataset_sha256"):
            reasons.append("dataset changed")
        if prior.get("metric") != s.get("metric"):
            reasons.append("metric changed")
        if prior.get("runs_per_item") != s.get("runs_per_item"):
            reasons.append("runs_per_item changed")
        if reasons:
            row["comparable"] = False
            row["reason"] = "; ".join(reasons)
        else:
            d = drift_test(prior["n_passed"], prior["n_items"], s["n_passed"], s["n_items"])
            row["comparable"] = True
            row["p_value"] = d.p_value
            row["method"] = d.method
        rows.append(row)
    if not rows:
        return None
    comparable = [r for r in rows if r["comparable"]]
    if comparable:
        adjusted = holm_adjust([r["p_value"] for r in comparable])
        for r, p_adj in zip(comparable, adjusted):
            r["p_holm"] = p_adj
            r["significant"] = p_adj < alpha
    return {
        "baseline_pack": Path(baseline_dir).name,
        "alpha": alpha,
        "method": "fisher_exact, holm_adjusted",
        "suites": rows,
    }


def build_pack(
    config: Config, results: dict[str, Any], baseline: Path | None = None
) -> Path:
    drift = compare_packs(baseline, results) if baseline else None

    results_text = json.dumps(results, indent=2, sort_keys=True)
    results_hash = _sha256_text(results_text)
    dataset_hashes = {s["dataset"]: s["dataset_sha256"] for s in results["suites"]}
    pack_id = compute_pack_id(results["config_sha256"], dataset_hashes, results_hash)

    model = results["model"]
    pack_name = f"{model['name']}-v{model['version']}-{pack_id[:12]}"
    pack_dir = (config.base_dir / config.out_dir / pack_name).resolve()
    pack_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, Any] = {
        "pack_id": pack_id,
        "evidentry_version": __version__,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "model": model,
        "config_sha256": results["config_sha256"],
        "dataset_sha256": dataset_hashes,
        "files": {"results.json": results_hash},
        "baseline_pack": str(baseline) if baseline else None,
    }

    report_text = render_markdown(results, config.mappings, manifest=manifest, drift=drift)
    manifest["files"]["report.md"] = _sha256_text(report_text)

    # newline="\n" keeps written bytes identical to the hashed strings on
    # Windows; otherwise \r\n translation breaks verification.
    (pack_dir / "results.json").write_text(results_text, encoding="utf-8", newline="\n")
    (pack_dir / "report.md").write_text(report_text, encoding="utf-8", newline="\n")
    (pack_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )
    return pack_dir


def verify_pack(pack_dir: str | Path) -> tuple[bool, list[str]]:
    """Recompute hashes and the pack id; report every mismatch found."""
    pack_dir = Path(pack_dir)
    problems: list[str] = []
    manifest_path = pack_dir / "manifest.json"
    if not manifest_path.exists():
        return False, [f"manifest.json not found in {pack_dir}"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    for rel_name, expected in manifest.get("files", {}).items():
        target = pack_dir / rel_name
        if not target.exists():
            problems.append(f"missing file: {rel_name}")
            continue
        actual = file_sha256(target)
        if actual != expected:
            problems.append(f"hash mismatch for {rel_name}: expected {expected[:16]}…, got {actual[:16]}…")

    results_path = pack_dir / "results.json"
    if results_path.exists():
        results_hash = file_sha256(results_path)
        recomputed = compute_pack_id(
            manifest.get("config_sha256", ""),
            manifest.get("dataset_sha256", {}),
            results_hash,
        )
        if recomputed != manifest.get("pack_id"):
            problems.append(
                f"pack_id mismatch: manifest says {str(manifest.get('pack_id'))[:16]}…, recomputed {recomputed[:16]}…"
            )

    return (not problems), problems
