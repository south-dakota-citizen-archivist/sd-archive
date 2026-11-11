"""A county's parcels from its Beacon app, as a record set: one record per parcel, with its
shape and everything its report page shows publicly.

A run reads every parcel's key, shape and tooltip from the map (tiles), then each parcel's
report page, at most `reports_per_run` new ones a run. Tiles and report pages are stored by
month, so a stopped run resumes and the next month's run reads them again. A parcel whose
report hasn't been read this month keeps the last one read; until every parcel has one, the
run is partial (nothing is marked dropped). Each report is compared with the app's profile
(beacon_discover), and what's new is counted on the run.
"""

from __future__ import annotations

import logging
import random
from collections import Counter
from datetime import date
from pathlib import Path

from entities.mentions import from_text
from records.sources import Found, Item, Source, Stopped
from records.sources._beacon import parse, profile, tiles
from records.sources._beacon.session import Blocked, Session

logger = logging.getLogger(__name__)
STATE = "0400000US46"


def browser_profile() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT) / "browser" / "beacon"


def safe(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-." else "_" for c in key)


def latest_report(store, key: str, month: str) -> tuple[str, bytes] | tuple[None, None]:
    """This month's stored report page for a parcel, or else the latest earlier one."""
    if store is None:
        return None, None
    name = f"reports/{month}/{safe(key)}.html"
    if store.has(name):
        return month, store.read(name)
    earlier = sorted(store.root.glob(f"reports/*/{safe(key)}.html"), reverse=True)
    if earlier:
        return earlier[0].parent.name, earlier[0].read_bytes()
    return None, None


def parcel_data(feature: dict, report: dict | None, month: str | None) -> dict:
    data = {"parcel_id": feature["key"], "tooltip": feature["tooltip"]}
    if report is not None:
        data |= {"report_month": month, "sections": report["sections"],
                 "owners": report["owners"], "photos": report["photos"],
                 "restricted": report["restricted"]}  # fmt: skip
    return data


def parcels(app: str, slug: str, *, county: str, name: str, publisher: str,
            reports_per_run: int = 2500) -> Source:  # fmt: skip
    """The Source for one county's Beacon app (county: its fully qualified GEOID)."""

    def items(fetcher, today: date | None = None) -> Found:
        month = f"{today or date.today():%Y-%m}"
        store = getattr(fetcher, "store", None)
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
                                                prefix=f"tiles/{month}")  # fmt: skip
            except Blocked as e:
                raise Stopped([], f"blocked reading the map: {e}") from e
            features = {f["key"]: f for f in parse.features(list(found.values()))}
            todo = [k for k in features if latest_report(store, k, month)[0] != month]
            random.shuffle(todo)  # not in key order
            fetched, failed = 0, []
            for key in todo:
                if fetched >= reports_per_run:
                    stopped = f"read {fetched} report pages this run; {len(todo) - fetched} left"
                    break
                try:
                    html = session.report(key)
                except Blocked as e:
                    stopped = f"blocked after {fetched} report pages: {e}"
                    break
                except RuntimeError as e:  # not a report page: noted, the run goes on
                    logger.warning("%s: %s", slug, e)
                    failed.append(key)
                    continue
                if store is not None:
                    store.save(f"reports/{month}/{safe(key)}.html", html,
                               parse.report_url(config, key))  # fmt: skip
                fetched += 1
            srid = int(config.get("SRID") or 0)

        out, new_things, missing = Found(), Counter(), 0
        for key, feature in sorted(features.items()):
            report_month, html = latest_report(store, key, month)
            report = parse.report(html) if html else None
            missing += report is None
            if report is not None and known:
                new_things.update(profile.differences(report, known))
            out.append(Item(key=key, data=parcel_data(feature, report, report_month),
                            source_url=parse.report_url(config, key),
                            geometry=parse.geometry(feature["wkt"], srid),
                            places=[STATE, county]))  # fmt: skip
        out.checks = {"tiles": tile_counts, "parcels": len(out), "reports_read": fetched,
                      "report_failed": failed[:50],
                      "without_report": missing,
                      "not_in_profile": dict(new_things.most_common(20))}  # fmt: skip
        if new_things:
            logger.warning("%s: report pages differ from the profile: %s", slug,
                           dict(new_things.most_common(5)))  # fmt: skip
        if stopped or missing:
            logger.info("%s: partial run (%s)", slug, stopped or f"{missing} without a report")
            raise Stopped(out, stopped or f"{missing} parcels without a report page")
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

    return Source(slug=slug, items=items, mentions=mentions, name=name,
                  dataset="beacon-parcels", publisher=publisher,
                  base_url=f"https://beacon.schneidercorp.com/Application.aspx?App={app}",
                  description=f"Every parcel in {name.split(' parcels')[0]}'s Beacon app: its "
                              "shape, owners, valuation and whatever else its report page "
                              "shows publicly.")  # fmt: skip
