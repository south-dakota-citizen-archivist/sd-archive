"""sddocs: reading documents. See README.md."""

from . import clean, forms, ocr, tables
from ._pages import PageCache, kind, pages, sha256, text

__all__ = ["PageCache", "clean", "forms", "kind", "ocr", "pages", "sha256", "tables", "text"]
