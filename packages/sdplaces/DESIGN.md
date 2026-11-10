# sdplaces: design (agreed 2026-11-09)

One source of truth for South Dakota places and the ID systems agencies use for them, plus address normalization and geocoding. Plain Python, no Django. It replaces four separate county→FIPS lookups (voter registration, motor vehicles, polling places, the GIS hub) and the Spearfish-only address code.

**Decisions (2026-11-09):** (1) historical counties included with dates and successors; (2) the legislator crosswalk moves to `sdnames`/`entities`; (3) strict name matching, with an error and suggestions for unknown names; (4) addresses and geocoding stay in sdplaces.

---

## 1. What it contains

### Places (`data/places.csv`)

One row per place, past and present:

| column | example | notes |
|---|---|---|
| `kind` | `county` | `state`, `county`, `place` (incorporated city or town), `tribal`, `school_district`, `sldu` / `sldl` (state senate and house districts) |
| `geoid` | `46081` | the Census GEOID, the key everything else joins on (and the `/place/<geoid>` URL) |
| `name` | `Lawrence County` | official name |
| `short_name` | `Lawrence` | how records usually write it |
| `slug` | `lawrence-county` | |
| `parent` | `46` | a place's county, a county's state |
| `ocd_id` | `ocd-division/country:us/state:sd/county:lawrence` | Open Civic Data ID |
| `valid_from`, `valid_to` | `1875-01-01`, `2015-05-01` | for places that no longer exist or changed codes |
| `successor` | `46102` | where a retired code's records belong now |

**Historical counties are included:** Shannon (46113, until 2015, succeeded by Oglala Lakota 46102), Washabaugh (46131, until 1983, merged into Jackson 46071), and Armstrong, if any of your data goes back far enough. Records keep the code in force on their date, and `current=True` follows `successor`.

> **Decided:** historical counties included.

### Other names for places (`data/aliases.csv`)

`kind, geoid, alias, source, note`: the spellings records actually use. For example "Shannon", "Mc Cook", "McCook Co.", "BonHomme", "Fall River Co", "Sioux Falls city". These are added as pipelines encounter them, each with where it was seen.

### ID crosswalks (`data/crosswalks/<system>.csv`)

One file per outside ID system, all with the same columns: `system_id, geoid, name_in_source, evidence, note`. `evidence` records how the mapping is known (e.g. `2022 general results, CountyID↔CountyName`). Nothing is inferred from position or order.

| system | maps | source | status |
|---|---|---|---|
| `sos_election_county` | the election system's `CountyID` → county | electionresults.sd.gov data | 59 known from 2022 results. IDs 3, 6, 7, 36, 38, 54, 59, 66 still to confirm from another election's data |
| `doe_district` | DOE `district_no` (`06001`) → school district GEOID / NCES LEAID | the NCES Common Core of Data (its `ST_LEAID` is `SD-06001`) | to build |
| `census_vtd` | 2020 voting districts → county | TIGER | later. SD precincts change between elections, so true precinct crosswalks come from SOS data |

**The legislator ID crosswalk** (session profile ID → person) is about people, not places. I'd put it in `sdnames` / `entities` as gold data for matching, not here. Legislative *districts* do belong here (`sldu`/`sldl`).

> **Decided:** the legislator crosswalk moves to `sdnames`/`entities`.

### Boundaries (downloaded, not shipped)

Shapes are too big to ship in the package. `sdplaces.boundaries(kind, year=2024)` downloads the Census cartographic boundary file once into `$SDPLACES_CACHE` (default `~/.cache/sdplaces/GENZ<year>/`) and returns a GeoDataFrame in EPSG:4326. Its rows are sdplaces' own places (GEOID, name, short name, slug, parent, valid dates), so the file's other rows (CDPs in the place file, other states, tribal areas that only touch the border) are left out. For an older year, historical places come back too: `boundaries("county", 2014)` includes Shannon County. The GIS hub's `load_jurisdictions` uses it to fill PostGIS. This needs the optional `geo` extra (geopandas); everything else is standard library.

---

## 2. The interface

