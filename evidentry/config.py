"""Load and validate an evidentry.yaml configuration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_METRICS = {"exact_match", "contains", "regex", "numeric", "refusal", "judge"}
VALID_PROVIDERS = {"mock", "anthropic", "openai", "external"}
VALID_JUDGE_TYPES = {"mock", "anthropic", "openai", "external"}
VALID_JUDGE_DECISIONS = {"unanimous", "majority"}


class ConfigError(ValueError):
    """Raised when an evidentry.yaml is missing or invalid."""


@dataclass
class ModelCard:
    name: str
    version: str
    use_case: str
    owner: str
    materiality_tier: int
    tier_rationale: str
    vendor: str = "internal"
    limitations: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "vendor": self.vendor,
            "use_case": self.use_case,
            "owner": self.owner,
            "materiality_tier": self.materiality_tier,
            "tier_rationale": self.tier_rationale,
            "limitations": list(self.limitations),
            "description": self.description,
        }


@dataclass
class JudgeSpec:
    """One judge on a panel: a model (or pre-computed verdict file) that
    scores outputs against the suite's rubric."""

    name: str
    type: str
    model_id: str = ""
    results_file: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "model_id": self.model_id,
            "results_file": self.results_file,
            "options": self.options,
        }


@dataclass
class JudgeConfig:
    """Judge panel for a `metric: judge` suite.

    `decision` is how per-judge verdicts become an item verdict:
    'unanimous' (default — disagreement counts against the item, consistent
    with the runs-per-item rule that instability is failure) or 'majority'
    (strict majority of judges; ties and invalid responses count against).
    `min_agreement` optionally gives the judge-agreement rate its own
    settledness verdict at that threshold.
    """

    rubric: str
    judges: list[JudgeSpec]
    decision: str = "unanimous"
    min_agreement: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rubric": self.rubric,
            "decision": self.decision,
            "min_agreement": self.min_agreement,
            "judges": [j.to_dict() for j in self.judges],
        }


@dataclass
class SuiteConfig:
    name: str
    dataset: str
    metric: str
    threshold: float
    description: str = ""
    runs: int = 1
    metric_options: dict[str, Any] = field(default_factory=dict)
    requirement_ids: list[str] = field(default_factory=list)
    judge: JudgeConfig | None = None


