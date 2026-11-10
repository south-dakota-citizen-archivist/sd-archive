# SD GIS Hub: plan

A Postgres-backed Django app that inventories every public GIS dataset available to South Dakotans, archives the ones worth keeping, and lets anyone search, map and download them.

It does three jobs that are currently separate CLI commands and notebooks:

| Job | Today | In the app |
| --- | --- | --- |
| **Discover:** what exists, who publishes it, what changed | `gis-scrape discover` → CSV | Scheduled crawls → a searchable census in Postgres, with first-seen, last-seen and change history |
| **Scrape:** archive the data itself | `gis-scrape run` → GeoParquet in git, daily Action | Scheduled harvests → features in PostGIS with full change history, plus GeoParquet exports |
| **Explore:** understand the data | Jupyter notebook | Dataset pages with a map, an attribute table, a change log, downloads and an API |

The structure follows [public-records-liberator](../public-records-liberator) (PRL) closely, so both projects work the same way.

## Status (2026-11-01)

**Built: phases 0–2, plus part of phase 3.**
- The Django app runs on Postgres.app.
- **The census:**
  - 8 ArcGIS sources: 2,523 datasets, covering 15 of 66 counties.
  - 18 Beacon portals, listed only.
  - Topics, duplicate detection, place and coverage pages.
  - Code labels and edit dates for each layer.
  - Adding a source by pasting a URL, with place suggestions.
- **Harvesting:**
  - Full history; the app has maps, tables, change logs, downloads and an API.
  - A harvest is skipped when a hosted layer is unchanged at the source.
- **Name search** works, including the `search_names` view for cross-project search.
- **Schedules:** Celery runs crawls and harvests, but only while a worker and beat are running.
- **Seed file:** `seeds/sources.toml` is rewritten after every change, so it always matches the database.

---

## TODO, ranked by importance (2026-11-01)

The hub has two jobs:
- give a defensible answer to "what GIS data does each South Dakota government publish?"
- keep a history that can't be rebuilt later

So work that protects or grows the history comes first, then coverage, then the depth of the archive, then more platforms, then polish.

### 1. Keep it running and keep it safe
1. **Run the Celery worker and beat all the time** (launchd or `brew services`, along with Redis). Without them nothing is crawled or harvested on schedule. The archive has 5 harvests in total, and every day without one is history lost for good.
2. **Back up nightly:** `pg_dump` of `sd_gis_hub` plus `media/exports/`, kept somewhere other than this laptop. Harvest history can't be downloaded again.
3. **Commit the work.** Everything since the initial commit is uncommitted. Before that:
   - `beacon-extractor/` fails ruff, which blocks pre-commit. It isn't part of the hub, so move it out of this repo.
   - `curl-cffi` and `nodriver` in `pyproject.toml` are unused by the hub; remove them.
4. **Make failures visible:**
   - a health panel on the home page: failed jobs, overdue harvests, sources not crawled in two weeks
   - a macOS notification when a harvest fails

### 2. Coverage: answer the census question
5. **Add District III** (`gis.districtiii.org/server`): 217 services, with places suggested for 35 counties.
6. **ArcGIS Online finder → `SourceCandidate`.** Search public services within South Dakota's bounding box, then:
   - keep items whose extent is mostly inside the state
   - drop basemap tiles and national publishers
   - group by server or organization

   A test search returned 10,000 items, mostly federal, so the filters matter.
7. **Review queue page.** Accept a candidate and the Add a source page opens with it filled in. Reject one and it's remembered.
8. **Check every regional planning district and council of governments for an ArcGIS server.** First District and District III are in or ready to add. Northeast Council of Governments, Black Hills Council of Local Governments, Central South Dakota Enhancement District and South Eastern Council of Governments have not been checked.
9. **Gaps page and records-request log.** For each county or city with nothing found, show what is known (a Beacon portal only, a PDF map, nothing at all). Record requests and their outcomes on the publisher.
10. **Fix attributions.** Meade County's parcels are credited to Pennington County. Crawls only fill in empty places, so an edit in the admin sticks.
11. **Re-crawl every source** so all layers get code labels and edit dates. Otherwise the weekly crawl fills them in.
12. **About and methodology page:** what "found" and "not found" mean, schedules, and caveats about GIS data versus official records.

