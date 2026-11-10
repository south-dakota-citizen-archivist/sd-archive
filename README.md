# sd-archive

Tools for obtaining, processing and publishing South Dakota public records: shared Python packages, data pipelines, a catalog of datasets, and a local Django app for South Dakota's public GIS data.

## Layout

```
sd-archive/
├── packages/
│   ├── sdfetch/     fetching with a raw store, fallbacks, checkpoints, WARC/HAR, Wayback
│   ├── sddocs/      reading PDFs: form fields, text layers, OCR engines, forms by position, tables
│   ├── sdllm/       language models for extraction and classification, quotes checked against the source
│   ├── sdnames/     names: normalizing, kinds, owner strings split into parties, parsing, blocking keys
│   ├── sdplaces/    places by Census GEOID, agency ID crosswalks, addresses, geocoding
│   └── sdgis/       ArcGIS REST and Schneider Beacon clients
├── pipelines/
│   └── elections/   precinct-level results from electionresults.sd.gov, 2008 on
├── catalog/         datasets.toml: every dataset, its kind (aggregate or records) and source
└── app/             the GIS hub: a census of published GIS data, archived with history
```

Every package works without Django. Each has its own README.

## Principles

- **Primary sources.** Every published row links to where it came from, and every fetch is recorded in a manifest (URL, time, SHA-256).
- **Two kinds of data.** In an *aggregate* dataset the place is the unit (voter registration by county, results by precinct). In a *records* dataset, things within a place are the unit (permits, parcels, filings). The kind decides how a dataset is stored and checked. `catalog/datasets.toml` records each dataset's kind.
- **Interruptible.** Every command can be stopped and run again. Downloads are skipped once stored, files are written whole or not at all, and long runs keep checkpoints.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Node 22, Postgres 17 with PostGIS on port 5432, Redis, and GDAL and GEOS (`brew install gdal geos`).

```sh
just setup                 # uv sync, npm ci + Tailwind build, pre-commit hooks
just manage migrate
just serve                 # http://localhost:8000
just test                  # the test suite
just check                 # every pre-commit hook on every file
```

Python 3.12; every command goes through `uv run` or `just`. Database settings come from the usual `PG*` environment variables (default: `sd_gis_hub` on localhost).

Tests never reach the internet: requests off this machine fail. Tests of real sites replay recorded responses (`@pytest.mark.vcr`, cassettes in each `tests/cassettes/`); `just record <tests>` records them again when a site changes. The few that can't be recorded are marked `network` (fetches through curl_cffi, fresh timestamps), and tests that start real browsers are marked `browser`. Both are skipped unless asked for (`uv run pytest -m network`).
