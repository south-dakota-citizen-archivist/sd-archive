import json

import pytest
from pydantic import BaseModel

import sdllm

LETTER = """DEPARTMENT of AGRICULTURE and NATURAL RESOURCES
September 21, 2026
M.G. Oil Co.
P.O. Box 1006
Rapid City, SD 57703
Subject: Department of Agriculture and Natural Resources File Number 2026.148:
pertaining to a release of diesel fuel at 1215 Main Street, Sturgis."""


class Letter(BaseModel):
    file_number: str | None = None
    recipient: str | None = None
    date: str | None = None
    material: str | None = None


class Fake:
    """A backend that answers from a list, and records what it was asked."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, model, messages, schema, **params):
        self.calls.append((model, messages, schema, params))
        a = self.answers.pop(0)
        return a if isinstance(a, str) else json.dumps(a)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("SDLLM_CACHE", str(tmp_path / "cache"))
    f = Fake()
    sdllm.register_backend("fake", f)
    return f


def test_guard_keeps_requests_local(monkeypatch):
    monkeypatch.delenv("SDLLM_ALLOW_REMOTE", raising=False)
    assert sdllm.is_local("ollama_chat/gemma4:latest")
    assert sdllm.is_local("openai/qwen", api_base="http://localhost:8080/v1")
    assert not sdllm.is_local("openai/gpt-x")
    assert not sdllm.is_local("claude-opus-5-5")
    assert sdllm.backend_for("claude-opus-5-5") == "anthropic"
    with pytest.raises(sdllm.RemoteCallBlocked):
        sdllm.extract(Letter, LETTER, model="claude-opus-5-5", cache=False)
    with pytest.raises(sdllm.RemoteCallBlocked):
        sdllm.classify(LETTER, ["letter", "form"], model="openai/gpt-x", cache=False)


def test_extract_checks_quotes(fake):
    fake.answers.append({
        "data": {"file_number": "2026.148", "recipient": "M.G. Oil Co.",
                 "date": "2026-09-21", "material": "gasoline"},
        "evidence": [
            {"field": "file_number", "quote": "File Number 2026.148"},
            {"field": "recipient", "quote": "M.G.  Oil Co."},  # spacing differs: still found
            {"field": "date", "quote": "September 21, 2026"},
            {"field": "material", "quote": "a release of gasoline"},  # not in the letter
        ],
    })  # fmt: skip
    got = sdllm.extract(Letter, LETTER, model="fake/m")
    assert got.data.file_number == "2026.148"
    assert got.unsupported == ["material"] and not got.ok
    system = fake.calls[0][1][0]["content"]
    assert "quote" in system and fake.calls[0][2].__name__ == "LetterWithEvidence"


def test_drop_unsupported(fake):
    fake.answers.append({"data": {"file_number": "2026.148", "material": "gasoline"},
                         "evidence": [{"field": "file_number", "quote": "2026.148"}]})  # fmt: skip
    got = sdllm.extract(Letter, LETTER, model="fake/m", drop_unsupported=True)
    assert got.data.material is None and got.unsupported == ["material"]


def test_ocr_quotes_match_loosely():
    assert sdllm.quote_found("release of diesel fuel at 1215 Main Street", LETTER)
    assert sdllm.quote_found("release of diese1 fuel at 1215 Main Street", LETTER)  # OCR 1 for l
    assert not sdllm.quote_found("release of gasoline at 40 Elm Street", LETTER)
    assert not sdllm.quote_found("", LETTER)


def test_cache(fake):
    fake.answers.append({"data": {"file_number": "2026.148"},
                         "evidence": [{"field": "file_number", "quote": "2026.148"}]})  # fmt: skip
    first = sdllm.extract(Letter, LETTER, model="fake/m")
    again = sdllm.extract(Letter, LETTER, model="fake/m")  # no answer queued: must be cached
    assert not first.cached and again.cached and again.data == first.data
    assert len(fake.calls) == 1


def test_invalid_answer_is_asked_again(fake):
    fake.answers += ["```json\n{\"label\": \"memo\", \"reason\": \"?\"}\n```",
                     {"label": "letter", "reason": "addressed to a company"}]  # fmt: skip
    got = sdllm.classify(LETTER, {"letter": "correspondence", "spill report": "the form"},
                         model="fake/m", cache=False)  # fmt: skip
    assert got.label == "letter"
    assert len(fake.calls) == 2 and "didn't match the schema" in fake.calls[1][1][-1]["content"]
    assert "- spill report: the form" in fake.calls[0][1][0]["content"]


def test_evaluate():
    gold = [
        {"name": "Jane Smith", "amount": 100, "city": None},
        {"name": "Bo Lee", "amount": 50, "city": "Lead"},
    ]
    pred = [
        {"name": "jane  smith", "amount": 100, "city": "Spearfish"},
        {"name": "Bo Lee", "amount": 55, "city": "Lead"},
    ]
    s = sdllm.evaluate(gold, pred)
    assert s["name"]["exact"] == 1.0
    assert s["amount"] == {"exact": 0.5, "precision": 0.5, "recall": 0.5}
    assert s["city"] == {"exact": 0.5, "precision": 0.5, "recall": 1.0}
    assert s["overall"]["exact"] == round((1 + 0.5 + 0.5) / 3, 4)


@pytest.mark.local_llm
def test_local_model_extracts(tmp_path, monkeypatch):
    monkeypatch.setenv("SDLLM_CACHE", str(tmp_path / "cache"))
    """The mechanics against a real model: a schema-valid answer whose quotes are checked.
    How well a model extracts is for the benchmarks, not this test."""
    got = sdllm.extract(Letter, LETTER, model=sdllm.DEFAULT_MODEL)
    assert isinstance(got.data, Letter) and any(got.data.model_dump().values())
    assert set(got.evidence) | set(got.unsupported)  # quotes came back and were checked