### 3. Depth: make the archive worth having
13. **Watch more datasets.** Phase 2 ended with only parcels watched, though the plan also named Sioux Falls property tax by year and Pennington assessed values. The census has 42 assessed-value datasets, plus zoning and permits. Pick 10 to 20.
14. **Valuation history from public layers:**
    - Minnehaha's yearly Neighborhood Parcels layers for 2020–2022 have land and building values and owners, so load them into one table by year.
    - Do the same wherever other counties publish yearly layers.
15. **Scratch views and reports** (pinned on 2026-11-01). A `scratch` schema of flat views over each harvested dataset, for QGIS and SQL. Then a `reports` app, starting with:
    - largest landowners
    - out-of-state owners by town
    - owner changes between harvests
    - mining claims
    - recorded vs. mapped acreage
16. **A common parcel view across counties:** parcel ID, owner, mailing address, situs address and value, mapped from each county's fields. That enables statewide owner reports and better name search.
17. **Changes feed with RSS.** It's in the Phase 2 plan but not built. Add "edited at the source" for layers that aren't watched.
18. **Fetch robustness:** split a batch when the request fails, not only when it's truncated (from ezesri).

### 4. More platforms
19. **Beacon file import.** You export a layer in your own browser (`ExtractData.aspx`, where an app offers it) and upload it to a dataset. It then goes through the same validation, history and export steps as an ArcGIS harvest.
20. **The ArcGIS services behind Beacon counties,** where they're public.
21. **ArcGIS Hub, WFS/GeoServer and CKAN adapters,** only when the finders turn up South Dakota sources on them.
22. **Related tables and attachments.** Record which layers have them, then optionally harvest child tables, such as inspections under permits.

### 5. Polish
23. **A combined name search across projects,** using `search_names` over `dblink`.
24. **Performance on large layers** (table sorting, tiles, diffs) once something over 100,000 features is watched.
25. **Seed-file guard:** notice hand edits that haven't been loaded before the app overwrites them.

### Later
- LLM-written dataset descriptions and topic suggestions through PRL's model registry, via the review queue.
- Linking parcel owners to PRL entities: lobbyists, campaign donors, appointees.

---

## Stack

Same as PRL, plus PostGIS:

