"""Check a Census legislative district file against the redistricting act that drew the
districts.

The acts describe each senatorial district by whole counties, by the voting tabulation
districts (VTDs) of that decade's census, and for a VTD split between districts, by its
census blocks or by streets ("that portion of VTD-DG7 located north of Interstate 90").
This reads an act into, for each district: whole counties, whole VTDs, split VTDs, and
counties given as "the rest" ("Meade county except for that portion in District No. 33",
"that portion of Butte county that is not in District No. 29"). Then, for the Census file:

- every VTD the act puts wholly in a district must be (almost) wholly in it;
- every VTD the Census file has (almost) wholly in a district must be the act's there too;
- a VTD the Census file splits must be one the act splits.

Differences are listed by district; streets and blocks inside split VTDs aren't checked.

    uv run python scripts/verify_districts.py ACT.txt SLD.zip VTD_DIR [--house]
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import pandas as pd

import sdplaces

WHOLE = 0.99  # a VTD with this share of its area in one district is wholly in it


ABBREVIATIONS = [(r"\btownships?\b|\btwnshp\b|\btwshp\b", "twp"), (r"\bnorth\b", "n"),
                 (r"\bsouth\b", "s"), (r"\beast\b", "e"), (r"\bwest\b", "w")]  # fmt: skip


def key(text: str) -> str:
    """A VTD's name or code reduced to compare the act's spelling with the Census file's
    (dashes between numbers kept: Sioux Falls' 1-1 isn't 11)."""
    t = text.lower()
    t = re.sub(r"^\s*vtd[\s_-]*", " ", t)  # "VTD3-9", "VTD_Springdale"
    t = re.sub(r"\b(vtd|precincts?|voting district|sioux falls)\b", " ", t)
    for pattern, short in ABBREVIATIONS:
        t = re.sub(pattern, short, t)
    t = re.sub(r"(?<=\d)\s*-\s*(?=\d)", "-", t)
    return re.sub(r"[^a-z0-9-]", "", t).strip("-")


def lead(text: str) -> str:
    """A VTD's leading token: its number or code ("14 Oneota-…" → "14", "RC 2-6" → "rc26")."""
    t = re.sub(r"\b(vtd|precincts?|voting district)\b", " ", text.lower()).strip(" -")
    m = re.match(r"([a-z]{0,4}\s?[0-9]+(?:\s?-\s?[0-9]+)?[a-z]?)\b", t)
    return re.sub(r"[^a-z0-9-]", "", m.group(1).replace(" ", "")) if m else key(text)


def words(text: str) -> list[str]:
    """A name's words, camel case split ("AberdeenWard3" → aberdeen, ward, 3)."""
    text = re.sub(
        r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Za-z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text
    )
    drop = {"voting", "district", "vtd", "precinct", "prec"}
    out = [w.lower() for w in re.findall(r"[A-Za-z]+|[0-9]+", text) if w.lower() not in drop]
    return [COMPASS.get(w, w) for w in out]


# compass points as Census abbreviates them (Sturgis Northwest / SturgisNW, W.BlackHawk)
COMPASS = {"northwest": "nw", "northeast": "ne", "southwest": "sw", "southeast": "se",
           "north": "n", "south": "s", "east": "e", "west": "w",
           "nth": "n", "sth": "s"}  # fmt: skip


def _shortens(short: str, full: str) -> bool:
    """Whether short is an abbreviation of full: its first letter, then the rest in order."""
    if not short or not full or short[0] != full[0]:
        return False
    it = iter(full)
    return all(c in it for c in short)


