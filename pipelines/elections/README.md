# elections

Every South Dakota election result on electionresults.sd.gov, at the precinct level, in one table. An **aggregate** dataset in the catalog (`catalog/datasets.toml`, `election-results`): the unit is precinct × contest × choice.

```sh
uv run python pipelines/elections/discover.py           # catalog every election (eid) → data/elections.csv
uv run python pipelines/elections/capture.py --all      # every race's precinct results, raw → .cache/elections/raw/
uv run python pipelines/elections/build.py              # one table + checks → .cache/elections/build/
```

All three resume where they stopped and never fetch the same thing twice. They make one request at a time with a pause between.

## What the site has

- **48 elections, November 2008 to November 2026** (10 generals, 11 primaries, 27 specials), each chosen with `?eid=`. The catalog is [`data/elections.csv`](data/elections.csv).
- **Precinct results for every race**, back to 2008, from the site's own API (`GetMapDataArchive`). One request with `county=0` returns every precinct that voted on a race, statewide or in one county. Each county's absentee ballots appear as an "Absentee Precinct".
- **County totals** (`category=CTY`) for races with county breakdowns. build.py checks that precinct results add up to them.

## What `capture.py` does

1. Follows every results page from the election's home page: statewide, ballot questions, legislative, county and local lists, and their per-county and per-place pages. Older elections (2008) have no per-county pages, so it follows links instead of assuming a layout.
2. Collects every race button, `BuildPrecincts(type, raceID, osn, county, party)` or `BuildCounty(…)`. Types seen so far: `SWR` statewide, `LEG` legislative, `RET` judicial retention, `BQ` statewide ballot questions, `CTY` county races and county ballot questions, `SDD` special districts (e.g. water development district directors), `MUN` municipal races (2008). In primaries, each party's race is separate (`party`).
3. Fetches each race's precinct results, and county totals where they exist. It saves everything raw in `.cache/elections/raw/<eid>/` with `manifest.jsonl` (URL, bytes, SHA-256, time), `races.json`, and `summary.json` (pages, races, rows, and the home page's ballots cast and registered voters).

## The table (`build.py`)

`precinct_results.parquet` has one row per election × race × precinct × candidate or choice: election, date and status; race type, ID, office sequence and name; party ballot (primaries); county code, name and **GEOID** (via `sdplaces`): `county_geoid` as of the election date, `county_geoid_current` for today's county; precinct ID and name; candidate or choice, party, ballot order, votes, winner; and **`source_url`** (the API request) and `page_url` (a results page listing the race).

Checks (`checks.json`): precinct sums against county totals, races with no precinct rows, duplicates, and county codes and names.

## Caveats

- **Most elections are labeled "Unofficial Results"** on the site, and its archive appears to keep the election-night counts. The 2024 general's presidential totals are within 0.05% of the certified canvass (Trump 271,938 against 272,081 certified). Use the SOS official canvass for certified numbers; elections the site labels official are marked `election_status = official`.
- **Some elections in the catalog are empty shells.** The 2016-12-14 special (eid 200) shows 0 ballots cast and no contests; build.py reports these as `no races`, with the ballots-cast count, so they aren't mistaken for a failed capture.
- **Precinct names and boundaries change between elections**, so a precinct ID isn't a stable place over time.
- The election system's county codes aren't FIPS and aren't alphabetical. `sdplaces` maps them (`from_id("sos_election_county", …)`).
- **The site shows today's county names on old elections.** Code 65 reads "Oglala Lakota" even in 2008, when the county was Shannon County. `county` keeps what the site says; `county_geoid` is the county in force on the election date (46113 before May 2015, 46102 after), and `county_geoid_current` is always 46102. Join on `county_geoid_current` for a series across years.
