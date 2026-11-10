from django.db import migrations

# "assess" alone caught risk/condition assessments; "apprais" caught tree appraisals
UPDATES = {
    "assessed-values": {
        "name_pattern": r"assessed|assessment roll|valuation|equaliz|appraisal district",
        "field_pattern": (
            r"^assessed|^assd|appraised_?val|land_?val|bldg_?val|build_?tv|land_?tv|mh_?tv"
            r"|tot_?val|value_?(land|na|ag)|taxable_?val"
        ),
    },
}


def update(apps, schema_editor):
    Topic = apps.get_model("census", "Topic")
    for slug, fields in UPDATES.items():
        Topic.objects.filter(slug=slug).update(**fields)


class Migration(migrations.Migration):
    dependencies = [("census", "0003_dataset_duplicate_of")]

    operations = [migrations.RunPython(update, migrations.RunPython.noop)]