```python
import sdplaces

sdplaces.county("Lawrence")  # Place(kind='county', geoid='46081', name='Lawrence County', …)
sdplaces.county("LAWRENCE COUNTY")  # same: case, " County" and punctuation don't matter
sdplaces.county("Shannon")  # Place(geoid='46113', valid_to=2015-05-01, successor='46102')
sdplaces.county("Shannon", current=True)  # Oglala Lakota County, 46102
sdplaces.place("Spearfish")  # the city
sdplaces.get("46081")  # any place by GEOID
sdplaces.from_id("sos_election_county", 57)  # Spink County
sdplaces.from_id("doe_district", "06001")  # Aberdeen 06-1 school district
sdplaces.counties(
    as_of="2010-01-01"
)  # the 66 counties in force on that date (Shannon, not Oglala Lakota)
sdplaces.children("46081", kind="place")  # cities and towns in Lawrence County

# pandas helper for pipelines: adds county_geoid, and fails loudly on any name it can't place
df = sdplaces.add_geoid(df, name_column="county", kind="county", date_column="date")

# optional `geo` extra
sdplaces.boundaries("county")  # GeoDataFrame of current boundaries
sdplaces.locate(44.49, -103.86)  # [South Dakota, Lawrence County, Spearfish city, …]
```

**Names are matched exactly, never guessed.** Matching uses the normalized name plus the aliases file. An unknown name raises `UnknownPlace` with near-miss suggestions, and is never mapped silently to the nearest match. That keeps every mapping auditable, in line with the primary-source rule.

> **Decided:** strict matching.

---

## 3. Addresses and geocoding

These come from whats-up-in-spearfish's `address.py` and `geo.py`, made statewide:

```python
from sdplaces import address, geocode

address.parse("2545 & 2555 CLEAR SPRING RD")  # two Address objects
address.key("515 27th St N, Spearfish, SD")  # "515 N 27TH ST"
address.loose("1440 N MAIN ST")  # "1440 MAIN ST" (the fallback when directions disagree)

geocode.batch(addresses, cache=JsonCache(path), within=sdplaces.get("46081"))
```

What changes from the Spearfish versions:
- **Town names:** Spearfish's code strips a hard-coded list of Northern Hills towns from the end of an address. The shared version strips any SD place name, from `places.csv` plus aliases.
- **Bounding box:** the hard-coded Spearfish box becomes a `within=` argument (any Place, or a box). Results outside it are rejected as bad data.
- **Cache:** the cache becomes something the caller passes in (a JSON file path, as Spearfish does now, or later a database table), not a fixed path in Spearfish's data folder.
- **Kept as they are:** the Census batch geocoder first, the Nominatim fallback at one request a second, retrying misses after 30 days, and approximate "street-level" hits.
- **Planned (Stage 4):** a local lookup against county E911 address points harvested by the GIS hub, tried before the Census geocoder.

> **Decided:** inside sdplaces.

---

## 4. Where the data comes from, and keeping it current

`packages/sdplaces/scripts/build_data.py` regenerates `places.csv` from the Census Gazetteer and TIGER/cartographic files (names, GEOIDs, parents), and OCD IDs from the opencivicdata `ocd-division-ids` repository. Historical counties, aliases and crosswalk rows are kept in git and edited by hand, each with its source. Rebuilding is deliberate (yearly, or when the Census changes something), and the diff is reviewed before committing.

Tests check:
- 66 current counties, and no GEOID used for two places
- every crosswalk and alias row points at a real GEOID
- every historical place has a `valid_to`, and its successor exists
- the known quirks: Shannon/Oglala Lakota by date, Washabaugh → Jackson, McCook/McPherson spellings, "Fall River" versus "Fall River County"

---

## 5. Who switches to it

