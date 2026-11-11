# sdllm

Language models for reading records:
- extraction into a schema, with every value's quote checked against the source
- classification
- a response cache
- a guard that keeps requests on this machine unless told otherwise
- per-field evaluation against hand-checked answers

Local models go through LiteLLM (Ollama, LM Studio, MLX and other OpenAI-compatible servers). Claude is available as a reference model, through Anthropic's SDK. Plain Python, no Django.

```python
from pydantic import BaseModel, Field
import sdllm


class Letter(BaseModel):
    file_number: str | None = Field(None, description="the agency's case or file number")
    recipient: str | None = Field(None, description="who the letter is addressed to")


got = sdllm.extract(Letter, text, model="ollama_chat/gemma4:latest")
got.data  # Letter(...)
got.evidence  # {"file_number": "File Number 2026.148", ...}: the quote given for each field
got.unsupported  # fields whose quote isn't in the text

sdllm.classify(
    text,
    {"spill report": "the state's form", "letter": "correspondence"},
    model="ollama_chat/gemma4:latest",
).label
```

## What it does

- **Schema-checked answers.** The model is asked for JSON matching a Pydantic model. JSON wrapped in a code fence or in prose is found (the first whole object). An answer that doesn't validate, or has no JSON, is asked for again, once by default, with the validation error.
- **Quotes checked against the source.** With `quote_required` (the default), the model returns a quote for each field it fills. Each quote must appear in the text: ignoring case and spacing, or as a 90% near match for OCR'd text. A near match must still have the quote's digits exactly and in order, so it can't change a number, date or amount.
  - Fields without a found quote are listed in `unsupported`, and set to `None` with `drop_unsupported=True`.
  - A found quote shows the text exists in the document, not that it belongs in that field. Per-field evaluation catches that.
- **Remote-call guard.** Only models on this machine are called unless `allow_remote=True` or `SDLLM_ALLOW_REMOTE=1`. That means a local LiteLLM provider such as `ollama_chat/…` or `lm_studio/…`, a localhost `api_base`, or a backend registered in code. Anything else raises `RemoteCallBlocked`.
- **Cache.** Answers are kept by a hash of model, messages, schema and parameters, in `~/.cache/sdllm` (or `$SDLLM_CACHE`), so a rerun asks nothing twice. Pass `cache=False` to skip it.
- **Claude as reference** (`model="claude-opus-5-5"`, the `claude` extra, and `allow_remote=True`): for building gold sets and setting an upper bound in benchmarks. It uses the SDK's structured outputs (`messages.parse`). A refusal raises `Refused`. There's no automatic fallback to another model, so a benchmark always knows which model answered.
- **Local models:** Ollama models get a 16K context window by default (`num_ctx`), because thinking models fill Ollama's small default and then return nothing. An empty answer raises `EmptyAnswer`.
- **`evaluate(gold, predicted)`** scores each field: exact match, precision (filled values that are right) and recall (gold values found).
- **Other backends:** `register_backend(name, fn)` serves models named `<name>/…`, for MLX or a test double.

## Use from another repo

```toml
[project]
dependencies = ["sdllm[local]"]

[tool.uv.sources]
sdllm = { git = "https://github.com/south-dakota-citizen-archivist/sd-archive", subdirectory = "packages/sdllm", tag = "sdllm-vX.Y.Z" }
```
