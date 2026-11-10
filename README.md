# sd-archive

The South Dakota citizen archive: one platform for obtaining, processing and (optionally) publishing South Dakota public records. It consolidates the separate projects in `~/civic-tech`. The layout, principles and staged migration are in [`../CONSOLIDATION-PLAN.md`](../CONSOLIDATION-PLAN.md).

**Status (2026-11-09):** Stage 2 has begun. `app/` is the SD GIS Hub, copied from `gis-scraping`. Shared libraries are being pulled out into `packages/`, and public records (from `public-records-liberator`) arrive in Stage 3.

## Layout

```
sd-archive/
├── pyproject.toml   uv workspace root: members app + packages/*, shared ruff and pytest settings, dev tools
├── justfile         everyday commands (`just` lists them)
├── packages/        shared libraries, usable without Django (sdgis, sdplaces, sdfetch, sddocs, sdllm, sdnames)
└── app/             the Django project: config/, core/, census/, harvest/, search/, templates/, tests/
                     see app/README.md for the GIS hub's sources, setup and how it works
```

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Node 22, Postgres.app 17 (with PostGIS) on port 5432, Redis (`brew services start redis`), GDAL and GEOS (`brew install gdal geos`).

```sh
just setup                 # uv sync, npm ci + Tailwind build, pre-commit hooks
just manage migrate
just serve                 # http://localhost:8000
just test                  # the test suite
just check                 # every pre-commit hook on every file
```

The database settings come from the usual `PG*` environment variables. The default database is `sd_gis_hub` on localhost; libpq's defaults and `~/.pgpass` are enough on this machine.

## Conventions

- Python 3.12 (`.python-version`). Every command goes through `uv run` or `just`.
- Claude config (`CLAUDE.md`, `.claude/skills/`) lives once, in `~/civic-tech`, not in this repo.
- Collection may use any tool, stealth browsers included. Public output links to primary sources (see `../CLAUDE.md`).
- Every command can be interrupted and rerun: see "Working on a laptop" in the plan.