@dataclass
class ProviderConfig:
    type: str
    model_id: str = ""
    results_file: str = ""
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    model: ModelCard
    provider: ProviderConfig
    suites: list[SuiteConfig]
    mappings: list[str]
    out_dir: str
    base_dir: Path

    def canonical_hash(self) -> str:
        """Stable SHA-256 over the semantic content of the config."""
        payload = {
            "model": self.model.to_dict(),
            "provider": {
                "type": self.provider.type,
                "model_id": self.provider.model_id,
                "results_file": self.provider.results_file,
                "options": self.provider.options,
            },
            "suites": [
                {
                    "name": s.name,
                    "dataset": s.dataset,
                    "metric": s.metric,
                    "threshold": s.threshold,
                    "runs": s.runs,
                    "metric_options": s.metric_options,
                    "requirement_ids": s.requirement_ids,
                    # Only present for judge suites, so hashes of existing
                    # non-judge configs are unchanged across versions.
                    **({"judge": s.judge.to_dict()} if s.judge is not None else {}),
                }
                for s in self.suites
            ],
            "mappings": self.mappings,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _require(data: dict[str, Any], key: str, where: str) -> Any:
    if key not in data:
        raise ConfigError(f"Missing required key '{key}' in {where}")
    return data[key]


def _load_judge(s: dict[str, Any], name: str, metric: str, runs: int) -> JudgeConfig | None:
    if metric != "judge":
        if "judge" in s:
            raise ConfigError(
                f"suite '{name}': a 'judge' block is only valid with metric: judge"
            )
        return None
    if runs != 1:
        # Judge self-consistency across repeated runs is its own evidence
        # problem (correlated judging events would need different intervals);
        # refuse rather than model it wrong.
        raise ConfigError(f"suite '{name}': metric 'judge' requires runs: 1")
    where = f"suite '{name}' judge"
    j = _require(s, "judge", f"suite '{name}' (metric 'judge' needs a judge block)")
    rubric = str(_require(j, "rubric", where)).strip()
    if not rubric:
        raise ConfigError(f"{where}: rubric must be non-empty")
    judges_raw = _require(j, "judges", where)
    if not judges_raw:
        raise ConfigError(f"{where}: at least one judge is required")
    specs: list[JudgeSpec] = []
    seen_judges: set[str] = set()
    for jr in judges_raw:
        jname = str(_require(jr, "name", where))
        if jname in seen_judges:
            raise ConfigError(f"{where}: duplicate judge name '{jname}'")
        seen_judges.add(jname)
        jtype = str(_require(jr, "type", f"{where} '{jname}'"))
        if jtype not in VALID_JUDGE_TYPES:
            raise ConfigError(
                f"{where} '{jname}': type must be one of {sorted(VALID_JUDGE_TYPES)}"
            )
        results_file = str(jr.get("results_file", ""))
        if jtype == "external" and not results_file:
            raise ConfigError(
                f"{where} '{jname}': results_file is required when type is 'external'"
            )
        specs.append(
            JudgeSpec(
                name=jname,
                type=jtype,
                model_id=str(jr.get("model_id", "")),
                results_file=results_file,
                options=dict(jr.get("options", {})),
            )
        )
    decision = str(j.get("decision", "unanimous"))
    if decision not in VALID_JUDGE_DECISIONS:
        raise ConfigError(
            f"{where}: decision must be one of {sorted(VALID_JUDGE_DECISIONS)}"
        )
    min_agreement: float | None = None
    if j.get("min_agreement") is not None:
        min_agreement = float(j["min_agreement"])
        if not 0.0 <= min_agreement <= 1.0:
            raise ConfigError(f"{where}: min_agreement must be between 0 and 1")
    return JudgeConfig(
        rubric=rubric, judges=specs, decision=decision, min_agreement=min_agreement
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"Top level of {path} must be a mapping")

    m = _require(raw, "model", str(path))
    tier = int(_require(m, "materiality_tier", "model"))
    if tier not in (1, 2, 3):
        raise ConfigError("model.materiality_tier must be 1, 2, or 3")
    model = ModelCard(
        name=str(_require(m, "name", "model")),
        version=str(_require(m, "version", "model")),
        use_case=str(_require(m, "use_case", "model")),
        owner=str(_require(m, "owner", "model")),
        materiality_tier=tier,
        tier_rationale=str(_require(m, "tier_rationale", "model")),
        vendor=str(m.get("vendor", "internal")),
        limitations=[str(x) for x in m.get("limitations", [])],
        description=str(m.get("description", "")),
    )

    p = _require(raw, "provider", str(path))
    ptype = str(_require(p, "type", "provider"))
    if ptype not in VALID_PROVIDERS:
        raise ConfigError(f"provider.type must be one of {sorted(VALID_PROVIDERS)}")
    provider = ProviderConfig(
        type=ptype,
        model_id=str(p.get("model_id", "")),
        results_file=str(p.get("results_file", "")),
        options=dict(p.get("options", {})),
    )
    if ptype == "external" and not provider.results_file:
        raise ConfigError("provider.results_file is required when provider.type is 'external'")

    suites_raw = _require(raw, "suites", str(path))
    if not suites_raw:
        raise ConfigError("At least one eval suite is required")
    suites: list[SuiteConfig] = []
    seen_names: set[str] = set()
    for s in suites_raw:
        name = str(_require(s, "name", "suite"))
        if name in seen_names:
            raise ConfigError(f"Duplicate suite name: {name}")
        seen_names.add(name)
        metric = str(_require(s, "metric", f"suite '{name}'"))
        if metric not in VALID_METRICS:
            raise ConfigError(
                f"suite '{name}': metric must be one of {sorted(VALID_METRICS)}"
            )
        threshold = float(_require(s, "threshold", f"suite '{name}'"))
        if not 0.0 <= threshold <= 1.0:
            raise ConfigError(f"suite '{name}': threshold must be between 0 and 1")
        runs = int(s.get("runs", 1))
        judge_cfg = _load_judge(s, name, metric, runs)
        suites.append(
            SuiteConfig(
                name=name,
                dataset=str(_require(s, "dataset", f"suite '{name}'")),
                metric=metric,
                threshold=threshold,
                description=str(s.get("description", "")),
                runs=runs,
                metric_options=dict(s.get("metric_options", {})),
                requirement_ids=[str(x) for x in s.get("requirement_ids", [])],
                judge=judge_cfg,
            )
        )

    report = raw.get("report", {})
    return Config(
        model=model,
        provider=provider,
        suites=suites,
        mappings=[str(x) for x in report.get("mappings", ["sr-26-2"])],
        out_dir=str(report.get("out_dir", "evidence")),
        base_dir=path.parent.resolve(),
    )
