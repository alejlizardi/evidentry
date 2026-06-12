"""LLM-as-judge scoring with disagreement evidence.

A judge's verdict is a measurement, not ground truth — judge reliability is
its own evidence problem. This module therefore treats *disagreement* as a
first-class output: every judge's verdict (and raw response) is stored in
the pack, the panel's agreement rate gets the same Wilson-interval
settledness semantics as a pass rate, pairwise agreement is chance-corrected
(Cohen's kappa), and the suite verdict is recomputed under each judge alone
so the report can say when the headline result depends on which judge you
believe.

Honesty rules, in code:

- A judge response with no parseable verdict is recorded as ``invalid`` —
  never silently coerced to pass or fail. Invalid responses are reliability
  evidence and are counted in the report.
- ``unanimous`` (the default decision rule) means disagreement counts
  against the item, consistent with the runs-per-item rule that instability
  is failure. ``majority`` requires a strict majority of the panel to say
  pass; ties and invalid responses count against.
- A single-judge panel is allowed but produces no disagreement evidence,
  and the report says so in bold.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import JudgeConfig, JudgeSpec, ProviderConfig
from .providers import AnthropicProvider, OpenAIProvider
from .stats import clopper_pearson_interval, cohen_kappa, threshold_verdict, wilson_interval

JUDGE_PROMPT = """\
You are evaluating whether a model output satisfies a quality criterion.

Criterion:
{rubric}

Input given to the model:
{input}
{reference}\
Model output to evaluate:
{output}