- **Django 5.2 LTS + GeoDjango** on **Postgres 16 + PostGIS**.
- **Celery + Redis** for crawls and harvests. **Celery Beat** runs a "what's due" task every minute, the same pattern as PRL's `enqueue_scheduled_discoveries`.
- **django-unfold** admin, **Django Ninja** API, **Tailwind 4** with server-rendered templates, plus **HTMX** for filters, pagination and job-status polling without a JavaScript build.
- **MapLibre GL JS** maps, with vector tiles generated in Postgres (`ST_AsMVT`) and served by a Django view. That needs no separate tile server until traffic calls for one.
- **pyogrio / GeoPandas / pyarrow**, kept from this repo for harvesting and GeoParquet export.
- Docker Compose for Postgres (`postgis/postgis:16`) and Redis only. Django and Celery run locally with `uv run`, like PRL.
- pytest-django, model-bakery, ruff and pre-commit, plus the README sources table generated from the database (PRL's `build_readme`).

GeoDjango needs the GDAL and GEOS system libraries (`brew install gdal geos` on macOS). pyogrio's bundled GDAL isn't enough for it, so the dev setup will cover this.

---

## Data model

Mirrors PRL's `RecordsCustodian → RecordSet → Record → Document`:

```
Jurisdiction ─┐
              ├─< Publisher ─< Source ─< Service ─< Dataset ─< DatasetObservation
              │                  │                    │
              │                  └─< Crawl            ├─< Harvest
              │                                       ├─< Feature (current + history)
              └──────────────────────────────────────>┘  Topic (M2M)
SourceCandidate (review queue)    Job (every task run)
```

| Model | What it is | Key fields |
| --- | --- | --- |
| `Jurisdiction` | A government area: state, 66 counties, municipalities, tribal nations, school districts and so on | FIPS/GEOID, type, name, boundary geometry (from Census TIGER), parent |
| `Publisher` | The agency or office that publishes data, such as "Pennington County Equalization" or "SDDOT" (like PRL's `RecordsCustodian`) | name, jurisdiction, website, contact |
| `Source` | One place datasets are published: an ArcGIS Server services directory, an ArcGIS Online org, a Hub site, a Beacon site | url, platform, publisher, crawl schedule, `folder_jurisdictions` (maps folders to jurisdictions for shared servers like 1stdistrict, where `Lawrence/` is county 46081), status, last crawl |
| `Service` | An ArcGIS service (MapServer or FeatureServer) | source, name, type, status, error, first and last seen |
| `Dataset` | **The census unit:** one layer or table | service, url, name, kind, geometry type, `fields` (JSON: name, type, alias), count, max record count, paging support, object ID field, publisher, jurisdictions (M2M, so a county's layer can be credited to that county even on a regional server), topics (M2M), `status` (discovered / watched / ignored), first seen, last seen, removed at |
| `DatasetObservation` | One crawl's snapshot of a dataset's metadata | crawl, count, hash of the field list, so the history shows count trends and schema changes ("field `SALE_PR` added June 3") |
| `Crawl` | One crawl of a source | counts of new, changed and removed datasets; errors; duration |
| `Harvest` | One download of a watched dataset's features (replaces `manifest.json` and the per-layer results) | expected and actual count, validation results, rows added/changed/removed, content hash, export file |
| `Feature` | One record from a harvested dataset | dataset, source object ID, geometry (generic, SRID 4326), `properties` (JSONB), content hash, `valid_from`, `valid_to` |
| `Topic` | A subject tag: parcels, assessed values, sales, permits, zoning, crashes, addresses, elections, hydrology and so on | name, rules (regexes on layer and field names); manual assignments always beat rules |
| `SourceCandidate` | A possible new source found by search, waiting for review (like PRL's review queue) | url, how it was found, evidence, status (pending / accepted / rejected) |
| `Job` | A record of every task run (PRL's `ScrapeJob`) | task, target, status, timing, log or error |

### How features are stored

Every harvested dataset goes in one `Feature` table, with attributes in JSONB, rather than a generated table per dataset. Datasets have very different schemas, and one table keeps the ORM, the tile view and the API generic.

History is kept by giving each version of a row a time range (`valid_from` / `valid_to`). A harvest:

1. Downloads the layer with the strategies already in this repo: object ID ranges, splitting truncated batches, offset paging as a fallback.
2. Validates the download: feature count matches the server, no duplicate IDs, and the drop guard against sudden shrinkage.
3. Compares each row's content hash with the current version:
   - unchanged rows are left alone
   - changed rows get the old version closed and a new one inserted
   - rows that disappeared are closed
   - new rows are inserted

   All of this happens in one transaction.
4. Writes a GeoParquet export to local `media/` storage for bulk downloads and reproducibility.

This gives "what changed between any two dates" and "this parcel's owner history" from SQL, which replaces the notebook's git-snapshot comparison. Parcels change slowly, so full history stays cheap. Indexes: GiST on geometry, (dataset, object ID, `valid_to`) for current rows, and GIN on `properties`.

Only datasets marked **watched** are harvested. Every other dataset stays a metadata-only census entry, with a live preview queried straight from the source.

---

## Code layout

```
config/                     settings/{base,dev,prod}.py, celery.py, urls.py
census/                     Jurisdiction, Publisher, Source, Service, Dataset, Topic, Crawl...
  platforms/                one adapter per platform, all with the same interface
    base.py                 list_datasets(source), count(dataset), fetch(dataset)
    arcgis_server.py        ← this repo's arcgis.py + discover.py
    arcgis_online.py        org content and the public search API
    beacon.py, hub.py, wfs.py, ckan.py   later
  finders/                  ways to find new sources → SourceCandidate
    arcgis_search.py        ArcGIS Online search, limited to South Dakota's extent
    datagov.py              data.gov catalog API
    webmaps.py              servers referenced in public web maps
  classify.py               topic rules
  tasks.py, admin.py, views/, api/, management/commands/
harvest/                    Harvest, Feature
  fetch.py, validate.py     ← this repo's scraper.py (fetch strategies, validation, drop guard)
  store.py                  history-keeping upsert
  export.py                 GeoParquet, GeoJSON, CSV, shapefile downloads
  tiles.py                  vector tile view
  tasks.py, views/, api/
places/                     loading Census TIGER boundaries for jurisdictions
templates/, static/         Tailwind, HTMX, MapLibre
```

The `platforms/` interface is how Beacon and other systems get added later: each adapter returns the same dataset records, and the census, user interface and API don't change. Beacon is mostly per-parcel lookup pages rather than a queryable API, so its adapter may only report that a site exists and what it offers, without harvesting it.

---

## Pages

Everything runs locally with no login. The Django admin is the place to edit records.

| Page | What it shows |
| --- | --- |
| **Home** | Search box; totals (sources, datasets, governments covered, features archived); a South Dakota map shaded by how much each county publishes; recent changes |
| **Catalog** `/datasets/` | Full-text search on names, fields and descriptions. Filters: jurisdiction, publisher, topic, platform, geometry type, watched or harvested, recently added or changed |
| **Jurisdiction** `/places/lawrence-county/` | "What does Lawrence County publish?" Datasets grouped by topic, the platforms it uses, gaps ("no public parcels found"), and data covering the county from regional, state and federal sources |
| **Coverage** `/coverage/` | The census table: governments × topics, with a map for any topic ("which counties publish assessed values?") |
| **Dataset** `/datasets/<id>/` | Tabs: **Overview** (metadata, publisher, source link, count history chart, field list with types) · **Map** (vector tiles once harvested, a live sample from the source before that) · **Table** (sort and filter by any attribute, server-side) · **Changes** (added, removed and modified features between any two harvests, schema changes) · **Download** (GeoParquet, GeoJSON, CSV, shapefile, API link) · **Provenance** (harvest log, validation results) |
| **Feature** `/datasets/<id>/features/<oid>/` | One record's current attributes, its map location and every past version: a parcel's owner and value history |
| **Changes** `/changes/` | Everything new, removed or significantly changed across the census, filterable, with RSS feeds (like PRL's record set feeds) |
| **Review** `/review/` | Source candidates; topic assignments the rules aren't sure about |
| **Jobs** `/jobs/` | Crawls and harvests: status, errors, durations |
| **About / methodology** | How the census is built, what "found" and "not found" mean, update schedules, caveats about GIS data versus official records |
| **API** `/api/v1/` | Jurisdictions, sources, datasets, changes; features as GeoJSON with bounding-box and attribute filters; generated docs |

Ad hoc analysis reads from Postgres through `harvest.frames.load_dataset(id, as_of=...)` instead of parquet files. The `analyze/` notebooks are gone; a scratchpad and a set of specific reports will replace them. Report ideas from the old Lawrence County notebook: largest landowners, out-of-state owners by town, owner changes between harvests, mining claims, and recorded vs. mapped acreage.

---

## Pipelines (Celery tasks)

1. **`find_sources`** (weekly): runs each finder and writes new URLs to `SourceCandidate` for review.
2. **`crawl_source`** (weekly per source, or on demand): lists services and datasets and records an observation of each. It also:
   - updates first-seen and last-seen dates, and marks removed datasets
   - applies the topic rules
   - skips copies of layers published in another service, keeping the original

   Built-in protections, from the Minnehaha hang:
   - 30-second timeouts for metadata requests, with 1 retry
   - a time limit per source
   - one request at a time per host by default
   - timeouts recorded as service errors, not crashes
3. **`harvest_dataset`** (on each watched dataset's schedule): downloads, validates and stores the features as described above, writes the export file, and records a `Harvest`.
4. **`notify`**: change digests to RSS and, optionally, Slack, as PRL does.
5. **Maintenance:** refresh Census boundaries yearly, clean up old exports, and regenerate the README table.

---

## Phases

Each phase ends with something usable.

### Phase 0: Foundation
- Django project setup: split settings, PostGIS and Redis in Docker Compose, Celery and Beat, Unfold, Tailwind, pytest-django, CI, pre-commit.
- Move this repo's ArcGIS client, fetch strategies and discovery crawler into `census/platforms/arcgis_server.py` and `harvest/fetch.py`, and convert their 121 tests.

**Ends with:** the app boots, the tests pass, and the existing logic runs inside Django.

### Phase 1: The census
- Census models, migrations and admin.
- Load Census TIGER boundaries for the state, counties, places and tribal areas.
- `crawl_source` with the protections above.
- Add the 8 sources we know about and crawl them: 1stdistrict, sdgis DOT, sdgis hosted, Game, Fish & Parks, Rapid City/Pennington, Minnehaha, Sioux Falls, Sioux Falls ArcGIS Online.
- Catalog search, dataset Overview tab with a live preview, jurisdiction pages, and the jobs page.
- First pass of topic rules.

**Ends with:** more than 2,000 layers across 8 servers, searchable and browsable by county.

### Phase 2: Harvesting and exploring
- `Feature` and `Harvest` models, the history-keeping upsert, and the export files.
- Import the existing Lawrence County parcels file as the first harvest.
- The "watch" action and per-dataset schedules.
- Dataset Map, Table, Changes and Download tabs; feature history pages; the vector tile view; the changes feed with RSS.

**Ends with:** Lawrence parcels, Sioux Falls property tax by year, and Pennington and Meade assessed values, all browsable with full history.

### Phase 3: Coverage
- Finders: ArcGIS Online search, data.gov and web maps.
- Review queue.
- Coverage table and map.
- Duplicate detection.
- Jurisdiction gaps.
- About and methodology page, and the API.

**Ends with:** a defensible answer to "what GIS data does each South Dakota government publish?"

### Phase 5: Polish
- Performance work on large datasets.
- A combined search page across projects, using the `search_names` view.

### Later ideas
- **LLM help through PRL's model registry:** plain-English dataset descriptions and topic suggestions with confidence scores, routed through the review queue.
- **Linking parcel owners to PRL entities:** lobbyists, campaign donors, appointees.

---

## Carried over from this repo

- The ArcGIS fetch strategies and their tests, validation, the drop guard, content hashing and the discovery crawler, all moved rather than rewritten.
- The current Lawrence parcels file becomes the first harvest.
- Discovery CSVs are replaced by `Dataset` rows. The first crawl recreates them.
- The `gis-scrape` command line, `layers.toml`, `data/` in git and the daily scrape Action are retired once phase 2 ships. Until then they keep running.

---

## Decisions (2026-11-01)

- **Runs on this laptop only.** Nothing gets deployed. Postgres.app (PostgreSQL 17 + PostGIS 3.5) and Homebrew Redis are already running. There's no login system: the Django admin superuser is enough, and every page is open locally.
- **Built in this repo.** The old CLI, `layers.toml`, `data/` and the GitHub scrape Action are retired once harvesting works in the app.
- **Searching names across databases is a goal.** Owner and taxpayer names are meant to be searchable here, and later across other projects (see below).
- Phase 5 becomes "Polish" rather than "Public launch".

## Name search, and searching across projects

- Each dataset has `name_fields`: the attributes that hold people or organization names, such as `CURRENT_OW` or `TAXPAYER_NAME`. They're suggested from field names and labels when a dataset is crawled, and editable in the admin.
- After each harvest, `search.NameEntry` is rebuilt for that dataset by a single SQL statement over `harvest.Feature`. It keeps one row per (dataset, feature, field, normalized name), with the dates each name was valid, so former owners stay findable. A trigram index (`pg_trgm`) on the normalized name makes fuzzy searches fast: "smith j" finds "SMITH, JOHN & JANE".
- **The cross-project contract:** a SQL view, `public.search_names`, with these columns:

  | Column | Meaning |
  | --- | --- |
  | `project` | Which project the row comes from |
  | `record_type` | What kind of record matched |
  | `record_label` | A readable description of the record |
  | `name` | The name as published |
  | `normalized_name` | The name after normalization |
  | `url` | Where to view the record |
  | `context` | Extra details, as JSONB |
  | `valid_from`, `valid_to` | When the name was valid |

  Normalization is the same in every project: uppercase, accents removed, punctuation turned into spaces, spaces collapsed. Any project on this Postgres server, PRL included, can publish the same view. A combined search page can then query every project's view with `dblink` (built into Postgres) without copying data. The same search is available at `/api/v1/search/names?q=`.
