"""Model providers: where eval outputs come from.

Four sources, one interface:

- mock: deterministic, reads the response from the dataset item itself.
  Lets anyone run the full pipeline (and CI) with no API key or network.
- external: ingest pre-computed outputs from a JSONL file of
  {"id": ..., "output": ...} rows — one line per dataset item. This is the
  integration path for outputs produced by your own harness. There are no
  format adapters for specific eval frameworks (DeepEval, Inspect,
  promptfoo) yet: you must export to this JSONL yourself, and evidentry
  re-scores the raw outputs with its own metrics — it does not consume
  another tool's scores or judgments.
- anthropic / openai: thin live adapters over stdlib urllib for teams that
  want evidentry to drive the eval run itself.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .config import ProviderConfig


class Provider(Protocol):
    def complete(self, item: dict[str, Any]) -> str: ...

    def describe(self) -> dict[str, Any]: ...


class MockProvider:
    """Returns the item's own 'mock_response' field (or echoes the input)."""

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg

    def complete(self, item: dict[str, Any]) -> str:
        return str(item.get("mock_response", item.get("input", "")))

    def describe(self) -> dict[str, Any]:
        return {"type": "mock", "model_id": self.cfg.model_id or "mock"}


class ExternalProvider:
    """Reads pre-computed outputs from a JSONL file of {id, output} rows."""

    def __init__(self, cfg: ProviderConfig, base_dir: Path):
        self.cfg = cfg
        path = base_dir / cfg.results_file
        if not path.exists():
            raise FileNotFoundError(f"External results file not found: {path}")
        self.outputs: dict[str, str] = {}
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                self.outputs[str(row["id"])] = str(row["output"])
        self.source = str(path)

    def complete(self, item: dict[str, Any]) -> str:
        item_id = str(item.get("id"))
        if item_id not in self.outputs:
            raise KeyError(f"No external output for item id '{item_id}'")
        return self.outputs[item_id]

    def describe(self) -> dict[str, Any]:
        return {
            "type": "external",
            "model_id": self.cfg.model_id or "external",
            "results_file": self.cfg.results_file,
        }


class AnthropicProvider:
    """Live adapter for the Anthropic Messages API (requires ANTHROPIC_API_KEY)."""

    API_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model_id = cfg.model_id or "claude-sonnet-4-6"
        self.max_tokens = int(cfg.options.get("max_tokens", 1024))
        self.system = str(cfg.options.get("system", ""))

    def complete(self, item: dict[str, Any]) -> str:
        body: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": str(item["input"])}],
        }
        if self.system:
            body["system"] = self.system
        req = urllib.request.Request(
            self.API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return "".join(
            block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
        )

    def describe(self) -> dict[str, Any]:
        return {"type": "anthropic", "model_id": self.model_id}


class OpenAIProvider:
    """Live adapter for OpenAI-compatible chat APIs (requires OPENAI_API_KEY).

    Set options.base_url to point at any compatible endpoint (vLLM, Azure
    gateways, local servers).
    """

    def __init__(self, cfg: ProviderConfig):
        self.cfg = cfg
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        self.base_url = str(cfg.options.get("base_url", "https://api.openai.com/v1")).rstrip("/")
        self.model_id = cfg.model_id or "gpt-4o-mini"
        self.max_tokens = int(cfg.options.get("max_tokens", 1024))
        self.system = str(cfg.options.get("system", ""))

    def complete(self, item: dict[str, Any]) -> str:
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": str(item["input"])})
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(
                {"model": self.model_id, "max_tokens": self.max_tokens, "messages": messages}
            ).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return str(data["choices"][0]["message"]["content"])

    def describe(self) -> dict[str, Any]:
        return {"type": "openai", "model_id": self.model_id, "base_url": self.base_url}


def make_provider(cfg: ProviderConfig, base_dir: Path) -> Provider:
    if cfg.type == "mock":
        return MockProvider(cfg)
    if cfg.type == "external":
        return ExternalProvider(cfg, base_dir)
    if cfg.type == "anthropic":
        return AnthropicProvider(cfg)
    if cfg.type == "openai":
        return OpenAIProvider(cfg)
    raise ValueError(f"Unknown provider type: {cfg.type}")