def abbreviates(census: str, act: str) -> tuple[float, float]:
    """How well the Census file's (abbreviated) name lines up with the act's: (the share of
    words that pair up, how closely). A Census word pairs with an act word it shortens
    ("WHnson" → West Hanson) or nearly matches (Brotherfield / Brothersfield), in order;
    if that pairs too few, in any order (NWSturgis / Sturgis Northwest)."""
    import difflib

    short, full = words(census), words(act)
    if not short or not full:
        return 0.0, 0.0

    def close(w: str, x: str) -> float:
        if w == x:
            return 1.0
        ratio = difflib.SequenceMatcher(None, w, x).ratio()
        if _shortens(w, x):
            return max(ratio, 0.5)
        return ratio if ratio >= (0.75 if min(len(w), len(x)) >= 4 else 0.85) else 0.0

    def pair(ordered: bool) -> tuple[float, float]:
        left, hits, tight, at = list(full), 0, 0.0, 0
        for w in short:
            pool = range(at, len(left)) if ordered else range(len(left))
            best = max(pool, key=lambda k: close(w, left[k]), default=None)
            if best is not None and close(w, left[best]) > 0:
                hits, tight = hits + 1, tight + close(w, left[best])
                if ordered:
                    at = best + 1
                else:
                    left.pop(best)
        n = max(len(short), len(full))
        return hits / n, tight / n

    ordered = pair(True)
    return ordered if ordered[0] >= 0.75 else max(ordered, pair(False))


def tail(text: str) -> str:
    """A VTD's code at the end of its name ("Rapid City Voting District 2-3A" → "2-3a",
    "Watertown Precinct E-1" → "e1"; a dash after a letter doesn't count: Census writes E1)."""
    m = re.search(r"\b([A-Za-z]?-?\d+(?:-\d+)?[A-Za-z]?)\s*$", text.strip())
    return vtd_code(m.group(1)) if m else ""


def vtd_code(text: str) -> str:
    return re.sub(r"(?<=[a-z])-(?=\d)", "", text.lower().strip())


@dataclass
class District:
    counties: set[str] = field(default_factory=set)
    vtds: set[tuple[str, str]] = field(default_factory=set)  # (county, the act's VTD text)
    split: set[tuple[str, str]] = field(default_factory=set)
    # a split VTD's other districts, from "except for those portions in District No. 10"
    split_with: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    rest_of: set[str] = field(default_factory=set)  # counties given as "the rest"


def district_texts(act: str, house: bool) -> dict[str, str]:
    """Each district's text. For the House: the senatorial districts, with those split into
    single-member house districts (26A, 26B, 28A, 28B) in their place."""
    out = _texts(act, r"\(\d+\)\s*District (?:No\. )?(\d+)\s*:")  # 2001's (28) has no "No."
    if house:
        split = _texts(act, r"District No\. (\d+[AB])\s*:")
        for n in {re.match(r"\d+", k)[0] for k in split}:
            out.pop(n, None)
        out |= split
    return out


def _texts(act: str, marker: str) -> dict[str, str]:
    parts = list(re.finditer(marker, act))
    out = {}
    for m, nxt in zip(parts, [*parts[1:], None], strict=True):
        end = nxt.start() if nxt else len(act)
        out.setdefault(m.group(1), re.split(r"\bSection \d+\.", act[m.end() : end])[0])
    return out


COUNTY = r"([A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)?) [Cc]ounty"
# where a VTD's name ends and the description of the part of it ends
CUT = (
    r"\s*\(except\b|\s+(?:except|that is|that are|located|consisting|surrounded|lying|"
    r"north of|south of|east of|west of|bounded|containing)\b"
)


def items(listing: str) -> list[str]:
    """A clause's list of VTDs, split on commas and "and"."""
    listing = re.sub(r"^\s*(?:consisting of\s+)?(?:precincts?\s+)?", "", listing)
    if "VTD" not in listing:  # "00-Sturgis, 06-Tilford, …"; 2001: "Aberdeen Ward 3, …"
        return [
            p.strip(" .,")
            for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", listing)
            if p.strip(" .,")
        ]
    nxt = r"(?=(?:VTD|that portions?|those portions?|the portions?|Sioux Falls|Precinct|Voting|Block))"  # noqa: E501
    parts = re.split(rf",\s*(?:and\s+)?{nxt}|\s+and\s+{nxt}", listing)
    return [p.strip(" .,") for p in parts if p.strip(" .,")]


