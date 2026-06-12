"""evidentry — turn LLM eval runs into auditable evidence packs with defensible statistics.

Define a model card and eval suites in YAML, run them (or ingest results
from your own harness), and emit a versioned, hash-pinned evidence pack:
Wilson-CI-gated verdicts, sample-size certificates for unsettled verdicts,
exact run-over-run drift tests with multiplicity control, cluster-adjusted
intervals for correlated items, LLM-judge panels with disagreement evidence
(agreement settledness, Cohen's kappa, judge-dependence flags), and optional
requirement-coverage mappings (SR 26-2 principles, EU AI Act Annex IV).
"""

__version__ = "0.3.0"
