from django.db import migrations

# (slug, name, layer/service name regex, field name/label regex). Case-insensitive.
# Rules are a first pass; manual assignments in the admin always win.
TOPICS = [
    ("parcels", "Parcels", r"parcel|cadastr|tax ?lot|landowner|ownership map", ""),
    (
        "assessed-values",
        "Assessed values",
        r"assess|valuation|equaliz",
        r"assess|apprais|land_?val|bldg_?val|build_?tv|land_?tv|mh_?tv|tot_?val|assd|value_?(land|na|ag)",
    ),
    (
        "property-tax",
        "Property tax",
        r"\btax(es)?\b|levy|\btif\b|tax increment",
        r"^tax_?\d|tax_?bill|tax_?amt|taxperacre|gross_tax|levy",
    ),
    (
        "sales",
        "Property sales",
        r"\bsales?\b",
        r"sale_?pr|sale_?price|selling_?pr|sale_?date|sale_?year|recent_?sal",
    ),
    ("zoning", "Zoning and land use", r"zoning|land ?use|\bflu\b|future land", r"^zon"),
    (
        "permits-licenses",
        "Permits and licenses",
        r"permit|licen[cs]|inspection|code enforce|violation",
        r"permit_?(n|num|type)|licen[cs]e_?(n|num|type)",
    ),
    ("addresses", "Addresses", r"address|e-?911|site ?point|rooftop", ""),
    (
        "transportation",
        "Roads and transportation",
        r"road|street|highway|interstate|\broute|rail|bridge|culvert|traffic|airport|trail|sign",
        "",
    ),
    (
        "crashes-safety",
        "Crashes and public safety",
        r"crash|accident|collision|fire|police|\bems\b",
        "",
    ),
    (
        "boundaries",
        "Boundaries and districts",
        r"bound|limits|district|\bward|township|annex|county line|plss|section",
        "",
    ),
    ("elections", "Elections", r"precinct|election|voting|polling|legislative", ""),
    (
        "water-environment",
        "Water and environment",
        r"flood|wetland|water|stream|river|creek|lake|hydro|watershed|aquifer|\bwells?\b|soil|contour|\bdams?\b|impair",
        "",
    ),
    (
        "wildlife-public-lands",
        "Wildlife and public lands",
        r"wildlife|species|habitat|hunting|fishing|\bparks?\b|public land|easement|forest|grassland|walk-?in",
        "",
    ),
    (
        "utilities",
        "Utilities",
        r"sewer|water ?main|storm|hydrant|utilit|electric|gas ?line|broadband|fiber|manhole|valve",
        "",
    ),
    (
        "buildings-facilities",
        "Buildings and facilities",
        r"building|structure|facilit|school|church|hospital|landmark|footprint",
        "",
    ),
]


def seed(apps, schema_editor):
    Topic = apps.get_model("census", "Topic")
    for order, (slug, name, name_pattern, field_pattern) in enumerate(TOPICS):
        Topic.objects.update_or_create(
            slug=slug,
            defaults={
                "name": name,
                "name_pattern": name_pattern,
                "field_pattern": field_pattern,
                "order": order,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("census", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
