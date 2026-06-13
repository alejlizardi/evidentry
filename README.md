# providence

[![CI](https://github.com/alejlizardi/providence/actions/workflows/ci.yml/badge.svg)](https://github.com/alejlizardi/providence/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) ![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

**Turn LLM eval runs into auditable evidence packs with defensible statistics.**

Most eval results are reported as a bare pass rate, regenerated ad hoc, and pasted into a doc. That's fine for iteration; it's not fine the moment someone — a reviewer, a customer's risk team, an auditor, or you in six months — asks *"how do you know, and can you show your work?"*

Eval frameworks measure your model. providence packages the measurement as evidence:

```
evals in  ──►  providence  ──►  versioned evidence pack out
                               ├─ report.md      (validation report, with stats and gap table)
                               ├─ results.json   (every item, every run)
                               └─ manifest.json  (SHA-256 of config, datasets, artifacts)
```

- **Define** a model card and acceptance thresholds in YAML.
- **Run** evals against Anthropic / OpenAI-compatible APIs — or **ingest** pre-computed outputs from your own harness as a one-line-per-item JSONL (`provider: external`). providence is an evidence layer, not another eval framework. `providence ingest promptfoo results.json` / `providence ingest inspect log.json` converts those tools' output files into the ingestion pair for you — extracting raw outputs only, because providence re-scores them with its own metrics; it never imports another tool's scores or assertions. (No DeepEval adapter yet — its export format isn't documented stably enough to pin.)
- **Emit** a versioned evidence pack: pass rates with Wilson 95% confidence intervals, interval-aware verdicts, sample-size certificates for unsettled verdicts, exact run-over-run drift tests with multiplicity control, and an optional requirement-coverage table mapped to governance frameworks — including an explicit list of what is *not* evidenced.
- **Verify** any pack later: `providence verify` recomputes every hash, catching accidental modification or corruption. (Integrity, not provenance: packs are unsigned, so this does not stop a determined forger — see roadmap.)
- **Export** a version-ordered series of packs to frontend-ready static JSON with `providence export` — `index.json`, each pack's `results.json`, and a `drift.json` timeline (Fisher + Holm across consecutive pairs). This feeds the dashboard below.

> **See it rendered:** [**providence-dashboard**](https://github.com/alejlizardi/providence-dashboard) ([live](https://alejlizardi.github.io/providence-dashboard/)) is a React dashboard for these packs — Wilson intervals as error bars, the settled-vs-`(point)` distinction shown visually, and the Holm-significant drift event highlighted. The statistics in [`stats.py`](providence/stats.py), made visible.

## The statistics are the point

A suite is marked **PASS** only when the *lower* Wilson confidence bound clears its threshold. A point estimate that clears it on a small sample gets **PASS (point)** — the report tells you when your evidence is too thin to be settled, which is exactly what a reviewer needs to know. Unsettled verdicts come with a **sample-size certificate**: how many more items it would take to settle them, by exact binomial power at the observed rate.

Run-over-run changes get **Fisher's exact test** instead of an eyeball comparison — exact at any sample size, including the 4-to-8-item suites real configs have, where the textbook z-test flags spurious drift. When several suites are monitored at once, p-values are **Holm-adjusted** so the family-wise false-drift rate stays at α. And a drift row is only computed when the runs are actually comparable (same dataset bytes, metric, and runs-per-item) — otherwise it is flagged **NOT COMPARABLE** rather than dressed up as a p-value.

Items that share a source (several questions about the same document, scenarios from the same template) are not independent evidence. Give them a `cluster` field and the interval and verdict use a **cluster-adjusted effective sample size** (one-way cluster-robust variance → design effect, with a t critical value carrying G−1 degrees of freedom because the variance is estimated from G clusters), so correlation widens your intervals instead of silently flattering them. Known limit, in the open: with very few clusters and high intra-cluster correlation even the adjusted interval under-covers somewhat — the fix is more clusters, not more items per cluster. `runs: N` repeats each item; an item passes only if every run passes, so output instability shows up as failure instead of luck.

Two opt-in variants, each with its trade stated (`statistics:` block in the config):

- **Strict mode** (`intervals: clopper_pearson`): exact-conservative Clopper–Pearson intervals for verdicts and certificates — coverage guaranteed ≥95% at *every* sample size and true rate (verified exactly in the validation study: min coverage 0.9534), at the price of systematically wider intervals and larger sample-size certificates. The guarantee is for independent items only, so clustered suites honestly fall back to the cluster-adjusted interval and the report says so.
- **Mid-p drift testing** (`drift_test: fisher_midp`): recovers a real share of the power Fisher's exactness sacrifices to discreteness (e.g. 0.22 → 0.33 at 8 items per run against a 0.9 → 0.5 drift, computed exactly). The honest cost: mid-p's false-alarm rate is approximately nominal on average but not guaranteed — its worst case reaches 0.057 at n = 50. That number is why plain Fisher stays the default.

What the statistics honestly mean — and don't: the intervals quantify sampling uncertainty *on your dataset*. They do not certify field performance on a different input distribution, and `verify` proves integrity (the bytes haven't changed since the pack was built), not provenance (packs are not yet signed — see roadmap).

## Quickstart

```bash
pip install providence
providence init my-model   # scaffold config + sample dataset
cd my-model
providence run             # works out of the box with the mock provider
```

Or run the worked example — a fictional bank validating a credit-memo summarizer (no API key needed; the mock provider makes it fully deterministic):

```bash
cd examples/credit_memo_summarizer
providence run -c providence.yaml
providence verify evidence/credit-memo-summarizer-v1.2.0-*
```

The committed sample output is in [`examples/credit_memo_summarizer/evidence/`](examples/credit_memo_summarizer/evidence/) — including a **failing** numeric-extraction suite and a use-limit violation, because an evidence tool you only see passing is a demo, not evidence. A second example, [`examples/judged_faithfulness/`](examples/judged_faithfulness/), scores a graded quality with a two-judge panel that genuinely disagrees. A third, [`examples/model_history/`](examples/model_history/), tracks one model across four versions on a fixed dataset — one release trips a Holm-significant drift event — and is the series the dashboard renders.

## What a pack asserts

| Question a reviewer asks | Artifact |
|---|---|
| What is this system, who owns it, why this risk tier? | model card + tier rationale |
| How was it tested, against what thresholds? | outcomes analysis with 95% CIs |
| Is the sample large enough to settle the verdict? | PASS vs PASS (point) distinction + sample-size certificate |
| Has it changed since last validation? | Fisher's exact test vs. baseline pack, Holm-adjusted, with dataset-parity checks |
| Are these the same results that were produced then? | manifest pins SHA-256 of config + datasets + results |
| What *isn't* covered? | requirement gap table (`NOT EVIDENCED` rows) |

## Metrics

`exact_match`, `contains` (all substrings), `regex`, `numeric` (tolerance-based, for extracted figures), `refusal` (use-limit controls: *the summarizer must decline to give investment advice* is a control that needs evidence like everything else). These cover deterministic, checkable properties. Graded qualities (faithfulness, tone) use the `judge` metric — see the next section, because a judge's verdict is a measurement, not ground truth.

## LLM-as-judge, with the disagreement in the open

For qualities no regex can check, a suite can be scored by a panel of LLM judges (`metric: judge`): you write a rubric, name two or more judges (Anthropic / OpenAI-compatible, pre-computed verdicts via `external`, or `mock` for deterministic runs), and pick an explicit decision rule — `unanimous` (default: disagreement counts against the item, the same way instability across repeated runs does) or `majority`.

The judges are part of the measurement instrument, so the pack carries evidence about *them* too:

- **Panel agreement gets the same settledness semantics as a pass rate** — a Wilson interval, and (if you set `min_agreement`) a PASS / PASS (point) style verdict on the agreement rate itself.
- **Pairwise Cohen's κ**, because raw agreement flatters judge pairs with extreme base rates: two judges who each pass ~95% of items agree ~90% of the time by chance alone.
- **Judge sensitivity:** the suite verdict is recomputed under each judge alone. If the result flips with the choice of judge, the report says **JUDGE-DEPENDENT** — one judge's opinion is never laundered into a fact about the model.
- **Unparseable judge responses are recorded as `invalid`**, counted against agreement and the item, and reported — never silently coerced to a verdict.
- Every judge's raw response is stored in the pack, so a reviewer can audit the judge, not just the judged.

The worked example in [`examples/judged_faithfulness/`](examples/judged_faithfulness/) shows all of this on a faithfulness suite where the judges genuinely disagree. A single-judge panel is allowed, but the report states in bold that it produces no disagreement evidence. Known limits: judge *self*-consistency (the same judge re-asked) is not yet modeled — judge suites are restricted to `runs: 1` rather than modeling correlated judging events wrong — and κ is reported as a point estimate without an interval.

Two deterministic metrics deserve their caveats in the open. `numeric` refuses to guess: if an output contains more than one number, the item *fails with an explanation* unless you set an explicit extraction rule (`first` / `last` / `any`) — a silent wrong guess feeding a confidence interval is exactly the failure an evidence tool exists to prevent. `refusal` is a transparent lexical heuristic (the patterns are ~10 lines of `metrics.py`); it distinguishes "I can't make credit decisions" from "I can't believe this stock", but it is not a semantic classifier — audit the item-level details when a use-limit verdict matters.

## CI integration

`providence run` is built to gate builds. Exit codes: **0** all suites at or above threshold, **2** a suite's verdict is FAIL / FAIL (point), **3** thresholds held but `--fail-on-drift` found a significant regression vs the baseline pack (Holm-adjusted across suites, so monitoring many suites doesn't fail builds at an inflated family-wise rate). A minimal GitHub Actions job:

```yaml
- name: Eval evidence gate
  run: |
    pip install providence
    providence run -c providence.yaml --baseline evidence/approved-baseline --fail-on-drift
- name: Upload evidence pack
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: evidence-pack
    path: evidence/
```

Commit an approved pack as the baseline (drift rows are only computed when dataset bytes, metric, and runs-per-item match — anything else is `NOT COMPARABLE`, not a p-value), and upload the fresh pack as an artifact so every build leaves auditable evidence whether it passed or not.

## Framework mappings — read this before using them

Packs can include requirement-coverage tables for **SR 26-2** (US interagency model risk guidance, April 2026) and **EU AI Act Annex IV**. Three facts you should know, from the primary sources:

- **SR 26-2 explicitly excludes generative and agentic AI from its scope** (footnote 3 of the guidance) and is **non-binding** ("non-compliance with this guidance will not result in supervisory criticism"). Its principles apply directly to traditional statistical models and non-generative AI. Using its structure for an LLM system — as the worked example does — is an *analogy*: organizing evidence around principles a bank already applies elsewhere, ahead of the GenAI-specific guidance the agencies have signaled. The mapping file says this in its header, with quotes.
- **EU AI Act high-risk documentation obligations are not yet in force** (expected Dec 2027 / Aug 2028 for most systems, post-Omnibus).
- The mappings are **interpretations** that structure evidence. They are not the guidance text, not legal advice, and not a substitute for independent validation. Requirements that need human judgment (conceptual soundness review, effective challenge, governance) are deliberately surfaced as gaps rather than papered over.

## Scope, honestly

providence covers outcomes-analysis-style evidence for text-in/text-out systems, scored by deterministic metrics or LLM-judge panels with disagreement reporting. Not yet covered: fairness testing, multi-turn agent traces, tool-call audit, pack signing, judge self-consistency modeling.

## Roadmap

- **Pack signing + trusted timestamps**, so integrity holds against tampering, not just accidents
- **Judge self-consistency** (the same judge re-asked; a judge that always agrees with itself is not the same as a judge that's right) and intervals on κ
- **DeepEval format adapter** (promptfoo and Inspect shipped via `providence ingest`; DeepEval needs a stably documented export format to pin against)
- Agent-trace evidence (multi-turn, tool calls)
- More mappings (NIST AI RMF, ISO/IEC 42001)

## Authors

Built by Alejandro Lizardi and John Dryden as part of Periapsis, working on statistically honest evaluation evidence for AI systems.

MIT licensed. Issues and war stories from your own eval reviews are very welcome.
