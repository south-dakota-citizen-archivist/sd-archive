"""Extraction and classification with schema-checked answers, quotes checked against the
source, and a response cache."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError, create_model

from ._backends import call

DEFAULT_MODEL = os.environ.get("SDLLM_MODEL", "ollama_chat/gemma4:latest")


def cache_dir() -> Path:
    return Path(os.environ.get("SDLLM_CACHE") or "~/.cache/sdllm").expanduser()


class Cache:
    """Answers kept by a hash of everything that shapes them (model, messages, schema,
    parameters), one JSON file each, so the same question is never asked twice."""

    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else cache_dir()

    @staticmethod
    def key(model: str, messages: list[dict], schema: type[BaseModel], params: dict) -> str:
        blob = json.dumps({"model": model, "messages": messages, "params": params,
                           "schema": schema.model_json_schema()}, sort_keys=True)  # fmt: skip
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, key: str) -> str | None:
        p = self.root / key[:2] / f"{key}.json"
        return json.loads(p.read_text())["answer"] if p.exists() else None

    def put(self, key: str, answer: str, model: str) -> None:
        p = self.root / key[:2] / f"{key}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".partial")
        tmp.write_text(json.dumps({"model": model, "answer": answer}))
        tmp.replace(p)


def ask(
    model: str,
    messages: list[dict],
    schema: type[BaseModel],
    *,
    cache: Cache | str | Path | bool = True,
    retries: int = 1,
    **params,
) -> tuple[BaseModel, bool]:
    """One schema-checked answer: (validated instance, came from the cache). An answer
    that doesn't validate is asked again, with the error, up to `retries` times."""
    store = None if cache is False else (cache if isinstance(cache, Cache) else
                                         Cache(None if cache is True else cache))  # fmt: skip
    call_params = {k: v for k, v in params.items() if k not in ("allow_remote", "api_base")}
    key = Cache.key(model, messages, schema, call_params) if store else ""
    if store and (hit := store.get(key)) is not None:
        return schema.model_validate_json(hit), True
    attempt_messages = list(messages)
    for attempt in range(retries + 1):
        text = call(model, attempt_messages, schema, **params)
        try:
            answer = schema.model_validate_json(_json_part(text))
        except ValidationError as exc:
            if attempt == retries:
                raise
            attempt_messages = [*messages, {"role": "assistant", "content": text},
                                {"role": "user", "content": f"That didn't match the schema: {exc}. "
                                 "Answer again with JSON that matches it."}]  # fmt: skip
            continue
        if store:
            store.put(key, answer.model_dump_json(), model)
        return answer, False
    raise AssertionError("unreachable")


def _json_part(text: str) -> str:
    """The JSON in a reply. Local models sometimes wrap it in a ```json fence or in prose
    ("Here is the data: {...} as requested"); this finds the first whole object. Text with
    no JSON comes back as it was, to fail validation and be asked again."""
    t = text.strip()
    if fenced := re.search(r"```[a-zA-Z]*\n?(.*?)```", t, re.DOTALL):
        t = fenced.group(1).strip()
    try:
        json.loads(t)
        return t
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for start in (i for i, ch in enumerate(t) if ch in "{["):
        try:
            _, end = decoder.raw_decode(t, start)
        except ValueError:
            continue
        return t[start:end]
    return t


# ---------------------------------------------------------------------------
# Quotes


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def quote_found(quote: str, source: str, threshold: float = 90) -> bool:
    """Whether a quote appears in the source: exactly (ignoring case and spacing), or, for
    OCR'd text, as a near match of at least `threshold` (0-100) whose digits appear in the
    source exactly and in order. A near match can't change a number, a date or an amount."""
    q, s = _norm(quote), _norm(source)
    if not q:
        return False
    if q in s:
        return True
    if (digits := re.sub(r"\D", "", q)) and digits not in re.sub(r"\D", "", s):
        return False
    from rapidfuzz import fuzz

    return fuzz.partial_ratio(q, s) >= threshold


class Evidence(BaseModel):
    field: str
    quote: str


@dataclass
class Extraction:
    data: BaseModel
    evidence: dict[str, str] = field(default_factory=dict)  # field → the quote given
    unsupported: list[str] = field(default_factory=list)  # fields whose quote isn't in the text
    model: str = ""
    cached: bool = False

    @property
    def ok(self) -> bool:
        return not self.unsupported


