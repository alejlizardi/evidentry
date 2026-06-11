"""Load and validate an evidentry.yaml configuration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_METRICS = {"exact_match", "contains", "regex", "numeric", "refusal"}
VALID_PROVIDERS = {"mock", "anthropic", "openai", "external"}


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
class SuiteConfig:
    name: str
    dataset: str
    metric: str
    threshold: float
    description: str = ""
    runs: int = 1
    metric_options: dict[str, Any] = field(default_factory=dict)
    requirement_ids: list[str] = field(default_factory=list)


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
        suites.append(
            SuiteConfig(
                name=name,
                dataset=str(_require(s, "dataset", f"suite '{name}'")),
                metric=metric,
                threshold=threshold,
                description=str(s.get("description", "")),
                runs=int(s.get("runs", 1)),
                metric_options=dict(s.get("metric_options", {})),
                requirement_ids=[str(x) for x in s.get("requirement_ids", [])],
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
