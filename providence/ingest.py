"""Format adapters: convert other eval tools' output files into providence's
external-ingestion pair (dataset.jsonl + outputs.jsonl).

Scope, stated plainly: an adapter extracts each item's id, input, and the
model's RAW OUTPUT TEXT. providence then re-scores those outputs with its
own metrics — it does not import the source tool's assertions, scores, or
judgments, because numbers it didn't compute are numbers it can't stand
behind. Items the source tool recorded as errored (no model output) are
skipped and counted out loud; an empty string scored as a failure would be
manufactured evidence.

Supported formats, pinned to what their maintainers document:

- promptfoo: the JSON file written by ``promptfoo eval --output results.json``
  (``version: 3`` summary, either bare or wrapped in an OutputFile envelope).
  Each result row carries ``testIdx``/``promptIdx``, the rendered
  ``prompt.raw``, and the provider's ``response.output``.
- inspect: an Inspect AI log in JSON form. The binary ``.eval`` format is
  not parsed — convert it first with ``inspect log dump <file>``. Samples
  carry ``id``, ``epoch``, ``input`` (string or chat messages), ``target``,
  and the completion at ``output.choices[0].message.content`` (the Python
  ``completion`` property is not a serialized field).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class IngestError(ValueError):
    """Raised when a source file is missing, malformed, or ambiguous."""


def _text_from_content(content: Any) -> str:
    """Inspect message content is `str | list[Content]`; keep the text parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        return "\n".join(parts)
    return str(content)


def _text_from_messages(messages: list[Any]) -> str:
    lines = []
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("role", "?")
            lines.append(f"{role}: {_text_from_content(msg.get('content'))}")
    return "\n".join(lines)


def ingest_promptfoo(
    path: Path, prompt_idx: int | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parse a promptfoo v3 results JSON into (dataset rows, output rows, info)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    # `--output results.json` may write the bare EvaluateSummaryV3 or an
    # OutputFile envelope whose `results` field holds the summary.
    summary = raw
    if isinstance(raw.get("results"), dict):
        summary = raw["results"]
    version = summary.get("version")
    if version != 3:
        raise IngestError(
            f"unsupported promptfoo output version {version!r}: only the "
            "documented version-3 summary is supported (promptfoo eval "
            "--output results.json)"
        )
    rows = summary.get("results")
    if not isinstance(rows, list) or not rows:
        raise IngestError("no results found in promptfoo file")

    prompt_indices = sorted({int(r.get("promptIdx", 0)) for r in rows})
    if len(prompt_indices) > 1 and prompt_idx is None:
        labels = {}
        for r in rows:
            p = r.get("prompt") or {}
            labels.setdefault(int(r.get("promptIdx", 0)), str(p.get("label", "")))
        listing = ", ".join(f"{i} ({labels.get(i, '')})" for i in prompt_indices)
        raise IngestError(
            "this promptfoo run compares multiple prompts — a dataset mixing "
            "them is not one suite's evidence. Pick one with --prompt-idx: "
            + listing
        )

    dataset: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    skipped: list[str] = []
    for r in rows:
        if prompt_idx is not None and int(r.get("promptIdx", 0)) != prompt_idx:
            continue
        item_id = str(r.get("id") or f"test-{r.get('testIdx')}")
        prompt = r.get("prompt") or {}
        if isinstance(prompt.get("raw"), str) and prompt["raw"]:
            item_input = prompt["raw"]
        else:
            item_input = json.dumps(r.get("vars") or {}, sort_keys=True)
        response = r.get("response") or {}
        output = response.get("output")
        if r.get("error") or response.get("error") or output is None:
            skipped.append(item_id)
            continue
        if not isinstance(output, str):
            output = json.dumps(output, sort_keys=True)
        dataset.append({"id": item_id, "input": item_input})
        outputs.append({"id": item_id, "output": output})
    info = {
        "source": "promptfoo",
        "n_items": len(dataset),
        "skipped_errored": skipped,
        "notes": [
            "promptfoo assertions/scores were NOT imported; providence re-scores raw outputs",
            "add an 'expected' field to each dataset.jsonl row before running",
        ],
    }
    return dataset, outputs, info


def ingest_inspect(
    path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Parse an Inspect AI JSON log into (dataset rows, output rows, info)."""
    path = Path(path)
    if path.suffix == ".eval":
        raise IngestError(
            "binary .eval logs are not parsed — convert first with: "
            f"inspect log dump {path.name} > {path.stem}.json"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    samples = raw.get("samples")
    if not isinstance(samples, list) or not samples:
        raise IngestError(
            "no samples found — is this an Inspect log in JSON form? "
            "(for .eval files run `inspect log dump` first; logs recorded "
            "with samples excluded cannot be ingested)"
        )

    multi_epoch = len({int(s.get("epoch", 1)) for s in samples}) > 1
    dataset: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    skipped: list[str] = []
    list_targets: list[str] = []
    for s in samples:
        base_id = str(s.get("id"))
        item_id = f"{base_id}#e{s.get('epoch')}" if multi_epoch else base_id
        item_input = s.get("input")
        if isinstance(item_input, list):
            item_input = _text_from_messages(item_input)
        out = s.get("output") or {}
        choices = out.get("choices") or []
        if out.get("error") or not choices:
            skipped.append(item_id)
            continue
        message = choices[0].get("message") or {}
        output_text = _text_from_content(message.get("content"))
        row: dict[str, Any] = {"id": item_id, "input": str(item_input)}
        target = s.get("target")
        if isinstance(target, list):
            if len(target) == 1:
                row["expected"] = str(target[0])
            elif target:
                # Inspect list targets usually mean any-of; providence's
                # `contains` requires ALL substrings. Carry the list but
                # make the user decide.
                row["expected"] = [str(t) for t in target]
                list_targets.append(item_id)
        elif target is not None and str(target) != "":
            row["expected"] = str(target)
        dataset.append(row)
        outputs.append({"id": item_id, "output": output_text})
    info = {
        "source": "inspect",
        "n_items": len(dataset),
        "skipped_errored": skipped,
        "notes": [
            "Inspect scorer results were NOT imported; providence re-scores raw outputs",
        ],
    }
    if multi_epoch:
        info["notes"].append(
            "multiple epochs found: each (sample, epoch) became its own item "
            "(ids suffixed #e<n>); epochs of one sample are correlated — "
            "consider a `cluster` field per sample id"
        )
    if list_targets:
        info["notes"].append(
            f"{len(list_targets)} item(s) have list targets ({', '.join(list_targets[:5])}"
            + ("…" if len(list_targets) > 5 else "")
            + "): Inspect list targets usually mean any-of, but providence's "
            "`contains` metric requires ALL substrings — review these before running"
        )
    return dataset, outputs, info


INGESTERS = {"promptfoo": ingest_promptfoo, "inspect": ingest_inspect}


def write_ingested(
    dataset: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    out_dir: Path,
    force: bool = False,
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = out_dir / "dataset.jsonl"
    outputs_path = out_dir / "outputs.jsonl"
    for p in (dataset_path, outputs_path):
        if p.exists() and not force:
            raise IngestError(f"refusing to overwrite {p} (use --force)")
    with dataset_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in dataset:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    with outputs_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in outputs:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    return dataset_path, outputs_path
