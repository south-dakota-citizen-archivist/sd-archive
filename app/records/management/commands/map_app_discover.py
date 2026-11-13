"""Find out what a county map app shows: sample its features' report pages and write its profile
(records/sources/_map_app/apps/<App>.json): the map's layers and settings, each report
section's shape and fields, and the sections that need an account."""

import json
import random
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from records.sources._map_app import parse, profile, tiles
from records.sources._map_app.session import Blocked, Session


def browser_profile() -> Path:
    return Path(settings.MEDIA_ROOT) / "browser" / "map-app"


class Command(BaseCommand):
    help = "Sample a county map app's report pages and write what its sections and fields are"

    def add_arguments(self, parser):
        parser.add_argument("app", help='the app name in its URLs, e.g. "LawrenceCountySD"')
        parser.add_argument("--sample", type=int, default=30, help="report pages to read")
        parser.add_argument("--stored", action="store_true",
                            help="use only report pages already stored; no requests")  # fmt: skip

    def handle(self, *args, app, sample, stored, **options):
        from sdfetch import Store

        store = Store(Path(settings.MEDIA_ROOT) / "raw" / "map-app-discovery" / app)
        if stored:
            pages = sorted(store.root.glob("reports/*.html"))
            if not pages:
                raise CommandError(f"no stored report pages for {app}")
            config = (json.loads(store.read("map-config.json")) if store.has("map-config.json")
                      else parse.map_config(pages[0].read_bytes()))  # fmt: skip
            reports = [parse.report(p.read_bytes()) for p in pages]
        else:
            config, reports = self.sample(app, sample, store)
        out = profile.build(reports, config)
        path = profile.save(app, out)
        w = self.stdout.write
        w(f"{len(reports)} report pages; layer {out['map'].get('layer')} "
          f"({out['map'].get('key_field')}), SRID {out['map'].get('srid')}")  # fmt: skip
        for title, s in out["sections"].items():
            w(f"  {title} ({s['kind']}, {s['pages']} pages): {', '.join(s['fields'])[:150]}")
        w(f"  needs an account: {', '.join(out['restricted']) or 'nothing'}")
        w(self.style.SUCCESS(f"Wrote {path}"))

    def sample(self, app: str, n: int, store) -> tuple[dict, list[dict]]:
        rng = random.Random()
        reports = []
        with Session(app, browser_profile()) as session:
            config = session.config
            store.save("map-config.json", json.dumps(config).encode(), session.map_url)
            extent = config.get("MaxExtent") or config.get("CurrentExtent")
            keys: list[str] = []
            for box in rng.sample(tiles.grid(extent, 12), 12):
                answer = session.api("GetVectorLayer", {
                    "layerId": config["LayerId"], "useSelection": False, "ext": box,
                    "wkt": None, "spatialRelation": 0, "featureLimit": 0})  # fmt: skip
                keys += [f["Key"] for f in answer]
                if len(set(keys)) >= n * 5:
                    break
            keys = sorted(set(keys))
            for key in rng.sample(keys, min(n, len(keys))):
                name = (
                    f"reports/{''.join(c if c.isalnum() or c in '-.' else '_' for c in key)}.html"
                )
                try:
                    html = store.read(name) if store.has(name) else session.report(key)
                except Blocked as e:
                    self.stderr.write(f"stopped: {e}")
                    break
                store.save(name, html, parse.report_url(config, key))
                reports.append(parse.report(html))
        if not reports:
            raise CommandError(f"no report pages read for {app}")
        return config, reports
