# evidentry results.json — the data contract

[`results.schema.json`](results.schema.json) (JSON Schema, draft 2020-12) describes
the `results.json` written into every evidence pack. It is the single payload the
dashboard renders: `evidentry export` copies it verbatim into `site-data/packs/<id>.json`,
and the frontend's TypeScript types mirror this schema.

It exists so the frontend isn't chasing a moving target — change the run output and
this schema (and the tests below) should change with it, deliberately.

## Shape at a glance

```
results.json
├── config_sha256            pins the config; part of the pack id
├── model                    the model card (name, version, owner, tier, limitations…)
├── provider                 mock | external | anthropic | openai
├── statistics               { intervals, drift_test } — which methods were used
├── suites[]                 one per configured suite
│   ├── suite, description, metric, threshold, dataset, dataset_sha256
│   ├── n_items, n_passed, pass_rate
│   ├── ci95_low, ci95_high, ci_method        the Wilson/Clopper-Pearson interval
│   ├── verdict                               PASS | PASS (point) | FAIL | FAIL (point)
│   ├── sample_size_certificate               present only on (point) verdicts
│   ├── items[] → runs[]                      per-item, per-run output + pass/detail
│   └── judge_evidence                        present only on judge-metric suites
└── summary                  totals across suites
```

## The two ideas the schema encodes

- **`verdict` has four values, not two.** `PASS` means even the confidence-interval
  *lower bound* clears the threshold — the verdict is *settled*. `PASS (point)` means
  the point estimate clears it but the interval does not: the sample is too small to
  call it evidence yet. `FAIL` / `FAIL (point)` mirror this. The dashboard renders the
  settled/unsettled distinction visually — it is the central idea of the project.
- **`sample_size_certificate`** answers "how many more items would settle this?" with
  an exact-binomial number. It appears only on `(point)` verdicts (settled verdicts
  need nothing more).

Drift between versions is **not** in this file — a single `results.json` is one run.
The version timeline is produced by `evidentry export`, which calls `compare_packs`
across consecutive packs and writes `drift.json` alongside `index.json`.

## Validating

```bash
pip install jsonschema
python -m tests.validate_schema   # or just run the test suite: pytest tests/test_schema.py
```

All committed example packs validate against this schema; `tests/test_schema.py`
enforces that, so a drift between the contract and the real output fails CI.
