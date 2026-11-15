"""A redistricting plan as census blocks: every block of the state assigned to its senatorial
and house district, read from the act that drew the plan.

The acts name each district's whole counties, whole voting districts (VTDs), counties given
as the rest ("Meade county except for that portion in District No. 33"), and for a VTD they
split, either its blocks (2021: by GEOID; 2011: "that portion of VTD-WL15 consisting of
Block 1239, …") or streets ("that portion of VTD-DG7 located north of Interstate 90").
A block goes to its district by the most precise thing that names it:

1. "act: block": a block the act lists;
2. "act: VTD": a VTD it names whole;
3. "act: county": a county it names whole;
4. "act: rest of county": the rest of a county a district takes "except for" others;
5. "reference: streets": a block of a VTD the act splits by streets, placed by the
   reference district file (Census's), and only into a district the act names for that VTD;
6. "omitted, attached": a block the act leaves out, attached as its omitted-area section
   prescribes: to the district that surrounds it, or if it touches several, the one with the
   least population (needs --population).

Anything else is reported. With --check, the result is compared block by block with a
Census district file (by each block's internal point).

    uv run python scripts/plan_blocks.py ACT.txt BAF_DIR VTD.zip BLOCKS.zip OUT.csv \\
        [--reference SLDU.zip SLDL.zip] [--check SLDU.zip SLDL.zip]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import pandas as pd

import sdplaces

sys.path.insert(0, str(Path(__file__).parent))
from verify_districts import (  # noqa: E402
    COUNTY,
    CUT,
    _texts,
    abbreviates,
    items,
    key,
    lead,
    tail,
    vtd_code,
)  # fmt: skip

BLOCK = r"46\d{13}"
PORTION = r"that portion of [A-Z][A-Za-z]+(?: [A-Z][A-Za-z]+)? [Cc]ounty"


@dataclass
class Named:
    """What a district's text names."""

    counties: set[str] = field(default_factory=set)
    rest_of: set[str] = field(default_factory=set)
    vtds: list[tuple[str, str]] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    vtd_blocks: list[tuple[str, str, str]] = field(default_factory=list)  # county, VTD, "1239"
    by_streets: list[tuple[str, str]] = field(default_factory=list)
    tract_blocks: list[tuple[str, str, str]] = field(default_factory=list)  # county, tract, block
    split_blocks: list[tuple[str, str, str]] = field(default_factory=list)  # split by streets


def read(text: str) -> Named:
    out = Named()
    clauses = re.split(rf";\s*(?:and\s+)?|\(\w\)\s+|,?\s*and (?={PORTION})|,\s*(?={PORTION})", text)
    for clause in clauses:
        clause = clause.strip(" .")
        if not clause:
            continue
        m = re.match(rf"(?:that portion of |The portion of )?{COUNTY}\s+"
                     r"(?:except for that portion|that is not in|not in)", clause, re.I)  # fmt: skip  # noqa: E501
        if m:
            out.rest_of.add(m.group(1))
            continue
        m = re.match(rf"(?:and )?(?:that portion of|The portion of) {COUNTY}\s*(.*)", clause, re.I)
        if m:
            county, rest = m.group(1), m.group(2)
            if re.search(BLOCK, rest):
                out.blocks += re.findall(BLOCK, rest)
                continue
            last = ""
            streets_listed = "containing the streets" in rest
            for item in items(rest):
                m2 = re.search(r"Tract (\d{6})/Block (\d{4})", item)
                if m2 and re.match(
                    r"(?:that portion of )?Tract|(?:[A-Z][a-z]+ )+Census Blocks", item
                ):
                    # 2001: "Tract 011003/Block 3005"; "that portion of Tract …/Block … containing
                    # the streets of …" is a block split by streets: it goes where the reference puts it  # noqa: E501
                    split = item.startswith("that portion")
                    (out.split_blocks if split else out.tract_blocks).append(
                        (county, m2.group(1), m2.group(2))
                    )
                    continue
                if streets_listed and not re.search(
                    r"\d|Voting|VTD|Precinct|Ward|Township|Twp", item
                ):
                    continue  # a street of the block before it ("…, Hanks Drive, …")
                if re.match(r"Block \d+", item):  # a block of the split VTD before it
                    if last:
                        out.vtd_blocks.append((county, last, item.split()[1]))
                    continue
                bare = re.sub(r"^(?:that |those |the )?portions? of\s+", "", item, flags=re.I)
                name = re.split(CUT, bare)[0].strip()
                if not name or name.lower() in ("portion", "that portion", "the portion"):
                    continue
                if bare != item or name != bare:
                    last = name
                    numbers = re.findall(r"Block (\d{4})", bare)
                    if numbers:
                        out.vtd_blocks += [(county, name, b) for b in numbers]
                    else:
                        out.by_streets.append((county, name))
                else:
                    out.vtds.append((county, item))
            continue
        if re.search(BLOCK, clause):
            out.blocks += re.findall(BLOCK, clause)
            continue
        m = re.match(r"(.*?)\s+[Cc]ount(?:y|ies)\b", clause)
        if m:
            out.counties |= {n.strip() for n in re.split(r",\s*(?:and\s+)?|\s+and\s+", m.group(1))
                             if n.strip()}  # fmt: skip
    return out


