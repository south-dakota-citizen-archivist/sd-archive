"""sddocs: reading documents. See README.md."""

from . import clean, forms, links, ocr, tables
from ._pages import PageCache, kind, pages, sha256, text

__all__ = [
    "PageCache",
    "clean",
    "forms",
    "kind",
    "links",
    "ocr",
    "pages",
    "sha256",
    "tables",
    "text",
]
