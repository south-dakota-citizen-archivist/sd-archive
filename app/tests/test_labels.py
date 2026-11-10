from datetime import UTC, datetime, timedelta

import pytest

from harvest import service
from harvest.labels import Labels, format_date
from records.models import Run
from sdgis.arcgis import client

STATUS = {
    "type": "codedValue",
    "name": "Status",
    "codedValues": [{"name": "Active", "code": 1}, {"name": "Retired", "code": 2}],
}
LAYER = {
    "typeIdField": "KIND",
    "types": [
        {"id": 1, "name": "Hydrant", "domains": {"STATUS": {"type": "inherited"}}},
        {
            "id": 2,
            "name": "Valve",
            "domains": {
                "STATUS": {
                    "type": "codedValue",
                    "codedValues": [{"name": "Open", "code": 1}, {"name": "Closed", "code": 2}],
                }
            },
        },
    ],
    "fields": [
        {"name": "KIND", "type": "esriFieldTypeSmallInteger", "alias": "Kind"},
        {"name": "STATUS", "type": "esriFieldTypeInteger", "alias": "Status", "domain": STATUS},
        {"name": "INSTALLED", "type": "esriFieldTypeDate", "alias": "Installed"},
        {"name": "NOTE", "type": "esriFieldTypeString", "alias": "Note"},
    ],
    "editingInfo": {"lastEditDate": 1735689600000},
}


def test_describe_fields():
    kind, status, installed, note = client.describe_fields(LAYER)
    assert kind["codes"] == {"1": "Hydrant", "2": "Valve"} and kind["subtype_field"] is True
    assert status["codes"] == {"1": "Active", "2": "Retired"}
    assert status["subtype_codes"] == {"2": {"1": "Open", "2": "Closed"}}, "inherited is skipped"
    assert installed == {"name": "INSTALLED", "type": "esriFieldTypeDate", "alias": "Installed"}
    assert "codes" not in note


def test_last_edit():
    assert client.last_edit(LAYER) == datetime(2025, 1, 1, tzinfo=UTC)
    assert client.last_edit({"editingInfo": {}}) is None
    assert client.last_edit({}) is None


@pytest.mark.parametrize(
    ("props", "field", "expected"),
    [
        ({"KIND": 1, "STATUS": 2}, "STATUS", ("Retired", 2)),
        ({"KIND": 2, "STATUS": 2}, "STATUS", ("Closed", 2)),  # the subtype's own labels
        ({"KIND": 2.0, "STATUS": 1.0}, "STATUS", ("Open", 1.0)),  # floats match integer codes
        ({"KIND": 2}, "KIND", ("Valve", 2)),
        ({"STATUS": 9}, "STATUS", (9, None)),  # unknown codes stay as they are
        ({"STATUS": None}, "STATUS", (None, None)),
        ({"INSTALLED": "2025-11-18T00:00:00.000Z"}, "INSTALLED", ("2025-11-18", None)),
        ({"NOTE": "2025-11-18T00:00:00.000Z"}, "NOTE", ("2025-11-18T00:00:00.000Z", None)),
    ],
)
def test_labels_cell(props, field, expected):
    assert Labels(client.describe_fields(LAYER)).cell(field, props) == expected


def test_codes_that_repeat_themselves_arent_labels():
    fields = [{"name": "COUNTY", "type": "", "codes": {"Lake": "Lake", "MN": "Minnehaha"}}]
    labels = Labels(fields)
    assert labels.cell("COUNTY", {"COUNTY": "Lake"}) == ("Lake", None)
    assert labels.cell("COUNTY", {"COUNTY": "MN"}) == ("Minnehaha", "MN")
    assert not Labels([{"name": "X", "type": "", "codes": {"A": "A"}}]).coded_fields


def test_format_date():
    assert format_date("2025-11-18T14:03:09.000Z") == "2025-11-18 14:03 UTC"
    assert format_date("2025-11-18T14:03:09") == "2025-11-18 14:03"
    assert format_date("not a date") == "not a date"


def with_codes(server):
    """Give the fake layer a coded NAME field."""
    domain = {"type": "codedValue", "codedValues": [{"name": "First", "code": "Town 1"}]}
    fields = [{**f, "domain": domain} if f["name"] == "NAME" else f for f in server.fields]
    server.extra_metadata["fields"] = fields


def test_harvest_refreshes_labels_and_csv_has_them(client, dataset, server):
    with_codes(server)
    service.harvest_dataset(dataset)
    dataset.refresh_from_db()
    assert next(f for f in dataset.fields if f["name"] == "NAME")["codes"] == {"Town 1": "First"}

    csv = b"".join(client.get(f"/datasets/{dataset.pk}/download.csv").streaming_content).decode()
    header, first = csv.splitlines()[:2]
    assert header.startswith("_key,OBJECTID,NAME,NAME_label,OWNER_NAM")
    assert first.startswith("1,1,Town 1,First,")

    page = client.get(f"/datasets/{dataset.pk}/table/").content.decode()
    assert "First" in page and "Town 1" in page


def test_harvest_skips_when_unchanged_at_source(dataset, server):
    server.extra_metadata["editingInfo"] = {"lastEditDate": 1735689600000}
    first = service.harvest_dataset(dataset)
    assert first.status == Run.Status.SUCCEEDED
    assert first.source_edited_at == datetime(2025, 1, 1, tzinfo=UTC)

    calls = len(server.query_calls())
    second = service.harvest_dataset(dataset)
    assert second.status == Run.Status.SKIPPED
    assert second.unchanged == 10 and not second.has_changes
    assert len(server.query_calls()) == calls, "no features downloaded"

    assert service.harvest_dataset(dataset, force=True).status == Run.Status.SUCCEEDED

    server.extra_metadata["editingInfo"] = {"lastEditDate": 1735776000000}
    assert service.harvest_dataset(dataset).status == Run.Status.SUCCEEDED, "new edit date"

    server.ids = server.ids[:-1]
    assert service.harvest_dataset(dataset).status == Run.Status.SUCCEEDED, "count changed"

    Run.objects.update(started_at=datetime.now(UTC) - timedelta(days=8))
    assert service.harvest_dataset(dataset).status == Run.Status.SUCCEEDED, "a week old"


def test_harvest_without_edit_date_always_downloads(dataset, server):
    service.harvest_dataset(dataset)
    assert service.harvest_dataset(dataset).status == Run.Status.SUCCEEDED
