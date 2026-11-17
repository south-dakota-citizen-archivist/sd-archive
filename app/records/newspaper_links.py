"""Newspaper ownership and circulation statements (the Secretary of State's, one a paper a
year) linked to the paper in the press association's directory.

A statement gives only the paper's name, as filed, and the spelling wanders over the years
("Corisca Globe", "Faulk County Record LLC", "The Nation Center News"). A statement is
linked when its name, normalized (case, a leading "The", "&" for "and", punctuation, and a
business suffix such as Inc. or LLC), is a directory paper's name normalized the same way;
no two directory papers share one. Every other name with a candidate (a directory
paper in a town the name gives, or with close distinctive words: candidates()) goes to the
review pages (sddocs.review), with the latest statement for its city, to choose one or to
say the paper isn't in the directory (closed, merged, not a member). A name with no
candidate is left unlinked: nearly all are papers that have closed.
Decisions are kept in links/newspapers.toml, keyed by the normalized name, so they cover
every spelling that normalizes alike, and applied whenever the links are rebuilt
(`manage.py link_records`, and at once after a decision).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

KIND = "newspaper"
STATEMENTS = "newspaper-circulation"  # the dataset
DIRECTORY = "newspaper-directory"  # the record set
DECISIONS = Path(__file__).with_name("links") / "newspapers.toml"
SUFFIX = re.compile(r"\s+(inc|llc|ltd|corp|co inc|company)$")


# words nearly every paper's name has: candidates are found by the rest (and by town)
GENERIC = frozenset(
    [
        "news",
        "times",
        "time",
        "journal",
        "herald",
        "leader",
        "tribune",
        "record",
        "register",
        "press",
        "pioneer",
        "enterprise",
        "review",
        "courier",
        "independent",
        "post",
        "sun",
        "star",
        "standard",
        "weekly",
        "daily",
        "county",
        "of",
        "and",
        "area",
        "edition",
        "publishing",
        "publishers",
        "publications",
        "gazette",
        "chronicle",
        "clarion",
        "booster",
        "argus",
        "dakotan",
        "messenger",
        "advocate",
        "reporter",
        "observer",
        "bulletin",
        "echo",
        "union",
        "champion",
        "serving",
        "inc",
        "llc",
    ]
)
CANDIDATE_SCORE = 80


def candidates(normalized: str, papers: dict[str, dict]) -> list[str]:
    """Directory papers a statement's name could be (their record keys): those in a town the
    name gives, then those whose distinctive words are close (token set ratio 80 or more),
    best first, four at most."""
    from rapidfuzz import fuzz

    def distinct(s: str) -> str:
        return " ".join(w for w in s.split() if w not in GENERIC)

    out = []
    for p in papers.values():
        for city in p["city"].split("/"):
            if re.search(rf"\b{re.escape(norm(city))}\b", normalized):
                out.append(p["key"])
    mine = distinct(normalized)
    scored = sorted(((fuzz.token_set_ratio(mine, distinct(k)), p["key"])
                     for k, p in papers.items()), reverse=True) if mine else []  # fmt: skip
    out += [key for score, key in scored if score >= CANDIDATE_SCORE]
    return list(dict.fromkeys(out))[:4]


def norm(name: str | None) -> str:
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"^the\s+", "", s.strip())
    s = " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())
    while (m := SUFFIX.search(s)) is not None:
        s = s[: m.start()]
    return s


def slug(normalized: str) -> str:
    return normalized.replace(" ", "-")


def directory() -> dict[str, dict]:
    """The directory's papers by normalized name."""
    from records.models import Record

    out: dict[str, dict] = {}
    for pk, key, data in Record.objects.current().filter(recordset__slug=DIRECTORY).values_list(
            "pk", "key", "data"):  # fmt: skip
        out[norm(data["publication_name"])] = {"pk": pk, "key": key,
                                               "name": data["publication_name"],
                                               "city": data["city"]}  # fmt: skip
    return out


def statements() -> dict[str, dict]:
    """The statements by their paper's normalized name: the names as filed, the years and
    the statements' record IDs."""
    from records.models import Record

    out: dict[str, dict] = defaultdict(lambda: {"names": Counter(), "years": [], "pks": []})
    for pk, data in Record.objects.current().filter(recordset__dataset=STATEMENTS).values_list(
            "pk", "data"):  # fmt: skip
        name = data.get("newspaper") or ""
        group = out[norm(name)]
        group["names"][name] += 1
        group["pks"].append(pk)
        if data.get("year"):
            group["years"].append(data["year"])
    out.pop("", None)
    return dict(out)


