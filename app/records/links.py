"""Links between records that name the same thing by an ID: a property sale and the GIS
features of the parcels it lists.

A sale gives its parcels' IDs (data["parcels"]) and is tied to its county. A GIS feature
carries its county's IDs in known fields: a map app feature its key and its report's
"Quick Ref ID", a harvested parcel layer the field named for it in LAYERS, with the county
the layer covers (not always the census dataset's place, which can be the host's). A sale's ID
links to every feature in the same county carrying that ID (a parcel drawn in several pieces
gives several features).

IDs are compared with spaces trimmed and letters upper-cased, and an ID of digits only
without its leading zeros (Minnehaha's sales give 065514 one year and 65514 the next). An ID
that would then stand for two different IDs in a county's GIS data links nothing; the run
counts those.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

KIND = "parcel"
SALES = "property-sales"
GIS_DATASET = "gis-data"

# A harvested parcel layer (its census dataset URL) → the field holding the county's ID,
# and the county (GEOID) the layer covers
LAYERS = {
    "https://www.1stdistrict.org/arcgis/rest/services/Lawrence/lawrencemapnet2/FeatureServer/0":
        ("PARCEL_ID", "46081"),
    "https://gis.rcgov.org/server/rest/services/AGOL/AGOL/MapServer/20": ("PARCELID", "46093"),
    "https://gis.minnehahacounty.gov/minnemap/rest/services/OpenData/MinnehahaCountyParcels"
    "/MapServer/0": ("MRRDID", "46099"),
    "https://www.1stdistrict.org/arcgis/rest/services/Moody/moodymapnet/FeatureServer/7":
        ("PIN", "46101"),
}  # fmt: skip

# A map app feature: where its IDs are
MAP_APP_FIELDS = {"key": ("key",), "Quick Ref ID": ("sections", "Summary", "Quick Ref ID")}


def normalize(value) -> str:
    text = str(value or "").strip().upper()
    return (text.lstrip("0") or "0") if text.isdigit() else text


def _get(data: dict, path: tuple[str, ...]):
    for part in path:
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


@dataclass
class Result:
    links: int = 0
    sales: int = 0  # sales with at least one link
    ids: Counter = field(default_factory=Counter)  # sale IDs: linked, not found, ambiguous
    by_county: dict = field(default_factory=dict)


def _county(geoids) -> str | None:
    found = [g for g in geoids if len(g) == 5 and g.startswith("46")]
    return found[0] if len(found) == 1 else None


def gis_ids():
    """(county GEOID, normalized ID) → [(record pk, field, ID as written)], from every
    current GIS feature with a known ID field."""
    from records.models import Record, RecordSet

    index = defaultdict(list)
    for rs in RecordSet.objects.filter(dataset=GIS_DATASET).select_related("gis_dataset"):
        layer = rs.gis_dataset
        records = Record.objects.current().filter(recordset=rs)
        if layer is not None:
            if layer.url not in LAYERS:
                continue
            name, county = LAYERS[layer.url]
            for pk, value in records.values_list("pk", f"data__{name}").iterator():
                if normalize(value):
                    index[(county, normalize(value))].append((pk, name, str(value).strip()))
            continue
        places = defaultdict(list)
        for pk, geoid in records.values_list("pk", "places__geoid"):
            if geoid:
                places[pk].append(geoid)
        for pk, data in records.values_list("pk", "data").iterator():
            county = _county(places.get(pk, []))
            if not county:
                continue
            for name, path in MAP_APP_FIELDS.items():
                value = _get(data, path)
                if normalize(value):
                    index[(county, normalize(value))].append((pk, name, str(value).strip()))
    return index


def link_sales(*, dry_run: bool = False) -> Result:
    """Rebuild the parcel links from property sales to GIS features."""
    from django.db import transaction

    from records.models import Record, RecordLink

    index = gis_ids()
    covered = {county for county, _ in index}  # counties with GIS data to link to
    ambiguous = {k for k, hits in index.items() if len({raw for _, _, raw in hits}) > 1}
    sales = Record.objects.current().filter(recordset__slug=SALES)
    places = defaultdict(list)
    for pk, geoid in sales.values_list("pk", "places__geoid"):
        if geoid:
            places[pk].append(geoid)
    out, links = Result(), []
    counties: dict[str, Counter] = defaultdict(Counter)
    for pk, parcels in sales.values_list("pk", "data__parcels").iterator():
        county = _county(places.get(pk, []))
        if county not in covered or not parcels:
            continue
        linked = False
        for raw in parcels:
            key = (county, normalize(raw))
            if key in ambiguous:
                out.ids["ambiguous"] += 1
                counties[county]["ambiguous"] += 1
                continue
            hits = index.get(key)
            out.ids["linked" if hits else "not found"] += 1
            counties[county]["linked" if hits else "not found"] += 1
            for to_pk, name, _ in hits or []:
                links.append(RecordLink(from_record_id=pk, to_record_id=to_pk, kind=KIND,
                                        from_field="parcels", to_field=name,
                                        value=str(raw).strip()[:200]))  # fmt: skip
                linked = True
        out.sales += linked
    links = list({(x.from_record_id, x.to_record_id): x for x in links}.values())
    out.links = len(links)
    out.by_county = {c: dict(n) for c, n in sorted(counties.items()) if n["linked"]}
    if not dry_run:
        with transaction.atomic():
            RecordLink.objects.filter(kind=KIND, from_record__recordset__slug=SALES).delete()
            RecordLink.objects.bulk_create(links, batch_size=5000)
    return out