| user | today | after |
|---|---|---|
| GIS hub (`census/jurisdictions.py`) | downloads its own Census files | `sdplaces.boundaries()` + `places.csv` (same GEOIDs, so no data changes) |
| voter registration | `us-county-fips.csv` + two hand-added rows | `sdplaces.add_geoid(date_column="date")` |
| **motor vehicles** | downloads the voter repo's FIPS CSV, with the same hand-added **Oglala Lakota → 46113** line: all **176 rows from 2015 to 2025** carry Shannon's retired code (found 2026-11-09 by checking against sdplaces) | `sdplaces.county()`, and the published CSV corrected |
| **polling places** | the same download, with **Oglala Lakota mapped to 46113, which is wrong** (it's Shannon's retired code; Oglala Lakota is 46102) | `sdplaces.county()`, and the published CSV corrected |
| precinct results | none (raw `CountyID`) | `from_id("sos_election_county", …)` |
| whats-up-in-spearfish | `scrapers/address.py`, `geo.py` | `sdplaces.address`, `sdplaces.geocode` (Stage 2's first release) |
| education dashboard | `district_no` only | `from_id("doe_district", …)` → GEOID, for place pages |

---

## 6. Build order, once agreed

1. `places.csv` (state, counties including historical ones, places, tribal areas) with `build_data.py`, the lookup interface, strict matching and tests.
2. Aliases and the `sos_election_county` crosswalk, seeded from the data you already have.
3. `address` and `geocode` from Spearfish, with Spearfish's existing tests carried over.
4. School districts with the DOE crosswalk, legislative districts, OCD IDs.
5. The GIS hub's loader switched to `boundaries()` (2026-11-10): reloading changed none of its 389 rows (names, slugs, parents, and boundaries byte for byte). Left: fixes for the 46113 bug in polling places and motor vehicles, as PRs to those repos.
6. **Try libpostal as a fallback parser** (agreed 2026-11-10). Run it on the address strings usaddress rejects in Spearfish's data (and, later, statewide records), and add it as an optional `postal` extra only if it clearly recovers more. Its `expand_address()` variants may also help entity matching. usaddress stays the main parser. The biggest accuracy gain is planned separately: matching keys against county E911 address points (Stage 4).

**Progress:** step 4 done 2026-11-10: 148 current school districts plus 3 historical (Rutland 39-4 and Oldham-Ramona 39-5 merged into the new Oldham-Ramona-Rutland 39-6 for 2023-24; Grant-Deuel 25-3 dissolved after 2016-17), 35 Senate and 37 House districts, OCD IDs on 597 rows (missing only for the two historical counties, Brant Lake city, Corsica-Stickney 21-3 and 39-6, which OCD doesn't list), and the `doe_district` crosswalk (151 DOE numbers → NCES LEAIDs, from the CCD 2022 and 2016 directories). Every one of the education dashboard's 1,340 district-years resolves to a district in force that year. OCD's own Oglala Lakota row carries Shannon's retired code 46113, so OCD IDs are matched by name for counties.

**GEOIDs are unique only within a kind** (41 are shared: Senate/House districts and counties, e.g. 46003; school districts and cities, e.g. 4629340 is Grant-Deuel 25-3 and Hillsview town). Each crosswalk declares the kind it maps to. **URLs:** geographic places never share a GEOID (tested), so `/place/<geoid>` works as planned for the state, counties, cities, CDPs and tribal areas; districts get their own paths: `/district/school/<geoid>`, `/district/senate/<n>`, `/district/house/<n>`.

Step 3 done 2026-11-10: `sdplaces.address` and `sdplaces.geocode` (Geocoder, JsonCache, Box), from whats-up-in-spearfish. Spearfish's tests pass unchanged. On all 14,475 address strings in Spearfish's data, keys differ from the original in 89 cases: 87 addresses keyed that the original couldn't key (Spearfish Mountain Lane), one misspelled town ('Speaerfish') kept out of a key, and one garbage key gone. Rules found along the way: Lane is an SD town; CT/MT/LA/NE are both states and street words; descriptions follow a semicolon. Step 2 done 2026-11-09: the `sos_election_county` crosswalk (66 counties) and `from_id()`. Step 1 done 2026-11-09. `places.csv` has 566 rows (1 state, 68 counties including Shannon and Washabaugh, 310 places, 175 CDPs, 12 tribal areas), with lookups, strict matching and 29 tests. Checked against the published data: every county name in voter registration (71,791 rows back to 1976, by date, all matching the file's own FIPS), motor vehicles and the 2022 precinct results is placed.
