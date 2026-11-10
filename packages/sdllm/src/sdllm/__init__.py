"""sdllm: language models for reading records. See README.md."""

from ._backends import (
    CLAUDE_DEFAULT,
    EmptyAnswer,
    Refused,
    RemoteCallBlocked,
    backend_for,
    is_local,
    register_backend,
)
from ._core import (
    DEFAULT_MODEL,
    Cache,
    Classification,
    Extraction,
    ask,
    classify,
    evaluate,
    extract,
    quote_found,
)

__all__ = [
    "CLAUDE_DEFAULT",
    "DEFAULT_MODEL",
    "EmptyAnswer",
    "Cache",
    "Classification",
    "Extraction",
    "Refused",
    "RemoteCallBlocked",
    "ask",
    "backend_for",
    "classify",
    "evaluate",
    "extract",
    "is_local",
    "quote_found",
    "register_backend",
]
