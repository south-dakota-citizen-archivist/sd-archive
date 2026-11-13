"""Build the public site: the hub's home page and each data page, as static files.

    uv run python publish/build.py [--out DIR]

Reads what the pipelines built (run them first: `just voter-registration`) and writes the
site to .cache/publish/site/ (or --out): HTML from the Jinja templates in templates/, the
shared static files, each page's data, and _health.json (when the site was built and how
current each page's data is). The site is served from the org's GitHub Pages root, so pages
link to each other with relative paths.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUT = REPO / ".cache" / "publish" / "site"
SITE_NAME = "South Dakota Citizen Archivist"
RELEASES = "https://github.com/south-dakota-citizen-archivist/sd-archive/releases"


def env():
    import jinja2

    return jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "templates"),
                              autoescape=jinja2.select_autoescape(["html"]),
                              trim_blocks=True, lstrip_blocks=True)  # fmt: skip


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def build(out: Path = OUT, voter_registration: Path | None = None) -> dict:
    """Write the site to out; returns _health.json's contents."""
    import maps

    vr_build = voter_registration or REPO / ".cache" / "voter-registration" / "build"
    data = json.loads((vr_build / "chart.json").read_text())
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    shutil.copy(HERE / "static" / "site.css", out / "static" / "site.css")
    built = datetime.now(UTC)
    common = {"site_name": SITE_NAME, "built": built.strftime("%B %-d, %Y"),
              "build_id": built.strftime("%Y%m%d%H%M"),
              "site_description": "Public records about South Dakota and its places, kept with "
                                  "their sources."}  # fmt: skip
    templates = env()
    common["nav"] = [{"path": "voter-registration/", "title": "Voter registration"},
                     {"path": "about/", "title": "About"}]  # fmt: skip

    latest = data["dates"][-1]
    vr = out / "voter-registration"
    vr.mkdir()
    (vr / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    shutil.copy(HERE / "static" / "voter-registration.js", vr / "app.js")
    (vr / "index.html").write_text(templates.get_template("voter_registration.html").render(
        **common, root="../", current="voter-registration/", parties=data["parties"],
        map_svg=maps.county_svg(),
        first_year=data["dates"][0][:4], latest_label=long_date(latest),
        snapshots=len(data["dates"]),
        downloads={"base": f"{RELEASES}/download/voter-registration-data",
                   "sources": f"{RELEASES}/tag/voter-registration-sources"}))  # fmt: skip

    pages = [{
        "path": "voter-registration/", "title": "Voter registration by party",
        "summary": "Registered voters by party, statewide and in each county, 1976 to now.",
        "source": 'South Dakota Secretary of State, <a href="https://sdsos.gov/elections-voting/'
                  'upcoming-elections/voter-registration-totals/voter-registration-by-county.aspx'
                  '">voter registration totals</a>',
        "covers": f"The state and its 66 counties, {len(data['dates'])} reports since "
                  f"{data['dates'][0][:4]}",
        "latest": long_date(latest),
    }]  # fmt: skip
    (out / "index.html").write_text(
        templates.get_template("home.html").render(**common, root="", current="", pages=pages)
    )
    (out / "about").mkdir()
    (out / "about" / "index.html").write_text(
        templates.get_template("about.html").render(**common, root="../", current="about/")
    )
    (out / ".nojekyll").write_text("")
    health = {"built": built.isoformat(timespec="seconds"),
              "pages": {"voter-registration": {"latest_report": latest,
                                               "reports": len(data["dates"]),
                                               "data_built": data["updated"]}}}  # fmt: skip
    (out / "_health.json").write_text(json.dumps(health, indent=1) + "\n")
    return health


def main() -> None:
    import sys

    sys.path.insert(0, str(HERE))
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    health = build(args.out)
    print(f"built {args.out}: {json.dumps(health)}")


if __name__ == "__main__":
    main()
