"""GIS layers and county map apps share one catalog dataset, gis-data; the map app record set
is gis-data, its records keyed `<app>:<feature key>`."""

from django.db import migrations

OLD = ("gis-parcels", "beacon-parcels")
APP = "LawrenceCountySD"


def forward(apps, schema_editor):
    RecordSet = apps.get_model("records", "RecordSet")
    Mention = apps.get_model("entities", "Mention")
    old = RecordSet.objects.filter(slug="lawrence-county-parcels").first()
    if old is not None:
        old.slug, old.name = "gis-data", "GIS data"
        old.save(update_fields=["slug", "name"])
        with schema_editor.connection.cursor() as c:
            c.execute(
                "UPDATE records_record SET key = %s || key WHERE recordset_id = %s "
                "AND key NOT LIKE %s",
                [f"{APP}:", old.pk, f"{APP}:%"],
            )
        with schema_editor.connection.cursor() as c:  # data: the app, and `key` for parcel_id
            c.execute(
                "UPDATE records_record SET data = jsonb_build_object('app', %s::text, 'key', "
                "data->'parcel_id') || (data - 'parcel_id') WHERE recordset_id = %s "
                "AND data ? 'parcel_id'",
                [APP, old.pk],
            )
    RecordSet.objects.filter(dataset__in=OLD).update(dataset="gis-data")
    Mention.objects.filter(dataset__in=OLD).update(dataset="gis-data")


class Migration(migrations.Migration):
    dependencies = [("records", "0007_document_dropped_at"), ("entities", "0001_mention")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
