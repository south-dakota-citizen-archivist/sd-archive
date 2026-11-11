"""The pre-2018 campaign finance system's filings: one record per filing."""

from entities.models import Mention
from records.models import Record, RecordSet
from records.sources.campaign_finance_legacy import source

URL = ("https://sdsos.gov/elections-voting/campaign-finance/search/Document.aspx"
       "?CabId=523E2A2A&DocGuid=20180119-2012-1860-4769-37484848d9bb")  # fmt: skip


def test_a_filing_from_the_listing():
    meta = {"year": "2001", "last_name": "Long", "first_name": "Larry", "committee_name": "",
            "source_system": "old"}  # fmt: skip
    assert source.row(meta, URL) == {
        "doc_guid": "20180119-2012-1860-4769-37484848d9bb", "cab_id": "523E2A2A",
        "year": 2001, "first_name": "Larry", "last_name": "Long",
        "committee_name": None}  # fmt: skip


def test_mentions_name_the_filer_and_the_committee(db):
    rs = RecordSet.objects.create(slug="campaign-finance-legacy", name="Old system",
                                  dataset="campaign-finance")  # fmt: skip
    record = Record.objects.create(
        recordset=rs, key="g", content_hash="x", first_seen="2026-01-01T00:00Z",
        data=source.row({"year": "2004", "first_name": "Larry", "last_name": "Long",
                         "committee_name": "SDUCDA PAC"}, URL),
    )  # fmt: skip
    Mention.objects.bulk_create(source.mentions(record))
    assert set(Mention.objects.values_list("role", "name")) == {
        ("filer", "Larry Long"),
        ("committee", "SDUCDA PAC"),
    }