@dataclass
class Result:
    links: int = 0
    names: Counter = field(default_factory=Counter)  # exact, by hand, not in directory, …


def link_newspapers(*, dry_run: bool = False) -> Result:
    """Rebuild the links from statements to directory papers."""
    from django.db import transaction

    from records.models import Record, RecordLink
    from sddocs import review

    papers = directory()
    by_key = {p["key"]: p for p in papers.values()}
    decided = {k: d for (k, f), d in review.load(DECISIONS).items() if f == "paper"}
    out, links = Result(), []
    names_by_pk = dict(Record.objects.filter(recordset__dataset=STATEMENTS).values_list(
        "pk", "data__newspaper"))  # fmt: skip
    for normalized, group in statements().items():
        paper = papers.get(normalized)
        how = "exact"
        if paper is None:
            d = decided.get(slug(normalized))
            if d is None:
                waiting = bool(candidates(normalized, papers))
                out.names["waiting for review" if waiting else "no close name or town"] += 1
                continue
            if d.get("value") is None:
                out.names["not in the directory"] += 1
                continue
            paper = by_key.get(d["value"])
            if paper is None:
                out.names["decided, but the paper's gone from the directory"] += 1
                continue
            how = "by hand"
        out.names[how] += 1
        for pk in group["pks"]:
            links.append(RecordLink(from_record_id=pk, to_record_id=paper["pk"], kind=KIND,
                                    from_field="newspaper", to_field="publication_name",
                                    value=(names_by_pk.get(pk) or "")[:200]))  # fmt: skip
    out.links = len(links)
    if not dry_run:
        with transaction.atomic():
            old = RecordLink.objects.filter(kind=KIND, from_record__recordset__dataset=STATEMENTS)
            old.delete()
            RecordLink.objects.bulk_create(links, batch_size=5000)
    return out


# --- the review pages ------------------------------------------------------------------


class NewspaperReview:
    """Statement names no directory paper has, for the review pages (review.providers)."""

    slug = "newspaper-links"
    name = "Newspaper statements ↔ the press directory"
    decisions = DECISIONS
    applies = "at once (the links are rebuilt)"

    def items(self) -> list:
        from django.urls import reverse

        from records.models import Document, Record
        from sddocs import review

        papers = directory()
        out = []
        groups = sorted(statements().items(), key=lambda kv: -len(kv[1]["pks"]))
        for normalized, group in groups:
            if normalized in papers:
                continue
            found = candidates(normalized, papers)
            if not found:
                continue  # no directory paper shares its town or its distinctive words
            readings = dict(zip(("1st", "2nd", "3rd", "4th"), found, strict=False))
            years = sorted(group["years"])
            latest = Record.objects.filter(pk__in=group["pks"]).order_by("-data__year").first()
            document = Document.objects.filter(record=latest).first() if latest else None
            names = [n for n, _ in group["names"].most_common()]
            out.append(review.Item(
                source=self.slug, key=slug(normalized), kind="pick", title=names[0],
                subtitle=(f"{len(group['pks'])} statement{'s' if len(group['pks']) != 1 else ''}"
                          + (f", {years[0]}–{years[-1]}" if years else "")
                          + (f"; also filed as {', '.join(names[1:4])}" if names[1:] else "")),
                links=[{"label": f"its {latest.data.get('year')} statement",
                        "url": latest.get_absolute_url()}] if latest else [],
                image={"embed": reverse("records:document_file", args=[document.pk])}
                if document else {},
                field="paper", label="the paper in the press directory", readings=readings,
                context={"placeholder": "Use one above (or a directory paper's name and town, "
                                        "as listed: Plain Talk (Vermillion))"},
                problems=["No paper in the directory has this name. Choose the one it is (the "
                          "statement gives its city; papers in a town the name gives come "
                          "first), or leave it empty if it isn't in the directory: closed, "
                          "merged into another, or not a member."]))  # fmt: skip
        return out

    def saved(self, key: str) -> None:
        link_newspapers()

    def check(self, item, cells):
        return None

    def decided(self):
        from sddocs import review

        return review.load(self.decisions)


REVIEW = NewspaperReview()
