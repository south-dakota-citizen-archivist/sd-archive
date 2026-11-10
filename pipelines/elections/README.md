# elections

Every South Dakota election result on electionresults.sd.gov, at the precinct level, in one table. An **aggregate** dataset in the catalog (`catalog/datasets.toml`, `election-results`): the unit is precinct × contest × choice.

```sh
uv run python pipelines/elections/discover.py           # catalog every election (eid) → data/elections.csv
uv run python pipelines/elections/capture.py --all      # every race's precinct results, raw → .cache/elections/raw/
uv run python pipelines/elections/build.py              # tables + checks → .cache/elections/build/
```

All three resume where they stopped and never fetch the same thing twice. They make one request at a time with a pause between.

As of 2026-11-10: **346,605 precinct rows** (317,452 from the API, 29,153 from the exports) 29,578 county rows and 156 recount rows, from the 44 elections that have results. The other four are empty listings on the site.

## What the site has

- **48 elections, November 2008 to November 2026** (10 generals, 11 primaries, 27 specials), each chosen with `?eid=`. The catalog is [`data/elections.csv`](data/elections.csv).
- **Precinct results for every race**, back to 2008, from the site's own API (`GetMapDataArchive`). One request with `county=0` returns every precinct that voted on a race, statewide or in one county. Each county's absentee ballots appear as an "Absentee Precinct".
- **County totals** (`category=CTY`) for races with county breakdowns. build.py checks that precinct results add up to them.
- **Export spreadsheets** (the Exports page): a workbook per county with a sheet per race and a row per precinct, plus statewide and legislative workbooks by county. They're a second primary source. They fill what the API lacks (2012's precincts, whose names the exports leave blank; two statewide ballot questions, 2010's Amendment K and 2016's Amendment T; local races in specials) and check the API's precinct rows. The site errors on 58 of 2008's 68 exports and 22 of the 2010 primary's; the API has both in full.
- **The current election isn't in the archive yet.** Its `?eid=` page shows 0 ballots cast, and its results are at the bare `default.aspx`, from the current API (`GetMapData`). capture.py notices this (an empty archive page, and the bare page showing the same date) and captures the live results, refetching them on every run because they can still change.

## What `capture.py` does

