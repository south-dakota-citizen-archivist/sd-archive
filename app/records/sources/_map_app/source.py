"""GIS data from county map apps, as one record set: one record per map feature in each app,
keyed `<app>:<feature key>`, with its shape and whatever its report page shows publicly.

A run reads each app in turn: every feature's key, shape and tooltip from the map (tiles),
then each feature's report page, at most `reports_per_run` new ones a run for each app.
Tiles and report pages are stored by app and month, so a stopped run resumes and the next
month's run reads them again. A feature whose report hasn't been read this month keeps the
last one read; until every feature has one, the run is partial (nothing is marked dropped).
Each report is compared with its app's profile (map_app_discover), and what's new is counted
on the run.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from datetime import date
from pathlib import Path

from entities.mentions import from_text
from records.sources import Found, Item, Source, Stopped
from records.sources._map_app import BASE_URL, parse, profile, tiles
from records.sources._map_app.session import Blocked, Session

logger = logging.getLogger(__name__)
STATE = "0400000US46"


def browser_profile() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "browser" / "map-app"


def safe(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-." else "_" for c in key)


def latest_report(
    store, key: str, month: str, app: str = ""
) -> tuple[str, bytes] | tuple[None, None]:
    """This month's stored report page for a feature, or else the latest earlier one."""
    if store is None:
        return None, None
    prefix = f"{app}/" if app else ""
    name = f"{prefix}reports/{month}/{safe(key)}.html"
    if store.has(name):
        return month, store.read(name)
    earlier = sorted(store.root.glob(f"{prefix}reports/*/{safe(key)}.html"), reverse=True)
    if earlier:
        return earlier[0].parent.name, earlier[0].read_bytes()
    return None, None


def feature_data(app: str, feature: dict, report: dict | None, month: str | None) -> dict:
    data = {"app": app, "key": feature["key"], "tooltip": feature["tooltip"]}
    if report is not None:
        data |= {"report_month": month, "sections": report["sections"],
                 "owners": report["owners"], "photos": report["photos"],
                 "restricted": report["restricted"]}  # fmt: skip
    return data


def map_app_source(slug: str, apps: dict[str, str], *, name: str, publisher: str,
                   dataset: str = "gis-data", description: str = "",
                   reports_per_run: int = 2500) -> Source:  # fmt: skip
    """The Source for county map apps (apps: app name → the county's fully qualified GEOID)."""

    def read_app(app: str, county: str, store, month: str, budget: int):
        """One app's features and report pages: (items, checks, reports read, stopped)."""
        known = profile.load(app) or {}
        stopped = None
        with Session(app, browser_profile()) as session:
            config = session.config
            extent = config.get("MaxExtent") or config.get("CurrentExtent")

            def ask(box):
                return session.api("GetVectorLayer", {
                    "layerId": config["LayerId"], "useSelection": False, "ext": box,
                    "wkt": None, "spatialRelation": 0, "featureLimit": 0})  # fmt: skip

            try:
                found, tile_counts = tiles.read(ask, extent, store=store,
                                                prefix=f"{app}/tiles/{month}")  # fmt: skip
            except Blocked as e:
                raise Stopped([], f"{app}: blocked reading the map: {e}") from e
            features = {f["key"]: f for f in parse.features(list(found.values()))}
            todo = [k for k in features if latest_report(store, k, month, app)[0] != month]
            random.shuffle(todo)  # not in key order
            fetched, failed = 0, []
            for key in todo:
                if fetched >= budget:
                    stopped = (f"{app}: read {fetched} report pages this run; "
                               f"{len(todo) - fetched} left")  # fmt: skip
                    break
                try:
                    html = session.report(key)
                except Blocked as e:
                    stopped = f"{app}: blocked after {fetched} report pages: {e}"
                    break
                except RuntimeError as e:  # not a report page: noted, the run goes on
                    logger.warning("%s %s: %s", slug, app, e)
                    failed.append(key)
                    continue
                if store is not None:
                    store.save(f"{app}/reports/{month}/{safe(key)}.html", html,
                               parse.report_url(config, key))  # fmt: skip
                fetched += 1
            srid = int(config.get("SRID") or 0)

        items, new_things, missing = [], Counter(), 0
        for key, feature in sorted(features.items()):
            report_month, html = latest_report(store, key, month, app)
            report = parse.report(html) if html else None
            missing += report is None
            if report is not None and known:
                new_things.update(profile.differences(report, known))
            items.append(Item(key=f"{app}:{key}",
                              data=feature_data(app, feature, report, report_month),
                              source_url=parse.report_url(config, key),
                              geometry=parse.geometry(feature["wkt"], srid),
                              places=[STATE, county]))  # fmt: skip
        checks = {"tiles": tile_counts, "features": len(items), "reports_read": fetched,
                  "report_failed": failed[:50], "without_report": missing,
                  "not_in_profile": dict(new_things.most_common(20))}  # fmt: skip
        if new_things:
            logger.warning("%s %s: report pages differ from the profile: %s", slug, app,
                           dict(new_things.most_common(5)))  # fmt: skip
        if not stopped and missing:
            stopped = f"{app}: {missing} features without a report page"
        return items, checks, fetched, stopped

    def items(fetcher, today: date | None = None) -> Found:
        month = f"{today or date.today():%Y-%m}"
        store = getattr(fetcher, "store", None)
        out, checks, stops = Found(), {}, []
        for app, county in apps.items():
            found, app_checks, _, stopped = read_app(app, county, store, month, reports_per_run)
            out += found
            checks[app] = app_checks
            if stopped:
                stops.append(stopped)
        out.checks = {"apps": checks, "features": len(out)}
        if stops:
            logger.info("%s: partial run (%s)", slug, "; ".join(stops))
            raise Stopped(out, "; ".join(stops))
        return out

    def mentions(record) -> list:
        out = []
        for owner in record.data.get("owners") or []:
            for name in owner.get("names") or []:
                out += from_text(name, dataset=record.recordset.dataset, record_key=str(record),
                                 field="owners", role=(owner.get("type") or "owner").lower(),
                                 url=record.source_url, start=len(out),
                                 context={"address": owner.get("address")})  # fmt: skip
        return out

    return Source(
        slug=slug,
        items=items,
        mentions=mentions,
        name=name,
        dataset=dataset,
        publisher=publisher,
        base_url=BASE_URL,
        description=description,
    )
