"""Build the public site: the hub's home page and each data page, as static files.

    uv run python publish/build.py [--out DIR]

Reads what the pipelines built (run them first: `just voter-registration motor-vehicles`)
and writes the site to .cache/publish/site/ (or --out): HTML from the Jinja templates in
templates/, the shared static files (site.css, and sda.js: what every data page's script
shares), each page's data, and _health.json (when the site was built and how current each
page's data is). A data page is an entry in PAGES: a function that writes the page (with
write_page) and returns its home-page card and its health. The site is served from the
org's GitHub Pages root, so pages link to each other with relative paths.
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
POPULATION = REPO / "pipelines" / "population" / "data" / "population.csv"
STATE = "0400000US46"


def env():
    import jinja2

    return jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "templates"),
                              autoescape=jinja2.select_autoescape(["html"]),
                              trim_blocks=True, lstrip_blocks=True)  # fmt: skip


def long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


MEASURE_LABELS = {  # the report's registration types, as the page names them
    "all": "All registrations",
    "passenger_low_speed": "Passenger and low-speed vehicles",
    "pickup_suv_van": "Pickups, SUVs and vans",
    "truck": "Trucks", "bus": "Buses", "trailer": "Trailers", "motorcycle": "Motorcycles",
    "off_road": "Off-road vehicles", "recreational": "Recreational vehicles",
    "snowmobile": "Snowmobiles", "boat": "Boats", "commercial_plate": "Commercial plates",
    "gross_vehicle_weight": "Weight-based plates (gross vehicle weight)",
    "irp_with_prorate_plate": "Interstate fleet plates (IRP prorate)",
    "electric": "Electric vehicles", "titles": "Titles issued",
}  # fmt: skip
COLUMN_LABELS = {  # the same, short, for the table's headings
    "all": "All", "passenger_low_speed": "Passenger", "pickup_suv_van": "Pickup/SUV/van",
    "truck": "Truck", "bus": "Bus", "trailer": "Trailer", "motorcycle": "Motorcycle",
    "off_road": "Off-road", "recreational": "RV", "snowmobile": "Snowmobile", "boat": "Boat",
    "commercial_plate": "Commercial", "gross_vehicle_weight": "Weight-based",
    "irp_with_prorate_plate": "IRP prorate", "electric": "Electric", "titles": "Titles",
}  # fmt: skip
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]  # fmt: skip


def write_page(out: Path, slug: str, template: str, data: dict, script: str, **context) -> None:
    """A data page at out/slug/: index.html from the template, data.json, and its script as
    app.js (a module importing static/sda.js)."""
    folder = out / slug
    folder.mkdir()
    (folder / "data.json").write_text(json.dumps(data, separators=(",", ":")))
    shutil.copy(HERE / "static" / script, folder / "app.js")
    (folder / "index.html").write_text(env().get_template(template).render(
        **context, root="../", current=f"{slug}/", app=True))  # fmt: skip


def population(years: list[int], path: Path = POPULATION) -> dict:
    """Each year's population (pipelines/population), statewide and by county; a year past
    the latest estimate takes the latest (estimated_from says which)."""
    import csv

    by = {}
    with path.open() as f:
        for r in csv.DictReader(f):
            by[(r["geoid"], int(r["year"]))] = int(r["population"])
    latest = max(y for _, y in by)
    used = [min(y, latest) for y in years]
    geoids = sorted({g for g, _ in by if g.startswith("0500000US")})
    return {"estimated_from": used,
            "state": [by.get((STATE, y)) for y in used],
            "counties": {g: [by.get((g, y)) for y in used] for g in geoids}}  # fmt: skip


def voter_registration(out: Path, common: dict, build_dir: Path) -> tuple[dict, dict]:
    import maps

    data = json.loads((build_dir / "chart.json").read_text())
    latest = data["dates"][-1]
    write_page(out, "voter-registration", "voter_registration.html", data,
               "voter-registration.js", **common, parties=data["parties"],
               map_svg=maps.county_svg(), first_year=data["dates"][0][:4],
               latest_label=long_date(latest), snapshots=len(data["dates"]),
               downloads={"base": f"{RELEASES}/download/voter-registration-data",
                          "sources": f"{RELEASES}/tag/voter-registration-sources"})  # fmt: skip
    card = {
        "path": "voter-registration/", "title": "Voter registration by party",
        "summary": "Registered voters by party, statewide and in each county, 1976 to now.",
        "source": 'South Dakota Secretary of State, <a href="https://sdsos.gov/elections-voting/'
                  'upcoming-elections/voter-registration-totals/voter-registration-by-county.aspx'
                  '">voter registration totals</a>',
        "covers": f"The state and its 66 counties, {len(data['dates'])} reports since "
                  f"{data['dates'][0][:4]}",
        "latest": long_date(latest),
    }  # fmt: skip
    health = {"latest_report": latest, "reports": len(data["dates"]),
              "data_built": data["updated"]}  # fmt: skip
    return card, health


def vehicle_registration(out: Path, common: dict, build_dir: Path) -> tuple[dict, dict]:
    import maps

    data = json.loads((build_dir / "chart.json").read_text())
    years = data["years"]
    data["population"] = population(years)
    data["labels"] = MEASURE_LABELS
    partial = data["partial"]
    latest_label = (f"{MONTHS[partial['through_month'] - 1]} {partial['year']}" if partial
                    else str(years[-1]))  # fmt: skip
    estimated = sorted({u for y, u in zip(years, data["population"]["estimated_from"],
                                          strict=True) if u != y})  # fmt: skip
    note = (f"years after {estimated[0]} use the {estimated[0]} estimate, the latest the "
            f"Census Bureau has published through its API" if estimated else "")  # fmt: skip
    columns = ["all", *data["measures"]]
    write_page(out, "vehicle-registration", "vehicle_registration.html", data,
               "vehicle-registration.js", **common, map_svg=maps.county_svg(), years=years,
               first_year=years[0], last_year=years[-1], latest_label=latest_label,
               partial=partial and {**partial,
                                    "month_name": MONTHS[partial["through_month"] - 1]},
               measure_options=[(m, MEASURE_LABELS[m]) for m in columns],
               table_columns=[(m, COLUMN_LABELS[m]) for m in columns],
               population_note=note,
               downloads={"base": f"{RELEASES}/download/motor-vehicles-data"})  # fmt: skip
    card = {
        "path": "vehicle-registration/", "title": "Vehicle registrations",
        "summary": "Vehicles registered each year by type, statewide and in each county, "
                   "with rates per resident.",
        "source": 'South Dakota Department of Revenue, <a href="https://sddor.seamlessdocs.com/'
                  'sc/statistics/">title and registration statistics</a>',
        "covers": f"The state and its 66 counties, {years[0]}–{years[-1]}"
                  + (f" and {partial['year']} so far" if partial else ""),
        "latest": latest_label,
    }  # fmt: skip
    health = {"latest_year": years[-1], "through": latest_label, "data_built": data["updated"]}
    return card, health


# Each data page: its slug, its builder and the pipeline build folder it reads (run the
# pipeline first). Pages appear in the navbar and on the home page in this order.
PAGES = [
    ("voter-registration", "Voter registration", voter_registration,
     REPO / ".cache" / "voter-registration" / "build"),
    ("vehicle-registration", "Vehicle registrations", vehicle_registration,
     REPO / ".cache" / "motor-vehicles" / "build"),
]  # fmt: skip


def build(out: Path = OUT, inputs: dict[str, Path] | None = None) -> dict:
    """Write the site to out; returns _health.json's contents. inputs: a page's build folder
    in place of its pipeline's (by slug)."""
    inputs = inputs or {}
    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    for name in ("site.css", "sda.js"):
        shutil.copy(HERE / "static" / name, out / "static" / name)
    built = datetime.now(UTC)
    common = {"site_name": SITE_NAME, "built": built.strftime("%B %-d, %Y"),
              "build_id": built.strftime("%Y%m%d%H%M"),
              "site_description": "Public records about South Dakota and its places, kept with "
                                  "their sources.",
              "nav": [*({"path": f"{slug}/", "title": title} for slug, title, *_ in PAGES),
                      {"path": "about/", "title": "About"}]}  # fmt: skip
    templates = env()
    cards, health = [], {"built": built.isoformat(timespec="seconds"), "pages": {}}
    for slug, _, page, folder in PAGES:
        card, page_health = page(out, common, inputs.get(slug, folder))
        cards.append(card)
        health["pages"][slug] = page_health
    (out / "index.html").write_text(
        templates.get_template("home.html").render(**common, root="", current="", pages=cards)
    )
    (out / "about").mkdir()
    (out / "about" / "index.html").write_text(
        templates.get_template("about.html").render(**common, root="../", current="about/")
    )
    (out / ".nojekyll").write_text("")
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