1. Follows every results page from the election's home page: statewide, ballot questions, legislative, county and local lists, and their per-county and per-place pages. Older elections (2008) have no per-county pages, so it follows links instead of assuming a layout.
2. Collects every race button, `BuildPrecincts(type, raceID, osn, county, party)` or `BuildCounty(…)`. Types seen so far: `SWR` statewide, `LEG` legislative, `RET` judicial retention, `BQ` statewide ballot questions, `CTY` county races and county ballot questions, `SDD` special districts (e.g. water development district directors), `MUN` municipal races (2008). In primaries, each party's race is separate (`party`).
3. Fetches each race's precinct results, and county totals where they exist.
4. Saves every workbook on the Exports page. They're ASP.NET postbacks: the page's form is read and each link posted back. It saves everything raw in `.cache/elections/raw/<eid>/` with `manifest.jsonl` (URL, bytes, SHA-256, time), `races.json`, and `summary.json` (pages, races, rows, and the home page's ballots cast and registered voters).

## The table (`build.py`)

`precinct_results.parquet` has one row per election × race × precinct × candidate or choice: election, date and status; race type, ID, office sequence and name; party ballot (primaries); county code, name and **GEOID** (via `sdplaces`): `county_geoid` as of the election date, `county_geoid_current` for today's county; precinct ID and name; candidate or choice, party, ballot order, votes, winner; and **`source_url`** (the API request) and `page_url` (a results page listing the race).

Rows from the exports have `source = "export"`, `export_file` and `export_sheet`, and take race and candidate details (IDs, party) from the API's county rows where it has them. 2012's export rows have no precinct names; `precinct_seq` is their order on the sheet.

Reading the exports:

- **Sheet names** are the race ID and the start of its name (`4265Presidential Electors`). In primaries, the ballot's party comes first (`REP14452Governor`, `NON` for the nonpartisan ballot), and rows are matched to the API by race, party ballot and county. The 2020 primary's sheets have no race ID (`REPState Rep District 06`), so they aren't read; the API has that election in full, so this loses only the cross-check.
- **Repeated sheets.** Some workbooks repeat a sheet with `_1` on its name (2008). A repeat with the same rows is dropped; one with different rows would be reported (`export repeated sheets`).
- **Race type for races the API doesn't list** (2008's and 2010's legislative and county races): from the legislative and statewide workbooks' sheet names, then `LEG` for a "State Senator/Representative" title (2008 has no legislative workbook), then `CTY` for a race in only one county's workbook.
- **All-zero API precincts.** Where the API's precinct rows for a race in a county are all 0 but its county total isn't, and the export's totals equal that county total, the export's precincts replace the API rows. This happens in the 2012 primary (three legislative races in Minnehaha and Pennington), and `checks.json` lists each one (`API precinct rows all zero`).

Checks (`checks.json`): precinct sums against county totals, API precinct sums against export totals, races with no precinct rows, duplicates (counting `precinct_seq`, for 2012's unnamed precincts), and county codes and names. Everything left in it as of 2026-11-10 is either an empty listing or a source error listed under Caveats.

## Caveats

- **Most elections are labeled "Unofficial Results"** on the site, and its archive appears to keep the election-night counts. The 2024 general's presidential totals are within 0.05% of the certified canvass (Trump 271,938 against 272,081 certified). Use the SOS official canvass for certified numbers; elections the site labels official are marked `election_status = official`.
- **Some elections in the catalog are empty shells.** Four (eids 91, 115, 200, 662, including the 2016-12-14 special and a 2024-05-28 primary) show 0 ballots cast and no contests, and their exports fail. build.py reports these as `no races`, with the ballots-cast count, so they aren't mistaken for a failed capture.
- **Some of the site's precinct numbers are wrong at the source.** In the 2010 general, Kristi Noem's precinct votes in Aurora County add up to 71, against a county total of 593, while the other candidates' precincts add up. The exports carry the same numbers. build.py reports every such mismatch (`precinct sums ≠ county totals`) and leaves the published numbers as they are.
- **The API and the exports disagree by a vote or two in a few precincts** (e.g. 2018 general, Lincoln County, "Sioux Falls 1-15": 316 against 317 for R. Blake Curd). It happens in about 1 in 300 of the county totals checked, in 2010–2024. The table keeps the API's numbers; `API precinct sums ≠ export totals` lists each one.
- **The 2026-07-28 runoff (eid 801) is broken at the source.** Its county totals are exactly three times its precinct sums, the site says "Precincts 5850 of 1950 Reporting", registered voters reads 10,125,314, and Jackson County's Precinct-3 appears twice in the API (the exports have it once). The precinct rows look right otherwise; the county rows and the home page's counts don't.
- **The 2012 primary lists one combined Davison County municipal precinct twice**, once with zero votes. Sums aren't affected.
- **Recounts** are in their own table, `recount_results.parquet`, with the same columns (`source = "export_recount"`), so the election-night rows stay as published. They come from export sheets named "<race>  RECOUNT" (often cut off: "  RE", "  RECO") next to the race's original sheet; join on `eid`, `race_id`, `party_ballot` and `county`. As of 2026-11-10 there are 10 races × counties, all in the 2010 general, and every recount total equals the election-night total. Either those recounts changed nothing or the site updated the original race to the recount's numbers; the site doesn't say which. Its "Possible Recounts" page lists close races, not recount results.
- **Precinct names and boundaries change between elections**, so a precinct ID isn't a stable place over time.
- The election system's county codes aren't FIPS and aren't alphabetical. `sdplaces` maps them (`from_id("sos_election_county", …)`).
- **The site shows today's county names on old elections.** Code 65 reads "Oglala Lakota" even in 2008, when the county was Shannon County. `county` keeps what the site says; `county_geoid` is the county in force on the election date (46113 before May 2015, 46102 after), and `county_geoid_current` is always 46102. Join on `county_geoid_current` for a series across years.