EXTRACT_INSTRUCTIONS = """Extract the fields described by the JSON schema from the document.
Use only what the document says; leave a field null (or empty) when the document doesn't give it.
For every field you fill in, add an evidence entry with the field's name and a short quote
copied exactly from the document that supports it."""


def extract(
    schema: type[BaseModel],
    text: str,
    *,
    model: str | None = None,
    instructions: str = "",
    quote_required: bool = True,
    drop_unsupported: bool = False,
    threshold: float = 90,
    **params,
) -> Extraction:
    """Fields from a document into `schema`. With quote_required, the model also returns a
    quote for each field it fills, and each quote is checked against the text: fields
    without one that's found are listed in `unsupported` (and set to None with
    drop_unsupported, where the schema allows it)."""
    model = model or DEFAULT_MODEL
    envelope = schema
    if quote_required:
        envelope = create_model(f"{schema.__name__}WithEvidence", data=(schema, ...),
                                evidence=(list[Evidence], ...))  # fmt: skip
    base = EXTRACT_INSTRUCTIONS if quote_required else EXTRACT_INSTRUCTIONS.split("\n")[0]
    system = "\n\n".join(x for x in (base, instructions) if x)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"<document>\n{text}\n</document>"}]  # fmt: skip
    answer, cached = ask(model, messages, envelope, **params)
    if not quote_required:
        return Extraction(answer, model=model, cached=cached)
    data: BaseModel = answer.data
    evidence = {e.field: e.quote for e in answer.evidence}
    filled = [k for k, v in data.model_dump().items() if v not in (None, "", [], {})]
    unsupported = [k for k in filled if not quote_found(evidence.get(k, ""), text, threshold)]
    if drop_unsupported and unsupported:
        values = data.model_dump()
        for k in unsupported:
            values[k] = None
        # a required field can't be emptied; it stays listed as unsupported
        with contextlib.suppress(ValidationError):
            data = schema.model_validate(values)
    return Extraction(data, evidence, unsupported, model, cached)


@dataclass
class Classification:
    label: str
    reason: str
    model: str
    cached: bool


def classify(
    text: str,
    labels: list[str] | dict[str, str],
    *,
    model: str | None = None,
    instructions: str = "",
    **params,
) -> Classification:
    """Which of `labels` a document is. labels: names, or names → descriptions."""
    model = model or DEFAULT_MODEL
    described = labels if isinstance(labels, dict) else dict.fromkeys(labels, "")
    names = tuple(described)
    answer_model = create_model("Classification", label=(Literal[names], ...),  # type: ignore[valid-type]
                                reason=(str, ...))  # fmt: skip
    listing = "\n".join(f"- {n}: {d}" if d else f"- {n}" for n, d in described.items())
    system = (f"Classify the document as one of these:\n{listing}\n"
              "Give the label and a one-sentence reason.")  # fmt: skip
    if instructions:
        system += "\n\n" + instructions
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"<document>\n{text}\n</document>"}]  # fmt: skip
    answer, cached = ask(model, messages, answer_model, **params)
    return Classification(answer.label, answer.reason, model, cached)


# ---------------------------------------------------------------------------
# Evaluation


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return _norm(a) == _norm(b)
    return a == b


def evaluate(gold: list[dict], predicted: list[dict], fields: list[str] | None = None) -> dict:
    """Per-field scores of predictions against hand-checked answers, row for row.

    For each field: `exact` (share of rows where they match), and treating a non-empty
    value as a claim, `precision` (claims that are right) and `recall` (gold values found).
    `overall` is the mean exact match across fields."""
    if len(gold) != len(predicted):
        raise ValueError("gold and predicted must have the same number of rows")
    fields = fields or sorted({k for row in gold for k in row})
    empty = (None, "", [], {})
    out: dict[str, dict] = {}
    for f in fields:
        exact = claimed = right = present = found = 0
        for g, p in zip(gold, predicted, strict=True):
            gv, pv = g.get(f), p.get(f)
            exact += _same(gv, pv) or (gv in empty and pv in empty)
            if pv not in empty:
                claimed += 1
                right += _same(gv, pv)
            if gv not in empty:
                present += 1
                found += _same(gv, pv)
        n = len(gold) or 1
        out[f] = {"exact": round(exact / n, 4),
                  "precision": round(right / claimed, 4) if claimed else None,
                  "recall": round(found / present, 4) if present else None}  # fmt: skip
    out["overall"] = {"exact": round(sum(v["exact"] for v in out.values()) / (len(fields) or 1), 4)}
    return out
