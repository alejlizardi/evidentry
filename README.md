# evidentry

[![CI](https://github.com/alejlizardi/evidentry/actions/workflows/ci.yml/badge.svg)](https://github.com/alejlizardi/evidentry/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) ![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)

**Turn LLM eval runs into auditable evidence packs with defensible statistics.**

Most eval results are reported as a bare pass rate, regenerated ad hoc, and pasted into a doc. That's fine for iteration; it's not fine the moment someone — a reviewer, a customer's risk team, an auditor, or you in six months — asks *"how do you know, and can you show your work?"*

Eval frameworks measure your model. evidentry packages the measurement as evidence:

```
evals in  ──►  evidentry  ──►  versioned evidence pack out
                               ├─ report.md      (validation report, with stats and gap table)
                               ├─ results.json   (every item, every run)
                               └─ manifest.json  (SHA-256 of config, datasets, artifacts)
```

- **Define** a model card and acceptance thresholds in YAML.
- **Run** evals against Anthropic / OpenAI-compatible APIs — or **ingest** pre-computed outputs from your own harness as a one-line-per-item JSONL (`provider: external`). evidentry is an evidence layer, not another eval framework. (There are no format adapters for DeepEval/Inspect/promptfoo yet — you export the raw outputs, evidentry re-scores them; it cannot consume those tools' own scores.)
- **Emit** a versioned evidence pack: pass rates with Wilson 95% confidence intervals, interval-aware verdicts, sample-size certificates for unsettled verdicts, exact run-over-run drift tests with multiplicity control, and an optional requirement-coverage table mapped to governance frameworks — including an explicit list of what is *not* evidenced.
- **Verify** any pack later: `evidentry verify` recomputes every hash, catching accidental modification or corruption. (Integrity, not provenance: packs are unsigned, so this does not stop a determined forger — see roadmap.)

## The statistics are the point

A suite is marked **PASS** only when the *lower* Wilson confidence bound clears its threshold. A point estimate that clears it on a small sample gets **PASS (point)** — the report tells you when your evidence is too thin to be settled, which is exactly what a reviewer needs to know. Unsettled verdicts come with a **sample-size certificate**: how many more items it would take to settle them, by exact binomial power at the observed rate.

Run-over-run changes get **Fisher's exact test** instead of an eyeball comparison — exact at any sample size, including the 4-to-8-item suites real configs have, where the textbook z-test flags spurious drift. When several suites are monitored at once, p-values are **Holm-adjusted** so the family-wise false-drift rate stays at α. And a drift row is only computed when the runs are actually comparable (same dataset bytes, metric, and runs-per-item) — otherwise it is flagged **NOT COMPARABLE** rather than dressed up as a p-value.

Items that share a source (several questions about the same document, scenarios from the same template) are not independent evidence. Give them a `cluster` field and the interval and verdict use a **cluster-adjusted effective sample size** (one-way cluster-robust variance → design effect, with a t critical value carrying G−1 degrees of freedom because the variance is estimated from G clusters), so correlation widens your intervals instead of silently flattering them. Known limit, in the open: with very few clusters and high intra-cluster correlation even the adjusted interval under-covers somewhat — the fix is more clusters, not more items per cluster. `runs: N` repeats each item; an item passes only if every run passes, so output instability shows up as failure instead of luck.

What the statistics honestly mean — and don't: the intervals quantify sampling uncertainty *on your dataset*. They do not certify field performance on a different input distribution, and `verify` proves integrity (the bytes haven't changed since the pack was built), not provenance (packs are not yet signed — see roadmap).

## Quickstart

```bash
pip install evidentry
evidentry init my-model   # scaffold config + sample dataset
cd my-model
evidentry run             # works out of the box with the mock provider
```

Or run the worked example — a fictional bank validating a credit-memo summarizer (no API key needed; the mock provider makes it fully deterministic):

```bash
cd examples/credit_memo_summarizer
evidentry run -c evidentry.yaml
evidentry verify evidence/credit-memo-summarizer-v1.2.0-*
```

The committed sample output is in [`examples/credit_memo_summarizer/evidence/`](examples/credit_memo_summarizer/evidence/) — including a **failing** numeric-extraction suite and a use-limit violation, because an evidence tool you only see passing is a demo, not evidence. A second example, [`examples/judged_faithfulness/`](examples/judged_faithfulness/), scores a graded quality with a two-judge panel that genuinely disagrees.

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

## Framework mappings — read this before using them

Packs can include requirement-coverage tables for **SR 26-2** (US interagency model risk guidance, April 2026) and **EU AI Act Annex IV**. Three facts you should know, from the primary sources:

- **SR 26-2 explicitly excludes generative and agentic AI from its scope** (footnote 3 of the guidance) and is **non-binding** ("non-compliance with this guidance will not result in supervisory criticism"). Its principles apply directly to traditional statistical models and non-generative AI. Using its structure for an LLM system — as the worked example does — is an *analogy*: organizing evidence around principles a bank already applies elsewhere, ahead of the GenAI-specific guidance the agencies have signaled. The mapping file says this in its header, with quotes.
- **EU AI Act high-risk documentation obligations are not yet in force** (expected Dec 2027 / Aug 2028 for most systems, post-Omnibus).
- The mappings are **interpretations** that structure evidence. They are not the guidance text, not legal advice, and not a substitute for independent validation. Requirements that need human judgment (conceptual soundness review, effective challenge, governance) are deliberately surfaced as gaps rather than papered over.

## Scope, honestly

evidentry covers outcomes-analysis-style evidence for text-in/text-out systems, scored by deterministic metrics or LLM-judge panels with disagreement reporting. Not yet covered: fairness testing, multi-turn agent traces, tool-call audit, pack signing, judge self-consistency modeling.

## Roadmap

- **Pack signing + trusted timestamps**, so integrity holds against tampering, not just accidents
- **Judge self-consistency** (the same judge re-asked; a judge that always agrees with itself is not the same as a judge that's right) and intervals on κ
- **Format adapters** for DeepEval / Inspect / promptfoo output files, so `external` ingestion doesn't require hand-exported JSONL
- Agent-trace evidence (multi-turn, tool calls)
- CI integration: fail the build when a high-tier model's evidence pack regresses
- More mappings (NIST AI RMF, ISO/IEC 42001)

## Authors

Built by Alejandro Lizardi and John Dryden as part of Periapsis, working on statistically honest evaluation evidence for AI systems.

MIT licensed. Issues and war stories from your own eval reviews are very welcome.
