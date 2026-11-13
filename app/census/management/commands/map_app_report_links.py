import contextlib
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from django.core.management.base import BaseCommand
from django.db import connection

from sdgis.mapapps import BASE_URL


@dataclass(frozen=True)
class KeyLayer:
    url: str  # the layer in the census holding the keys
    field: str  # field holding the map app's KeyValue
    layer: str | None  # Layer= in report links; some apps reject it
    confirmed: bool  # a generated link has been checked in a browser
    width: int = 0  # zero-pad keys to this many characters
    report: str = ""  # a report link with {key}, for apps where name-based links fail


# map app source slug -> where its features' keys come from
KEY_LAYERS = {
    "lawrence-county-map-app": KeyLayer(
        "https://www.1stdistrict.org/arcgis/rest/services/Lawrence/lawrencemapnet2/FeatureServer/0",
        "PARCEL_ID",
        "Parcels",
        confirmed=True,
    ),
    "meade-county-map-app": KeyLayer(
        "https://gis.rcgov.org/server/rest/services/AGOL/AGOL/MapServer/20",
        "PARCELID",
        None,
        confirmed=True,
    ),
    # the map app's KeyValue is the county record ID (MRRDID), zero-padded: 84440 -> 084440
    "minnehaha-county-map-app": KeyLayer(
        "https://gis.minnehahacounty.gov/minnemap/rest/services"
        "/OpenData/MinnehahaCountyParcels/MapServer/0",
        "MRRDID",
        None,
        confirmed=True,
        width=6,
        report=(
            f"{BASE_URL}/Application.aspx?AppID=1180&LayerID=35026&PageTypeID=4"
            "&PageID=13301&KeyValue={key}"
        ),
    ),
    "moody-county-map-app": KeyLayer(
        "https://www.1stdistrict.org/arcgis/rest/services/Moody/moodymapnet/FeatureServer/7",
        "PIN",
        "Parcels",
        confirmed=True,
    ),
}

# Real keys start with a digit; this drops blanks and placeholders like ROAD or LAKE
SQL = """
SELECT DISTINCT btrim(f.data ->> %(field)s) AS pid
FROM records_record f JOIN records_recordset rs ON rs.id = f.recordset_id
JOIN census_dataset d ON d.id = rs.gis_dataset_id
WHERE d.url = %(url)s AND f.dropped_at IS NULL
  AND btrim(f.data ->> %(field)s) ~ '^[0-9][0-9A-Za-z.-]*$'
ORDER BY pid
"""


def report_url(source, keys: KeyLayer, key: str) -> str:
    key = key.zfill(keys.width)
    if keys.report:
        return keys.report.format(key=quote(key))
    query = {"App": source.url.split("App=")[1].split("&")[0]}
    if keys.layer:
        query["Layer"] = keys.layer
    query |= {"PageType": "Report", "KeyValue": key}
    return f"{BASE_URL}/Application.aspx?{urlencode(query)}"


class Command(BaseCommand):
    help = (
        "Write a CSV of map app report links, built from feature keys in harvested ArcGIS "
        "layers, for counties with both (see KEY_LAYERS)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "sources", nargs="*", help="map app source slugs (default: all with confirmed links)"
        )
        parser.add_argument("-o", "--output", help="CSV path (default: stdout)")

    def handle(self, *args, sources, output, **options):
        with contextlib.ExitStack() as stack:
            out = stack.enter_context(Path(output).open("w", newline="")) if output else sys.stdout
            default = [slug for slug, p in KEY_LAYERS.items() if p.confirmed]
            self.write(csv.writer(out), sources or default)

    def write(self, writer, slugs: list[str]) -> None:
        from census.models import Source

        writer.writerow(["county", "key", "report_url"])
        for slug in slugs:
            source = Source.objects.get(slug=slug)
            keys = KEY_LAYERS[slug]
            if not keys.confirmed:
                self.stderr.write(f"{slug}: link format unconfirmed; check one before using these")
            with connection.cursor() as cursor:
                cursor.execute(SQL, {"field": keys.field, "url": keys.url})
                ids = [row[0] for row in cursor.fetchall()]
            if not ids:
                self.stderr.write(f"{slug}: no keys; is {keys.url} harvested?")
            for pid in ids:
                writer.writerow([source.jurisdiction.name, pid, report_url(source, keys, pid)])
            self.stderr.write(f"{source.jurisdiction.name}: {len(ids):,} links")
