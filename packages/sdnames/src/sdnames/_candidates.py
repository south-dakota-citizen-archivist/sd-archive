"""Finding compound surnames in a list of names, for review before they go in surnames.txt."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from ._lexicon import GENERATIONS, PARTICLES, compound_surnames, given_names
from ._normalize import normalize

_ASIDE = re.compile(r"\"[^\"]*\"|\([^)]*\)|“[^”]*”")
_TITLES = frozenset(["DR", "REP", "SEN", "MR", "MRS", "MS", "HON", "REV"])


def surname_candidates(names: Iterable[str]) -> Counter:
    """Multi-word family names in names, with how often each appears, leaving out particle
    names (VAN CAMP) and ones already listed. Comma names (FAMILY, GIVEN) give the family
    part; given-first names give the words after the first word that isn't a given name or
    an initial. Every candidate needs a look: organizations and double given names slip in."""
    names = [n for n in names if n and not any(c.isdigit() for c in n)]
    firsts = Counter()
    for n in names:
        if "," not in n:
            words = normalize(_ASIDE.sub(" ", n)).split()
            if words:
                firsts[words[0]] += 1
    given = given_names() | {w for w, c in firsts.items() if c >= 2}
    known = compound_surnames()
    found: Counter = Counter()
    for n in names:
        if "&" in n or " AND " in n.upper():
            continue
        if "," in n:
            words = normalize(n.split(",", 1)[0]).split()
        else:
            words = [w for w in normalize(_ASIDE.sub(" ", n)).split() if w not in _TITLES]
            while words and words[-1] in GENERATIONS:
                words.pop()
            start = next((i for i in range(1, len(words) - 1)
                          if len(words[i]) > 1 and words[i] not in given), None)  # fmt: skip
            words = words[start:] if start else []
        while words and words[-1] in GENERATIONS:
            words.pop()
        if len(words) < 2 or words[0] in PARTICLES or tuple(words) in known:
            continue
        found[" ".join(words)] += 1
    return found
