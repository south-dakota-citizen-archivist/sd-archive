# sd-archive: everyday commands. Run `just` to list them.

manage := "uv run python app/manage.py"

# List the recipes
default:
    @just --list

# Install Python and front-end dependencies, and the git hooks
setup:
    uv sync
    npm ci --prefix app
    npm run build --prefix app
    uv run pre-commit install

# Lint, check and test
check:
    uv run pre-commit run --all-files

# Run the test suite (extra arguments go to pytest)
test *args:
    uv run pytest {{args}}

# Tests that need live servers or browsers, with the network allowed: `just test-network packages/sdfetch`
test-network *args:
    uv run pytest -o addopts="-ra" -m "network or browser" {{args}}

# Record (or re-record) the live responses that @pytest.mark.vcr tests replay: `just record packages/sdgis -k live_layer`
record *args:
    uv run pytest --record-mode=rewrite {{args}}

# Run the Django development server at http://localhost:8000
serve:
    {{manage}} runserver

# Celery worker and beat (Redis must be running: brew services start redis)
worker:
    cd app && uv run celery -A config worker -l info

beat:
    cd app && uv run celery -A config beat -l info

# What's running, stalled, failed or overdue, what will resume, disk and backups
status:
    {{manage}} status

# Any Django management command: `just manage migrate`, `just manage shell`
manage *args:
    {{manage}} {{args}}

# Crawl GIS sources now (all active, or by slug: `just crawl sd-gfp`)
crawl *slugs:
    {{manage}} crawl {{slugs}}

# Harvest watched GIS datasets now (all, or by id: `just harvest 641 --force`); kept awake, resumes if stopped
harvest *args:
    caffeinate -i {{manage}} harvest {{args}}

# Run record sets' sources now (`just scrape salaries phone-directory`); kept awake
scrape *args:
    caffeinate -i {{manage}} scrape {{args}}

# Move a record set's documents through fetch, read, categorize, extract (`just documents oaths --limit 50`)
documents *args:
    caffeinate -i {{manage}} documents {{args}}

# Write the vendor checkbook's Parquet files and replace them on its GitHub release
publish-vendor-checkbook:
    {{manage}} export_vendor_checkbook
    gh release upload vendor-checkbook-data app/media/exports/vendor-checkbook/*.parquet --clobber

# Rebuild Tailwind CSS while editing templates
css:
    npm run dev --prefix app

# Dump the databases to ~/sd-backups now (the nightly launchd job runs the same script)
dump:
    zsh ../ops/pg-nightly-dump.sh
    tail -n 4 ~/sd-backups/dump.log
