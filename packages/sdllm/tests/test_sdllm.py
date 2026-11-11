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
    ocr = LETTER.replace("diesel", "diese1").replace("Street", "Strect")  # OCR misreads
    assert sdllm.quote_found("release of diesel fuel at 1215 Main Street", ocr)
    assert not sdllm.quote_found("release of gasoline at 40 Elm Street", LETTER)
    assert not sdllm.quote_found("", LETTER)


def test_a_near_match_cant_change_a_number():
    assert sdllm.quote_found("File Number 2026.148", LETTER)
    assert not sdllm.quote_found("File Number 2026.149", LETTER)
    assert not sdllm.quote_found("September 27, 2026", LETTER)
    assert not sdllm.quote_found("P.O. Box 1008", LETTER)
    assert sdllm.quote_found("September 21 2026", LETTER)  # punctuation isn't a digit


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


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ('{"name": "Alice", "year": 2024}', {"name": "Alice", "year": 2024}),
        ('  \n{"key": "value"}\n  ', {"key": "value"}),
        ('```json\n{"name": "Bob"}\n```', {"name": "Bob"}),
        ('```\n{"name": "Carol"}\n```', {"name": "Carol"}),
        ('```json\n{\n  "name": "Eve",\n  "year": 2023\n}\n```', {"name": "Eve", "year": 2023}),
        ('Here is the data: {"name": "Dave"} as requested.', {"name": "Dave"}),
        ('First: {"name": "Alice"} and second: {"name": "Bob"}', {"name": "Alice"}),
        ('{"status": "ok"} — extracted from the document above.', {"status": "ok"}),
        ('{"key": "value with } brace inside"}', {"key": "value with } brace inside"}),
        (
            '{"outer": {"inner": "value"}, "list": [1, 2, 3]}',
            {"outer": {"inner": "value"}, "list": [1, 2, 3]},
        ),
        ('Sure! ```json\n{"name": "Fay"}\n``` Let me know.', {"name": "Fay"}),
    ],
)
def test_json_part_finds_the_object(reply, expected):
    from sdllm._core import _json_part

    assert json.loads(_json_part(reply)) == expected


@pytest.mark.parametrize("reply", ["", "I cannot extract structured data."])
def test_json_part_leaves_text_without_json(reply):
    from sdllm._core import _json_part

    assert _json_part(reply) == reply.strip()


def test_prose_wrapped_answer_validates(fake):
    fake.answers.append('Here you go: {"label": "letter", "reason": "It is addressed."} Thanks!')
    result = sdllm.classify(LETTER, ["letter", "form"], model="fake/x", cache=False)
    assert result.label == "letter"


def test_answer_without_json_is_asked_again(fake):
    fake.answers += ["I can't tell.", {"label": "form", "reason": "Fields."}]
    result = sdllm.classify(LETTER, ["letter", "form"], model="fake/x", cache=False)
    assert result.label == "form"
    assert len(fake.calls) == 2