def parse(text: str) -> District:
    d = District()
    # clauses: by semicolons, (a) (b) markers, and each "that portion of <county> county"
    clauses = re.split(
        r";\s*(?:and\s+)?|\(\w\)\s+|,?\s*and (?=that portion of [A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)? [Cc]ounty)|,\s*(?=that portion of [A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)? [Cc]ounty)",  # noqa: E501
        text,
    )
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        # "Meade county except for that portion in District No. 33" / "… that is not in District"
        m = re.match(
            rf"(?:that portion of |The portion of )?{COUNTY}\s+(?:except for that portion|that is not in|not in)",  # noqa: E501
            clause,
            re.I,
        )  # noqa: E501
        if m:
            d.rest_of.add(m.group(1))
            continue
        m = re.match(
            rf"(?:and )?(?:that portion of|The portion of) {COUNTY}\s*(?:in\s+)?(.*)", clause, re.I
        )
        if m:
            county, rest = m.group(1), m.group(2)
            if m.group(0).lower().split(county.lower() + " county")[1].strip().startswith("in "):
                # 2021: "The portion of Brown County in VTD-precinct 15 …, the blocks of: …"
                d.split.add((county, re.split(r"[,;]\s*the blocks? of", rest)[0].strip()))
                continue
            # 2011/2001: "consisting of A, B, that portion of C located …, and D"
            for item in items(rest):
                if re.match(
                    r"(?:Block \d+|Tract \d+/Block|Pennington Census Blocks|[A-Z][a-z]+ Census Blocks)",  # noqa: E501
                    item,
                ):
                    continue  # a census block (of the split VTD before it, or listed alone)
                bare = re.sub(r"^(?:that |those |the )?portions? of\s+", "", item, flags=re.I)
                name = re.split(CUT, bare)[0].strip()
                if not name or name.lower() in ("portion", "that portion", "the portion"):
                    continue  # another part of the VTD just named
                if bare != item or name != bare:  # a part of the VTD
                    d.split.add((county, name))
                    others = set(re.findall(r"District No\. (\d+[AB]?)", bare))
                    d.split_with.setdefault((county, name), set()).update(others)
                elif re.match(r"(VTD|Sioux Falls Voting|Precinct|Voting|[A-Z0-9])", item):
                    d.vtds.add((county, item))
            continue
        # "Day, Marshall, and Roberts counties" (with an "and that portion …" after it)
        head = re.split(r",?\s*and that portion of|, that portion of", clause)[0]
        m = re.match(r"(.*?)\s+[Cc]ount(?:y|ies)\b", head)
        if m:
            for name in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(1)):
                if name.strip():
                    d.counties.add(name.strip())
            tail = clause[len(head) :]
            if tail.strip():
                sub = parse(re.sub(r"^,?\s*and\s+", "", tail))
                d.vtds |= sub.vtds
                d.split |= sub.split
                d.rest_of |= sub.rest_of
                d.counties |= sub.counties
    return d


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("act", type=Path)
    ap.add_argument("sld", type=Path)
    ap.add_argument("vtds", type=Path)
    ap.add_argument("--house", action="store_true")
    args = ap.parse_args()

    act = " ".join(args.act.read_text().split())
    plan = {n: parse(t) for n, t in district_texts(act, args.house).items()}
    sld = gpd.read_file(f"zip://{args.sld}").to_crs(5070)
    num = [c for c in sld.columns if c.startswith("SLD") and "ST" in c][0]
    sld["d"] = sld[num].str.lstrip("0")
    vtd = pd.concat([gpd.read_file(f"zip://{f}") for f in sorted(args.vtds.glob("*.zip"))])
    vtd = gpd.GeoDataFrame(vtd, crs=vtd.crs).to_crs(5070)
    col = {k: [x for x in vtd.columns if x.startswith(k)][0] for k in ("COUNTYFP", "VTDST", "NAME")}
    names = {
        p.geoid[2:]: p.short_name for p in sdplaces.all_places("county", include_historical=True)
    }
    vtd["county"] = vtd[col["COUNTYFP"]].map(names)
    vtd["vtd"] = vtd[col["VTDST"]] + "|" + vtd[col["NAME"]]

    # the act's VTD names → the Census file's VTDs, county by county
    by_county = defaultdict(list)
    for county, code_name in zip(vtd["county"], vtd["vtd"], strict=True):
        by_county[county].append(code_name)

    def find(county: str, text: str) -> str | None:
        found = [v for v in by_county.get(county, []) if key(v.split("|")[1]) == key(text)
                 or key(v.split("|")[0]) == key(text)]  # fmt: skip
        if len(found) != 1:
            found = [v for v in by_county.get(county, []) if lead(v.split("|")[1]) == lead(text)]
        if len(found) != 1 and len(key(text)) >= 5:  # "Springdale" for "SpringdaleTwp"
            found = [
                v for v in by_county.get(county, []) if key(v.split("|")[1]).startswith(key(text))
            ]
        if len(found) != 1:  # 2000's abbreviations: "Lbrt-Hcla-…-DtrtTwp" for "Liberty-Hecla-…"
            scored = sorted(((abbreviates(v.split("|")[1], text), v) for v in by_county.get(county, [])),  # noqa: E501
                            reverse=True)  # fmt: skip
            if (
                scored
                and scored[0][0][0] >= 0.75
                and (len(scored) == 1 or scored[1][0] < scored[0][0])
            ):
                found = [scored[0][1]]
        return found[0] if len(found) == 1 else None

    unknown = []
    act_whole, act_split = {}, defaultdict(set)
    for n, d in plan.items():
        for county, text in d.vtds:
            v = find(county, text)
            if v is None:
                unknown.append((n, county, text))
            else:
                act_whole[(county, v)] = n
        for county, text in d.split:
            v = find(county, text)
            if v is None:
                unknown.append((n, county, text + " (split)"))
            else:
                act_split[(county, v)] |= {n, *d.split_with.get((county, text), ())}

    pieces = gpd.overlay(vtd[["county", "vtd", "geometry"]], sld[["d", "geometry"]],
                         how="intersection", keep_geom_type=True)  # fmt: skip
    pieces["area"] = pieces.area
    share = pieces.groupby(["county", "vtd", "d"])["area"].sum().reset_index()
    share["share"] = share["area"] / share.groupby(["county", "vtd"])["area"].transform("sum")
    share = share[share["share"] > 0.001]

    problems = defaultdict(list)
    for (county, v), rows in share.groupby(["county", "vtd"]):
        census = dict(zip(rows["d"], rows["share"], strict=True))
        top = max(census, key=census.get)
        label = f"{county} {v.split('|')[1]}"
        if (county, v) in act_split:
            if not set(census) <= act_split[(county, v)] | {
                    n for n, d in plan.items() if county in d.counties | d.rest_of}:  # fmt: skip
                problems[top].append(f"{label}: the act splits it among {sorted(act_split[(county, v)])}; "  # noqa: E501
                                     f"Census has {fmt(census)}")  # fmt: skip
            continue
        if (county, v) in act_whole:
            n = act_whole[(county, v)]
            if census.get(n, 0) < WHOLE:
                problems[n].append(f"{label}: the act puts it all in {n}; Census has {fmt(census)}")
            continue
        homes = [n for n, d in plan.items() if county in d.counties]
        rests = [n for n, d in plan.items() if county in d.rest_of]
        if len(homes) == 1 and census.get(homes[0], 0) >= WHOLE:
            continue
        if rests and census.get(top, 0) >= WHOLE and top in rests:
            continue
        problems[top].append(f"{label}: the act doesn't name it; Census has {fmt(census)}")

    bad = 0
    for n in sorted(set(sld["d"]), key=lambda x: (int(re.match(r"\d+", x)[0]), x)):
        if n not in plan:
            problems[n].append("not in the act")
        status = "ok" if not problems.get(n) else "check"
        bad += status == "check"
        print(f"District {n}: {status}")
        for p in problems.get(n, []):
            print(f"    {p}")
    for n, county, text in unknown:
        print(
            f"  the act's {county} County {text!r} (District {n}): no such VTD in the Census file"
        )
    print(f"{bad} district(s) to check; {len(unknown)} names not found")


def fmt(census: dict) -> str:
    return ", ".join(f"{d} {s:.0%}" for d, s in sorted(census.items(), key=lambda x: -x[1]))


if __name__ == "__main__":
    main()
