# Changelog

## 0.3.0 — unreleased

The judge release: graded qualities (faithfulness, tone) can now be scored
by LLM judges — with the judges' own reliability reported as evidence,
because a judge's verdict is a measurement, not ground truth.

### Added
- **`metric: judge`**: score a suite with a panel of LLM judges against a
  written rubric. Judges use the same provider adapters as the system under
  test (Anthropic / OpenAI-compatible), pre-computed verdicts via
  `external` JSONL, or `mock` for deterministic runs. Decision rules are
  explicit: `unanimous` (default — disagreement counts against the item,
  consistent with the runs-per-item instability rule) or `majority`
  (strict; ties and invalid responses count against).
- **Judge-disagreement evidence** in every judge-scored suite:
  - panel agreement rate with a Wilson 95% CI, and — when `min_agreement`
    is configured — the same settledness verdict semantics a pass rate
    gets (cluster-adjusted when items declare clusters);
  - pairwise **Cohen's κ** (chance-corrected agreement; raw agreement is
    inflated when both judges pass nearly everything), undefined cases
    reported as such rather than forced to a number;
  - **JUDGE-DEPENDENT flag**: the suite verdict is recomputed under each
    judge alone, and the report (and `run` summary line) says loudly when
    the result flips with the choice of judge;
  - unparseable judge responses recorded as **`invalid`** — counted
    against agreement and item passes, reported per judge, never silently
    coerced to a verdict;
  - every judge's raw response stored in the pack, so reviewers can audit
    the judge, not just the judged.
- Worked example `examples/judged_faithfulness/` (mock judges, no API key):
  a faithfulness suite exhibiting real disagreement, an invalid judge
  response, an unsettled agreement verdict, and a judge-dependent result —
  with its committed evidence pack verified from a fresh checkout in CI.
- **Statistics options** (`statistics:` block; defaults unchanged, and
  configs without the block keep their existing config hashes):
  - `intervals: clopper_pearson` — strict mode: exact-conservative
    intervals for verdicts and sample-size certificates, coverage
    guaranteed ≥95% at every (n, p) (verified exactly in the validation
    study: min coverage 0.9534; implementation cross-checked against an
    independent tail-search to 2e-15). Clustered suites override strict
    mode back to the cluster-adjusted interval — a strict label on
    correlated data would be a false promise — and the report says so.
  - `drift_test: fisher_midp` — mid-p Fisher for run-over-run drift:
    equally-probable tables get half weight, recovering real small-n power
    (0.22 → 0.33 at 8 items/run against a 0.9 → 0.5 drift, exact). Stated
    cost: worst-case size 0.057 at n=50, above nominal — which is why
    plain Fisher remains the default. The report's drift section explains
    whichever test produced its p-values.

### Honest limits (stated in README and report)
- Judge *self*-consistency is not yet modeled: judge suites are restricted
  to `runs: 1` rather than treating correlated judging events as
  independent evidence.
- κ is a point estimate (no interval yet); a single-judge panel is allowed
  but flagged in bold as producing no disagreement evidence.

### Tests
- 84 → 130 unit tests, including a hand-computed κ pin, end-to-end
  judge-run regressions, the mid-p hand value (5/5 vs 2/5 → exactly 1/12),
  Clopper-Pearson's defining tail identities and an exact-coverage check,
  and strict-mode/mid-p config plumbing end-to-end.

## 0.2.0 — 2026-06-11

The statistics release. An independent code audit of 0.1.0 found nine
defects in the statistical and reporting layer; all nine are fixed here,
and the audit's counterexamples are pinned as regression tests.

### Changed
- **Drift testing** replaced wholesale: the two-proportion z-test (anti-conservative
  at small n — its real false-alarm rate exceeds the nominal 5% by up to ~50%
  at 4–8 items per run) is replaced by two-sided Fisher's exact test, valid
  at every sample size. Pure stdlib.
- **Drift comparisons refuse to test incomparable runs**: if the dataset
  bytes (`dataset_sha256`), metric, or runs-per-item changed between
  baseline and current, the row is reported `NOT COMPARABLE` instead of
  dressed up as a p-value.
- **Multiple-comparison control**: drift p-values are Holm-adjusted across
  the comparable suites; raw and adjusted values are both reported.
- **Requirement coverage is outcome-aware**: a requirement backed by suite
  results now reads `FAILING (n/m suites below threshold)` when its mapped
  suites fail — artifact presence alone never reads `EVIDENCED`.
- **`refusal` metric** rebuilt as transparent regex patterns (a refusal is a
  first-person declination to perform an action, or an explicit
  policy/scope statement). Fixes known misclassifications in both
  directions.
- **`numeric` metric** no longer silently grabs the first number in an
  ambiguous output: multiple numbers fail with an explanation unless an
  explicit extraction rule (`extract: first | last | any`) is configured.
- Vocabulary honesty: packs are **hash-pinned, not "hash-chained"**;
  `verify` detects accidental modification and corruption, not forgery
  (packs are unsigned; signing is on the roadmap). External ingestion is
  documented as what it is: a one-line-per-item `{id, output}` JSONL from
  your own harness; there are no framework adapters yet.

### Added
- **Sample-size certificates**: every unsettled (point-grade) verdict
  reports how many total items would settle it, by exact binomial power at
  the observed rate — including the honest boundary case: a rate sitting
  exactly on the threshold can never settle, and the report says so.
- **Cluster-adjusted intervals**: dataset items may declare a `cluster`
  (shared source document, scenario, or template). The interval and verdict
  then use a one-way cluster-robust variance → design effect → effective
  sample size, with a t critical value carrying G−1 degrees of freedom.
  The adjustment can only widen an interval, never narrow it.
- CI workflow: unit tests on Linux + Windows across Python 3.10–3.14, plus
  verification of the committed example pack from a fresh checkout on both
  platforms.

### Tests
- 49 → 84 unit tests, including the audit's exhibits as regression pins
  and t-quantile checks against standard tables.

## 0.1.0 — 2026-06-11 (internal, unreleased)

Initial version: YAML model card + suites, mock/external/Anthropic/OpenAI
providers, Wilson-interval verdicts (PASS vs PASS-point), two-proportion
z-test drift comparison, hash-pinned evidence packs with `verify`, gap
tables for SR 26-2 (by analogy; GenAI is out of that guidance's scope) and
EU AI Act Annex IV mappings.