Judge only the criterion above; do not reward or penalize anything else.
End your reply with exactly one line:
VERDICT: PASS
or
VERDICT: FAIL"""

_VERDICT_RE = re.compile(r"VERDICT:\s*(PASS|FAIL)\b", re.IGNORECASE)

VALID_VERDICTS = ("pass", "fail")


def build_judge_prompt(rubric: str, item: dict[str, Any], output: str) -> str:
    """The exact prompt a live judge sees; kept simple and inspectable."""
    reference = ""
    if item.get("expected") is not None:
        reference = f"\nReference (expected) answer:\n{item['expected']}\n\n"
    return JUDGE_PROMPT.format(
        rubric=rubric, input=item.get("input", ""), reference=reference, output=output
    )


def parse_verdict(text: str) -> str:
    """'pass' | 'fail' | 'invalid'. The last VERDICT line wins (judges that
    deliberate before deciding put it at the end); anything unparseable is
    'invalid', never a guess."""
    matches = _VERDICT_RE.findall(text or "")
    if not matches:
        return "invalid"
    return matches[-1].lower()


class MockJudge:
    """Deterministic judge for tests and worked examples: reads the verdict
    from the item's own ``mock_judges`` map, e.g.
    ``{"mock_judges": {"strict-judge": "pass", "lenient-judge": "fail"}}``.
    A missing or unrecognized entry is 'invalid' with an explanation."""

    def __init__(self, spec: JudgeSpec):
        self.spec = spec

    def judge(self, item: dict[str, Any], output: str, rubric: str) -> tuple[str, str]:
        verdicts = item.get("mock_judges", {})
        if self.spec.name not in verdicts:
            return "invalid", f"(no mock_judges entry for judge '{self.spec.name}')"
        raw = str(verdicts[self.spec.name])
        v = raw.strip().lower()
        return (v if v in VALID_VERDICTS else "invalid"), raw


class ExternalJudge:
    """Pre-computed judge verdicts from a JSONL file of {"id", "verdict"}
    rows — the ingestion path for judging done by your own harness. A
    missing row is a wiring error and raises; a row whose verdict is not
    pass/fail is recorded as 'invalid' (that is reliability evidence)."""

    def __init__(self, spec: JudgeSpec, base_dir: Path):
        self.spec = spec
        path = base_dir / spec.results_file
        if not path.exists():
            raise FileNotFoundError(
                f"Judge '{spec.name}': verdicts file not found: {path}"
            )
        self.verdicts: dict[str, str] = {}
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                self.verdicts[str(row["id"])] = str(row["verdict"])

    def judge(self, item: dict[str, Any], output: str, rubric: str) -> tuple[str, str]:
        item_id = str(item.get("id"))
        if item_id not in self.verdicts:
            raise KeyError(
                f"Judge '{self.spec.name}': no external verdict for item id '{item_id}'"
            )
        raw = self.verdicts[item_id]
        v = raw.strip().lower()
        return (v if v in VALID_VERDICTS else "invalid"), raw


class LiveJudge:
    """A live model judging through the same provider adapters used for the
    system under test. Builds the rubric prompt, sends it, parses the
    VERDICT line; the raw response is stored in the pack so a reviewer can
    audit the judge, not just the judged."""

    def __init__(self, spec: JudgeSpec):
        self.spec = spec
        pcfg = ProviderConfig(type=spec.type, model_id=spec.model_id, options=spec.options)
        if spec.type == "anthropic":
            self.backend: Any = AnthropicProvider(pcfg)
        else:
            self.backend = OpenAIProvider(pcfg)

    def judge(self, item: dict[str, Any], output: str, rubric: str) -> tuple[str, str]:
        prompt = build_judge_prompt(rubric, item, output)
        raw = self.backend.complete({"input": prompt})
        return parse_verdict(raw), raw


def make_judges(cfg: JudgeConfig, base_dir: Path) -> list[Any]:
    panel: list[Any] = []
    for spec in cfg.judges:
        if spec.type == "mock":
            panel.append(MockJudge(spec))
        elif spec.type == "external":
            panel.append(ExternalJudge(spec, base_dir))
        else:
            panel.append(LiveJudge(spec))
    return panel


def consensus(verdicts: list[str], decision: str) -> bool:
    """Item verdict from per-judge verdicts. Invalid responses always count
    against — an unreadable judgment is not evidence of passing."""
    if decision == "unanimous":
        return all(v == "pass" for v in verdicts)
    if decision == "majority":
        return sum(v == "pass" for v in verdicts) * 2 > len(verdicts)
    raise ValueError(f"unknown decision rule: {decision!r}")


def judge_evidence(
    cfg: JudgeConfig,
    item_results: list[dict[str, Any]],
    threshold: float,
    clusters: list[str] | None = None,
    interval: str = "wilson",
) -> dict[str, Any]:
    """Suite-level judge-disagreement evidence, assembled from per-item
    verdicts (each item's first run carries a ``judges`` list; judge suites
    are restricted to runs: 1 by config validation).

    Three layers, weakest claim first:

    - **agreement**: the rate at which the whole panel returned the same
      valid verdict, with a Wilson CI — and, when ``min_agreement`` is
      configured, the same settledness verdict (PASS vs PASS (point)) a
      pass rate gets. Clustered items use the cluster-adjusted interval,
      same as the suite verdict.
    - **pairwise**: observed agreement and Cohen's kappa per judge pair,
      because raw agreement is inflated when both judges pass nearly
      everything.
    - **sensitivity**: the suite pass rate recomputed under each judge
      alone. If the point estimate's side of the threshold differs across
      judges, the headline verdict is judge-dependent and the report must
      say so.
    """
    names = [j.name for j in cfg.judges]
    n_items = len(item_results)
    # verdict matrix: per judge, the verdict for each item (runs == 1)
    by_judge: dict[str, list[str]] = {name: [] for name in names}
    for it in item_results:
        row = {jr["judge"]: jr["verdict"] for jr in it["runs"][0]["judges"]}
        for name in names:
            by_judge[name].append(row[name])

    invalid_by_judge = {
        name: sum(v == "invalid" for v in verdicts) for name, verdicts in by_judge.items()
    }
    n_invalid = sum(invalid_by_judge.values())

    out: dict[str, Any] = {
        "judges": [
            {"name": j.name, "type": j.type, "model_id": j.model_id} for j in cfg.judges
        ],
        "decision": cfg.decision,
        "rubric": cfg.rubric,
        "n_judges": len(names),
        "n_items": n_items,
        "n_invalid_responses": n_invalid,
        "invalid_by_judge": invalid_by_judge,
    }

    # Per-judge suite pass rates and verdict sensitivity. An invalid
    # verdict counts as a fail for that judge's rate, consistent with the
    # consensus rules.
    per_judge = []
    directions: set[bool] = set()
    for name in names:
        passes = sum(v == "pass" for v in by_judge[name])
        rate = passes / n_items if n_items else 0.0
        v = threshold_verdict(
            passes,
            n_items,
            threshold,
            item_passed=[x == "pass" for x in by_judge[name]],
            clusters=clusters,
            interval=interval,
        )
        directions.add(rate >= threshold)
        per_judge.append(
            {
                "name": name,
                "n_passed": passes,
                "pass_rate": rate,
                "verdict": v["verdict"],
            }
        )
    out["per_judge"] = per_judge
    out["judge_dependent"] = len(directions) > 1

    if len(names) < 2:
        out["agreement"] = None
        out["pairwise"] = []
        return out

    # Panel agreement: all judges returned the same *valid* verdict. An
    # item where any judge came back invalid is not an agreement, whatever
    # the other judges said.
    agree_flags = [
        all(v == by_judge[names[0]][i] for v in (by_judge[n][i] for n in names))
        and by_judge[names[0]][i] in VALID_VERDICTS
        for i in range(n_items)
    ]
    agree_count = sum(agree_flags)
    agreement: dict[str, Any] = {
        "rate": agree_count / n_items if n_items else 0.0,
        "n_agreed": agree_count,
    }
    if cfg.min_agreement is not None:
        agreement["settledness"] = threshold_verdict(
            agree_count,
            n_items,
            cfg.min_agreement,
            item_passed=agree_flags,
            clusters=clusters,
            interval=interval,
        )
        agreement["min_agreement"] = cfg.min_agreement
    else:
        ifn = clopper_pearson_interval if interval == "clopper_pearson" else wilson_interval
        low, high = ifn(agree_count, n_items)
        agreement["ci95_low"] = low
        agreement["ci95_high"] = high
    out["agreement"] = agreement

    pairwise = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            pairwise.append(
                {"judge_a": a, "judge_b": b, **cohen_kappa(by_judge[a], by_judge[b])}
            )
    out["pairwise"] = pairwise
    return out
