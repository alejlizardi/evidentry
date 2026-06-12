# LLM-as-judge with disagreement evidence

The credit-memo summarizer again, but scored on a graded quality —
**faithfulness** — by a two-judge panel instead of string matching. Both
judges are `mock` (verdicts live in the dataset), so it runs with no API
key:

```bash
cd examples/judged_faithfulness
evidentry run -c evidentry.yaml
```

It is built to demonstrate the failure modes a judge layer must surface
rather than hide. Expect to see, in the report:

- **FAIL (point)** on the suite: under the unanimous rule only 6/10 items
  pass, but the sample is too small to call that settled — so it carries a
  sample-size certificate like any other unsettled verdict.
- **Panel agreement 70%** with a settledness verdict against the configured
  `min_agreement: 0.85` — the agreement rate is itself a measured quantity
  with a confidence interval, not a vibe.
- **Cohen's κ ≈ 0.40** between the judges: raw agreement (77.8% over valid
  pairs) flatters a pair of judges who both pass most items; kappa is the
  chance-corrected number.
- **JUDGE-DEPENDENT**: the lenient judge alone gives 90% (above the 75%
  threshold), the strict judge alone gives 60% (below it). The headline
  verdict flips with the choice of judge, and the report says so instead of
  laundering one judge's opinion into a fact.
- **1 invalid judge response** (`fa-08`: the strict judge deliberates
  instead of giving a verdict) — recorded as `invalid` and counted against
  agreement and the item, never silently coerced.
