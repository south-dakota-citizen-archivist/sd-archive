import contextlib
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode

from django.core.management.base import BaseCommand
from django.db import connection

from sdgis.beacon import BASE_URL


@dataclass(frozen=True)
class ParcelLayer:
    url: str  # parcel layer in the census
    field: str  # field holding Beacon's KeyValue
    layer: str | None  # Layer= in report links; some apps reject it
    confirmed: bool  # a generated link has been checked in a browser
    width: int = 0  # zero-pad keys to this many characters
    report: str = ""  # a report link with {key}, for apps where name-based links fail


# Beacon source slug -> where its parcel IDs come from
PARCEL_LAYERS = {
    "lawrence-county-beacon": ParcelLayer(
        "https://www.1stdistrict.org/arcgis/rest/services/Lawrence/lawrencemapnet2/FeatureServer/0",
        "PARCEL_ID",
        "Parcels",
        confirmed=True,
    ),
    "meade-county-beacon": ParcelLayer(
        "https://gis.rcgov.org/server/rest/services/AGOL/AGOL/MapServer/20",
        "PARCELID",
        None,
        confirmed=True,
    ),
    # Beacon's KeyValue is the county record ID (MRRDID), zero-padded: 84440 -> 084440
    "minnehaha-county-beacon": ParcelLayer(
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
    "moody-county-beacon": ParcelLayer(
        "https://www.1stdistrict.org/arcgis/rest/services/Moody/moodymapnet/FeatureServer/7",
        "PIN",
        "Parcels",
        confirmed=True,
    ),
}

# Real parcel IDs start with a digit; this drops blanks and placeholders like ROAD or LAKE
SQL = """
SELECT DISTINCT btrim(f.data ->> %(field)s) AS pid
FROM records_record f JOIN records_recordset rs ON rs.id = f.recordset_id
JOIN census_dataset d ON d.id = rs.gis_dataset_id
WHERE d.url = %(url)s AND f.dropped_at IS NULL
  AND btrim(f.data ->> %(field)s) ~ '^[0-9][0-9A-Za-z.-]*$'
ORDER BY pid
"""


def report_url(source, parcels: ParcelLayer, key: str) -> str:
    key = key.zfill(parcels.width)
    if parcels.report:
        return parcels.report.format(key=quote(key))
    query = {"App": source.url.split("App=")[1].split("&")[0]}
    if parcels.layer:
        query["Layer"] = parcels.layer
    query |= {"PageType": "Report", "KeyValue": key}
    return f"{BASE_URL}/Application.aspx?{urlencode(query)}"


class Command(BaseCommand):
    help = (
        "Write a CSV of Beacon parcel report links, built from parcel IDs in harvested ArcGIS "
        "layers, for counties with both (see PARCEL_LAYERS)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "sources", nargs="*", help="Beacon source slugs (default: all with confirmed links)"
        )
        parser.add_argument("-o", "--output", help="CSV path (default: stdout)")

    def handle(self, *args, sources, output, **options):
        with contextlib.ExitStack() as stack:
            out = stack.enter_context(Path(output).open("w", newline="")) if output else sys.stdout
            default = [slug for slug, p in PARCEL_LAYERS.items() if p.confirmed]
            self.write(csv.writer(out), sources or default)

    def write(self, writer, slugs: list[str]) -> None:
        from census.models import Source

        writer.writerow(["county", "parcel_id", "report_url"])
        for slug in slugs:
            source = Source.objects.get(slug=slug)
            parcels = PARCEL_LAYERS[slug]
            if not parcels.confirmed:
                self.stderr.write(f"{slug}: link format unconfirmed; check one before using these")
            with connection.cursor() as cursor:
                cursor.execute(SQL, {"field": parcels.field, "url": parcels.url})
                ids = [row[0] for row in cursor.fetchall()]
            if not ids:
                self.stderr.write(f"{slug}: no parcel IDs; is {parcels.url} harvested?")
            for pid in ids:
                writer.writerow([source.jurisdiction.name, pid, report_url(source, parcels, pid)])
            self.stderr.write(f"{source.jurisdiction.name}: {len(ids):,} links")
