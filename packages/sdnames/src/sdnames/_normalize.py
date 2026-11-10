"""One normalization rule for names, in Python and in SQL."""

from __future__ import annotations

import re
import unicodedata

from anyascii import anyascii

_NON_ALNUM = re.compile(r"[^0-9A-Z]+")


def normalize(value: str | None) -> str | None:
    """Uppercase, accents removed, everything but letters and digits to one space, trimmed.

    The same result as the SQL function from normalize_sql() for Latin-script text. (The SQL
    keeps letters from other scripts; this transliterates them.)"""
    if value is None:
        return None
    # letters and digits transliterated (Ø → O, ß → SS); symbols (™, ©) become spaces
    text = "".join(anyascii(c) if unicodedata.category(c)[0] in "LN" else " " for c in value)
    return _NON_ALNUM.sub(" ", text.upper()).strip()


def normalize_sql(name: str = "sd_normalize_name") -> str:
    """CREATE FUNCTION for the same rule in Postgres (needs the unaccent extension).
    unaccent() isn't IMMUTABLE on its own, so it's called with an explicit dictionary inside a
    function declared IMMUTABLE, which lets indexes use it."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        raise ValueError(f"not a plain SQL name: {name!r}")
    return rf"""
CREATE OR REPLACE FUNCTION {name}(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS $$
    SELECT btrim(regexp_replace(
        regexp_replace(upper(public.unaccent('public.unaccent'::regdictionary, value)),
                       '[^[:alnum:]]+', ' ', 'g'),
        '\s+', ' ', 'g'))
$$;
"""
