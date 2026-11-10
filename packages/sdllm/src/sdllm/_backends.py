"""Where a request goes: a local model through LiteLLM, or Claude through Anthropic's SDK.

A backend takes a model name, chat messages and a Pydantic model for the answer, and
returns the answer as JSON text. The remote-call guard runs before any backend is called.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from urllib.parse import urlsplit

from pydantic import BaseModel

# LiteLLM providers that run on this machine
LOCAL_PREFIXES = ("ollama/", "ollama_chat/", "lm_studio/", "llamafile/", "hosted_vllm/")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
CLAUDE_DEFAULT = "claude-opus-5-5"


class RemoteCallBlocked(RuntimeError):
    """A request would leave this machine; pass allow_remote=True or set SDLLM_ALLOW_REMOTE=1."""


class Refused(RuntimeError):
    """The model declined to answer."""


Backend = Callable[..., str]  # (model, messages, schema, **params) -> JSON text
_backends: dict[str, Backend] = {}


def register_backend(name: str, backend: Backend) -> None:
    _backends[name] = backend


def backend_for(model: str) -> str:
    """Which backend serves a model name: "claude-…" goes to Anthropic; "<name>/…" to a
    backend registered under that name; anything else to LiteLLM."""
    if model.startswith("claude-"):
        return "anthropic"
    head = model.split("/", 1)[0]
    return head if head in _backends else "litellm"


def is_local(model: str, api_base: str | None = None) -> bool:
    """A model served on this machine: a local LiteLLM provider, a localhost api_base, or a
    backend registered in code (which is trusted to be local)."""
    if api_base:
        return (urlsplit(api_base).hostname or "") in LOCAL_HOSTS
    return model.startswith(LOCAL_PREFIXES) or backend_for(model) not in ("litellm", "anthropic")


def guard(model: str, api_base: str | None, allow_remote: bool | None) -> None:
    allowed = (
        allow_remote if allow_remote is not None else os.environ.get("SDLLM_ALLOW_REMOTE") == "1"
    )
    if not allowed and not is_local(model, api_base):
        raise RemoteCallBlocked(
            f"{model} isn't on this machine; pass allow_remote=True or set SDLLM_ALLOW_REMOTE=1"
        )


class EmptyAnswer(RuntimeError):
    """The model returned no answer text (often: its thinking filled the context window)."""


OLLAMA_CONTEXT = 16384  # Ollama's default window is small; thinking models fill it


def _litellm(model: str, messages: list[dict], schema: type[BaseModel], **params) -> str:
    import litellm

    if model.startswith(("ollama/", "ollama_chat/")):
        params.setdefault("num_ctx", OLLAMA_CONTEXT)

    response = litellm.completion(
        model=model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": True,
            },  # fmt: skip
        },
        **params,
    )
    choice = response.choices[0]
    if not (choice.message.content or "").strip():
        thought = len(getattr(choice.message, "reasoning_content", None) or "")
        raise EmptyAnswer(
            f"{model} returned no answer (finish_reason={choice.finish_reason}, "
            f"{thought} characters of thinking); try a larger num_ctx or max_tokens"
        )
    return choice.message.content


def _anthropic(model: str, messages: list[dict], schema: type[BaseModel], **params) -> str:
    import anthropic

    client = anthropic.Anthropic()
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    chat = [m for m in messages if m["role"] != "system"]
    kwargs = {"system": system} if system else {}
    response = client.messages.parse(
        model=model,
        max_tokens=params.pop("max_tokens", 16000),
        messages=chat,
        output_format=schema,
        **kwargs,
        **params,
    )
    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise Refused(f"{model} declined ({category})")
    return json.dumps(response.parsed_output.model_dump(mode="json"))


register_backend("litellm", _litellm)
register_backend("anthropic", _anthropic)


def call(
    model: str,
    messages: list[dict],
    schema: type[BaseModel],
    *,
    api_base: str | None = None,
    allow_remote: bool | None = None,
    **params,
) -> str:
    guard(model, api_base, allow_remote)
    if api_base:
        params["api_base"] = api_base
    return _backends[backend_for(model)](model, messages, schema, **params)
