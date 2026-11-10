"""Word lists: compound surnames, surname particles, generational suffixes, given names."""

from __future__ import annotations

from functools import cache
from importlib.resources import files

from ._normalize import normalize

# Words that start a family name and join the next word to it: VAN CAMP, MC PHERSON, ST JOHN.
PARTICLES = frozenset(
    [
        "VAN",
        "VON",
        "DE",
        "DEL",
        "DELLA",
        "DER",
        "DEN",
        "DI",
        "DA",
        "DU",
        "DOS",
        "LA",
        "LE",
        "MC",
        "MAC",
        "ST",
        "SAINT",
        "TEN",
        "TER",
        "VER",
        "VANDER",
        "VANDEN",
        "O",
    ]
)
GENERATIONS = frozenset(["JR", "SR", "II", "III", "IV"])


@cache
def compound_surnames() -> frozenset[tuple[str, ...]]:
    """Multi-word family names that aren't particle names (BRINGS PLENTY, TWO BULLS), from
    surnames.txt, as tuples of normalized words."""
    text = files("sdnames").joinpath("surnames.txt").read_text()
    out = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(tuple(normalize(line).split()))
    return frozenset(out)


@cache
def given_names() -> frozenset[str]:
    """English given names and nicknames (from the nicknames dataset), normalized."""
    from nicknames import name_triplets

    names = set()
    for t in name_triplets():
        names.add(normalize(t.name1))
        names.add(normalize(t.name2))
    return frozenset(names)


def is_family(words: list[str]) -> bool:
    """These words are a known compound surname: listed whole (TWO BULLS), or joined by
    hyphens from listed surnames and single words (BORDEAUX-BLACK BEAR,
    DIAZ-TAKES THE SHIELD)."""
    known = compound_surnames()
    parts = [tuple(normalize(p).split()) for p in " ".join(words).split("-")]
    parts = [p for p in parts if p]
    if not any(p in known for p in parts):
        return False
    return all(len(p) == 1 or p in known for p in parts)


def family_length_front(words: list[str]) -> int:
    """How many of the leading words are the family name, for names written family first."""
    for k in range(min(5, len(words) - 1), 1, -1):
        if is_family(words[:k]):
            return k
    n = 0
    while n < len(words) - 1 and normalize(words[n]) in PARTICLES:
        n += 1
    return n + 1


def family_length_back(words: list[str]) -> int:
    """How many of the trailing words are a known compound surname (0 if none), for names
    written given first. At least one word is left for the given name."""
    for k in range(min(5, len(words) - 1), 0, -1):
        if is_family(words[-k:]):
            return k
    return 0
