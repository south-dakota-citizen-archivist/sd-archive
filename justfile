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

# Run the Django development server at http://localhost:8000
serve:
    {{manage}} runserver

# Celery worker and beat (Redis must be running: brew services start redis)
worker:
    cd app && uv run celery -A config worker -l info

beat:
    cd app && uv run celery -A config beat -l info

# Any Django management command: `just manage migrate`, `just manage shell`
manage *args:
    {{manage}} {{args}}

# Crawl GIS sources now (all active, or by slug: `just crawl sd-gfp`)
crawl *slugs:
    {{manage}} crawl {{slugs}}

# Harvest watched GIS datasets now (all, or by id: `just harvest 641 --force`)
harvest *args:
    {{manage}} harvest {{args}}

# Rebuild Tailwind CSS while editing templates
css:
    npm run dev --prefix app

# Dump the databases to ~/sd-backups now (the nightly launchd job runs the same script)
dump:
    zsh ../ops/pg-nightly-dump.sh
    tail -n 4 ~/sd-backups/dump.log
