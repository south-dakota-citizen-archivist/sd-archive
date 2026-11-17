"""The older reports' two-letter county codes (PE, MA, BN…), which no published list explains.

A code is read as a county from the people in both kinds of report: a DOC number with one
code in the older reports and one county court in the newer ones pairs the two. A code is
taken from that evidence when at least three people pair it with one county, at least 80% of
those it pairs, and the code starts with that county's first letter (PE, Pennington: 217 of
238). Other codes are settled by hand on the review pages (decisions in county_codes.toml,
sddocs.review), with the evidence and the counties whose names fit the letters; a code no
one has settled is left as printed, its records tied to the state only.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

DECISIONS = Path(__file__).with_name("county_codes.toml")
MIN_PEOPLE, MIN_SHARE = 3, 0.8


def evidence(older: dict[str, set], newer: dict[str, set]) -> dict[str, Counter]:
    """code → counties, counted by people: older is DOC number → the codes of a case that
    lists one, newer is DOC number → the counties of a case that lists one."""
    out: dict[str, Counter] = defaultdict(Counter)
    for doc, codes in older.items():
        counties = newer.get(doc, set())
        if len(codes) == 1 and len(counties) == 1:
            out[next(iter(codes))][next(iter(counties))] += 1
    return dict(out)


def from_evidence(code: str, counted: Counter) -> str | None:
    if not counted:
        return None
    county, n = counted.most_common(1)[0]
    total = sum(counted.values())
    if n >= MIN_PEOPLE and n / total >= MIN_SHARE and county[:1].upper() == code[:1].upper():
        return county
    return None


def mapping(codes: Counter, found: dict[str, Counter]) -> dict[str, dict]:
    """Every code the reports print: its county and how it was settled (evidence, by hand,
    or not: None)."""
    from sddocs import review

    decided = {k: d for (k, f), d in review.load(DECISIONS).items() if f == "county"}
    out = {}
    for code in sorted(codes):
        counted = found.get(code, Counter())
        county, how = from_evidence(code, counted), "evidence"
        if county is None and code in decided:
            county, how = decided[code].get("value"), "by hand"
        out[code] = {"county": county, "how": how if county else None, "printed": codes[code],
                     "evidence": dict(counted.most_common(4))}  # fmt: skip
    return out


def candidates(code: str, counted: Counter) -> list[str]:
    """Counties a code could be: those people pair it with, then those whose names fit its
    letters (the first letter first, then the second in order after it)."""
    import sdplaces

    names = [c.name.removesuffix(" County") for c in sdplaces.counties()]
    fits = [n for n in names if n[:1].upper() == code[:1].upper()
            and code[1:2].lower() in n[1:].lower()]  # fmt: skip
    return list(dict.fromkeys([*(c for c, _ in counted.most_common()), *fits]))[:6]


class CountyCodeReview:
    """Codes the evidence doesn't settle, for the review pages (review.providers)."""

    slug = "parole-county-codes"
    name = "Parole reports' county codes"
    decisions = DECISIONS
    applies = "on the next run (`just scrape parole-board-actions`)"

    def items(self) -> list:
        from django.urls import reverse

        from records.models import Run
        from sddocs import review

        run = (Run.objects.filter(recordset__slug="parole-board-actions")
               .exclude(checks={}).order_by("-started_at").first())  # fmt: skip
        codes = (run.checks or {}).get("county_codes", {}) if run else {}
        out = []
        for code, info in sorted(codes.items(), key=lambda kv: -kv[1]["printed"]):
            if info.get("how") == "evidence":
                continue
            counted = Counter(info.get("evidence") or {})
            options = candidates(code, counted)
            evidence_note = ", ".join(f"{c} ({n})" for c, n in counted.most_common()) or "none"
            out.append(review.Item(
                source=self.slug, key=code, kind="pick", title=f"County code {code}",
                subtitle=f"printed on {info['printed']} crimes in the older reports",
                links=[{"label": "the hearings",
                        "url": reverse("records:recordset_table", args=["parole-board-actions"])
                        + f"?q={code}"}],
                field="county", label="the county",
                readings={f"{i + 1}": c for i, c in enumerate(options)},
                problems=[f"People who appear in both kinds of report pair it with: "
                          f"{evidence_note}. Choose the county, or leave it empty if it "
                          f"isn't one (out of state)."],
                context={"placeholder": "A county's name: Bon Homme"}))  # fmt: skip
        return out

    def saved(self, key: str) -> None:
        pass

    def check(self, item, cells):
        return None

    def decided(self):
        from sddocs import review

        return review.load(self.decisions)


REVIEW = CountyCodeReview()