def read_baf(path: Path) -> list[dict]:
    text = path.read_text()
    return list(csv.DictReader(text.splitlines(), delimiter="|" if "|" in text[:200] else ","))


def district_of(points: gpd.GeoDataFrame, path: Path) -> dict[str, str]:
    sld = gpd.read_file(f"zip://{path}").to_crs(points.crs)
    num = [c for c in sld.columns if c.startswith("SLD") and "ST" in c][0]
    j = gpd.sjoin(points, sld[[num, "geometry"]], predicate="within", how="left")
    j = j[~j.index.duplicated()]
    return dict(zip(j["block"], j[num].fillna("").str.lstrip("0"), strict=True))


def main() -> None:
    ap = argparse.ArgumentParser()
    for a in ("act", "baf", "vtd", "blocks", "out"):
        ap.add_argument(a, type=Path)
    ap.add_argument("--reference", nargs=2, type=Path, metavar=("SLDU", "SLDL"))
    ap.add_argument("--check", nargs=2, type=Path, metavar=("SLDU", "SLDL"))
    ap.add_argument("--population", type=Path, help="the census's blocks_<year>.parquet")
    args = ap.parse_args()
    population: dict[str, int] = {}
    if args.population:
        counts = pd.read_parquet(args.population)
        total = next(c for c in ("P1_001N", "P001001", "PL001001") if c in counts.columns)
        population = dict(zip(counts["block"], counts[total].fillna(0).astype(int), strict=True))

    act = " ".join(args.act.read_text().split())
    senate = _texts(act, r"\(\d+\)\s*District (?:No\. )?(\d+)\s*:")  # 2001's (28) has no "No."
    house_split = _texts(act, r"District No\. (\d+[AB])\s*:")
    house = {
        n: t for n, t in senate.items() if n not in {k[:-1] for k in house_split}
    } | house_split
    names = {
        p.geoid[2:]: p.short_name for p in sdplaces.all_places("county", include_historical=True)
    }

    files = sorted(args.vtd.glob("*.zip")) if args.vtd.is_dir() else [args.vtd]  # 2010: by county
    vtd = pd.concat([gpd.read_file(f"zip://{f}") for f in files])
    col = {k: [x for x in vtd.columns if x.startswith(k)][0] for k in ("COUNTYFP", "VTDST", "NAME")}
    vtd_names = defaultdict(list)
    for cfp, code, name in zip(
        vtd[col["COUNTYFP"]], vtd[col["VTDST"]], vtd[col["NAME"]], strict=True
    ):
        vtd_names[names[cfp]].append((code, name))

    shapes = gpd.read_file(f"zip://{args.blocks}")
    gid = [c for c in shapes.columns if c.startswith(("GEOID", "BLKIDFP"))][0]
    blockce = [c for c in shapes.columns if c.startswith("BLOCKCE")][0]
    lat = [c for c in shapes.columns if c.startswith("INTPTLAT")][0]
    lon = [c for c in shapes.columns if c.startswith("INTPTLON")][0]
    shapes = shapes.rename(columns={gid: "block"})
    points = gpd.GeoDataFrame({"block": shapes["block"]}, crs=shapes.crs,
                              geometry=gpd.points_from_xy(shapes[lon].astype(float),
                                                          shapes[lat].astype(float)))  # fmt: skip
    number = dict(zip(shapes["block"], shapes[blockce], strict=True))

    # each block's county and VTD: Census's assignment file, or (2000, which has none) the VTD
    # its internal point is in (2000's VTDs are made of whole blocks)
    vtd_file = (
        next(args.baf.glob("BlockAssign_ST46_SD_VTD.txt"), None) if args.baf.is_dir() else None
    )
    if vtd_file:
        rows = read_baf(vtd_file)
        block_vtd = {r["BLOCKID"]: (names[r["COUNTYFP"]], r["DISTRICT"]) for r in rows}
    else:
        j = gpd.sjoin(points, vtd[[col["COUNTYFP"], col["VTDST"], "geometry"]].to_crs(points.crs),
                      predicate="within", how="left")  # fmt: skip
        j = j[~j.index.duplicated()]
        block_vtd = {b: (names[b[2:5]], code if isinstance(code, str) else "")
                     for b, code in zip(j["block"], j[col["VTDST"]], strict=True)}  # fmt: skip
    blocks_of_vtd = defaultdict(list)
    for b, cv in block_vtd.items():
        blocks_of_vtd[cv].append(b)
    fips = {v: k for k, v in names.items()}

    def find(county: str, text: str) -> str | None:
        options = vtd_names.get(county, [])
        for test in (lambda c, n: key(n) == key(text) or key(c) == key(text),
                     lambda c, n: lead(n) == lead(text),
                     lambda c, n: bool(tail(text)) and vtd_code(c) == tail(text)):  # fmt: skip
            found = [c for c, n in options if test(c, n)]
            if len(found) == 1:
                return found[0]
        if len(key(text)) >= 5:
            found = [c for c, n in options if key(n).startswith(key(text))]
            if len(found) == 1:
                return found[0]
        scored = sorted(((abbreviates(n, text), c) for c, n in options), reverse=True)
        if scored and scored[0][0][0] >= 0.75 and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            return scored[0][1]
        return None

    neighbors = None

    def assign(texts: dict[str, str], reference: dict[str, str] | None):
        nonlocal neighbors
        problems: list[str] = []
        levels: dict[str, dict[str, str]] = defaultdict(dict)

        def put(level: str, b: str, n: str, what: str) -> None:
            if b in levels[level] and levels[level][b] != n:
                problems.append(f"{what}: named for {levels[level][b]} and {n}")
            levels[level][b] = n

        streets = defaultdict(set)  # (county, VTD code) → districts that take part of it by streets
        split_block = defaultdict(set)  # a block the act splits by streets → its districts
        rest_of = defaultdict(set)
        for n, text in texts.items():
            named = read(text)
            for b in named.blocks:
                put("act: block", b, n, f"block {b}")
            for county, tract, block in named.split_blocks:
                split_block[f"46{fips[county]}{tract}{block}"].add(n)
            for county, tract, block in named.tract_blocks:
                b = f"46{fips[county]}{tract}{block}"
                if b not in block_vtd:
                    problems.append(f"District {n}: no block {county} {tract}/{block}")
                put("act: block", b, n, f"block {b}")
            for county, name, num in named.vtd_blocks:
                code = find(county, name)
                found = [b for b in blocks_of_vtd.get((county, code), []) if number[b] == num]
                if len(found) != 1:
                    problems.append(
                        f"District {n}: {county} {name} Block {num}: {len(found)} blocks"
                    )
                for b in found:
                    put("act: block", b, n, f"{county} {name} Block {num}")
            for county, name in named.vtds:
                code = find(county, name)
                if code is None:
                    problems.append(f"District {n}: no VTD {name!r} in {county} County")
                for b in blocks_of_vtd.get((county, code), []):
                    put("act: VTD", b, n, f"{county} {name}")
            for county, name in named.by_streets:
                code = find(county, name)
                if code is None:
                    problems.append(
                        f"District {n}: no VTD {name!r} in {county} County (by streets)"
                    )
                streets[(county, code)].add(n)
            for county in named.counties:
                if county not in names.values():
                    problems.append(f"District {n}: no county {county!r}")
            for b, (county, _) in block_vtd.items():
                if county in named.counties:
                    put("act: county", b, n, f"{county} County")
            for county in named.rest_of:
                rest_of[county].add(n)
        out, how = {}, {}
        order = ["act: block", "act: VTD", "act: county"]
        for b in block_vtd:
            for level in order:
                if b in levels[level]:
                    out[b], how[b] = levels[level][b], level
                    break
        for b, districts in split_block.items():  # split between districts by streets
            if reference and reference.get(b) in districts | {out.get(b)}:
                out[b], how[b] = reference[b], "reference: block split by streets"
        for b, (county, code) in block_vtd.items():
            if b in out:
                continue
            part_of = streets.get((county, code))
            if part_of and reference and reference.get(b) in part_of | rest_of.get(county, set()):
                out[b], how[b] = reference[b], "reference: streets"
            elif len(rest_of.get(county, ())) == 1 and not part_of:
                out[b], how[b] = next(iter(rest_of[county])), "act: rest of county"
            elif part_of and rest_of.get(county) and len(part_of | rest_of[county]) == 1:
                out[b], how[b] = next(iter(part_of | rest_of[county])), "act: rest of county"
        missing = [b for b in block_vtd if b not in out]
        if missing:  # the act's omitted-area section: the district all its neighbors are in
            if neighbors is None:
                touching = gpd.sjoin(shapes[["block", "geometry"]], shapes[["block", "geometry"]],
                                     predicate="touches")  # fmt: skip
                neighbors = touching.groupby("block_left")["block_right"].apply(set).to_dict()
            people = Counter()
            for b, n in out.items():
                people[n] += population.get(b, 0)
            # an omitted area (blocks next to each other) goes as a whole: to the district
            # around it, or if it touches several, the one with the least population
            left = set(missing)
            while left:
                area, edge = set(), [left.pop()]
                while edge:
                    b = edge.pop()
                    area.add(b)
                    nxt = [x for x in neighbors.get(b, ()) if x in left]
                    left -= set(nxt)
                    edge += nxt
                around = {out.get(x) for b in area for x in neighbors.get(b, ())} - {None}
                if len(around) == 1:
                    n, label = around.pop(), "omitted, attached"
                elif around and population:
                    n = min(sorted(around), key=lambda d: people[d])
                    label = "omitted, attached to the least populous"
                else:
                    continue
                for b in area:
                    out[b], how[b] = n, label
            still = [b for b in block_vtd if b not in out]
            if still:
                by_vtd = Counter(block_vtd[b] for b in still)
                problems.append(f"{len(still)} blocks in no district, in {len(by_vtd)} VTDs: "
                                f"{by_vtd.most_common(8)}")  # fmt: skip
        return out, how, sorted(set(problems))

    ref = [district_of(points, p) for p in args.reference] if args.reference else [None, None]
    sen, sen_how, sen_problems = assign(senate, ref[0])
    hou, hou_how, hou_problems = assign(house, ref[1])
    for label, problems in (("Senate", sen_problems), ("House", hou_problems)):
        for p in problems:
            print(f"  {label}: {p}")
    with args.out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["block", "senate", "senate_from", "house", "house_from"])
        for b in sorted(block_vtd):
            w.writerow([b, sen.get(b, ""), sen_how.get(b, ""), hou.get(b, ""), hou_how.get(b, "")])
    for label, how in (("Senate", sen_how), ("House", hou_how)):
        print(f"{label}: {len(how)} of {len(block_vtd)} blocks; {dict(Counter(how.values()))}")
    if population:
        # "…the ideal population of a senatorial district, based on the official 2010 federal
        # census, is 23,262 people" (2021's text keeps the struck figure too: the last wins)
        ideals = re.findall(r"ideal population of a senatorial district.{0,80}?is ([\d,]+)", act)
        ideal = ideals[-1] if ideals else None
        ideal = int(ideal.replace(",", "")) if ideal else None
        people = Counter()
        for b, n in sen.items():
            people[n] += population.get(b, 0)
        total = sum(population.values())
        print(f"Senate: {sum(people.values())} of {total} people placed; ideal {ideal}")
        if ideal:
            dev = {n: (p - ideal) / ideal for n, p in people.items()}
            lo, hi = min(dev, key=dev.get), max(dev, key=dev.get)
            print(f"  smallest {lo} {people[lo]} ({dev[lo]:+.2%}), largest {hi} {people[hi]} "
                  f"({dev[hi]:+.2%})")  # fmt: skip
        with args.out.with_name(args.out.stem + "-population.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["district", "people"])
            for n in sorted(people, key=lambda x: (int(re.match(r"\d+", x)[0]), x)):
                w.writerow([n, people[n]])

    if args.check:
        for label, path, mine in (("Senate", args.check[0], sen), ("House", args.check[1], hou)):
            census = district_of(points, path)
            differ = [b for b in block_vtd if mine.get(b) != census.get(b)]
            print(f"{label}: {len(differ)} blocks differ from {path.name}")
            for b in differ[:12]:
                how = (sen_how if label == "Senate" else hou_how).get(b)
                print(f"    {b} ({block_vtd[b][0]}, VTD {block_vtd[b][1]}): act {mine.get(b)} ({how}),"  # noqa: E501
                      f" Census {census.get(b)}")  # fmt: skip


if __name__ == "__main__":
    main()
