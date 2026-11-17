"""Newspaper circulation statements linked to the papers in the press directory: exact names
link, close ones go to the review pages, and a decision there links (or doesn't)."""

import pytest
from django.utils import timezone

from records import newspaper_links as links
from records.models import Record, RecordLink, RecordSet


@pytest.fixture
def papers(db, tmp_path, monkeypatch):
    monkeypatch.setattr(links, "DECISIONS", tmp_path / "newspapers.toml")
    monkeypatch.setattr(links.REVIEW, "decisions", tmp_path / "newspapers.toml")
    monkeypatch.setattr("review.providers.REPO", tmp_path)  # no pipeline queues
    monkeypatch.setattr("review.providers.RECORD_PROVIDERS", ["records.newspaper_links"])
    directory = RecordSet.objects.create(slug="newspaper-directory", name="Newspapers",
                                         dataset="newspapers")  # fmt: skip
    statements = RecordSet.objects.create(slug="newspaper-circulation", name="Statements",
                                          dataset="newspaper-circulation")  # fmt: skip
    now = timezone.now()

    def paper(name, city):
        return Record.objects.create(recordset=directory, key=f"{name} ({city})", content_hash="x",
                                     first_seen=now, data={"publication_name": name,
                                                           "city": city})  # fmt: skip

    def statement(name, year):
        data = {"newspaper": name, "year": year}
        return Record.objects.create(recordset=statements, key=f"{name} {year}",
                                     content_hash="x", first_seen=now, data=data)  # fmt: skip

    return {"bonesteel": paper("Bonesteel Enterprise", "Bonesteel"),
            "huron": paper("Huron Plainsman", "Huron"),
            "plain talk": paper("Plain Talk", "Vermillion"),
            "statements": [statement("The Bonesteel Enterprise", 2004),
                           statement("Bonesteel Enterprise, LLC", 2019),
                           statement("Plainsman", 2010), statement("Plainsman", 2011),
                           statement("Vermillion Plain Talk", 2012),
                           statement("Langford Bugle", 2001)]}  # fmt: skip


def test_names_alike_link(papers):
    out = links.link_newspapers()
    assert out.names == {"exact": 1, "waiting for review": 2, "no close name or town": 1}
    linked = RecordLink.objects.filter(kind="newspaper")
    assert {(x.from_record.data["newspaper"], x.to_record_id) for x in linked} == {
        ("The Bonesteel Enterprise", papers["bonesteel"].pk),
        ("Bonesteel Enterprise, LLC", papers["bonesteel"].pk)}  # fmt: skip


def test_close_names_are_reviewed(papers, client):
    links.link_newspapers()
    items = {i.key: i for i in links.REVIEW.items()}
    assert set(items) == {"plainsman", "vermillion-plain-talk"}  # not Langford Bugle
    assert items["plainsman"].readings == {"1st": "Huron Plainsman (Huron)"}
    assert items["vermillion-plain-talk"].readings["1st"] == "Plain Talk (Vermillion)"  # by town
    page = client.get("/review/newspaper-links/plainsman/").content.decode()
    assert "Huron Plainsman (Huron)" in page
    client.post("/review/newspaper-links/plainsman/decide/", {
        "field": "paper", "from": "1st", "value": "Huron Plainsman (Huron)",
        "reason": "The statements are filed from Huron."})  # fmt: skip
    plainsman = RecordLink.objects.filter(kind="newspaper", to_record=papers["huron"])
    assert plainsman.count() == 2  # both years, at once
    client.post("/review/newspaper-links/vermillion-plain-talk/decide/", {
        "field": "paper", "from": "none", "value": "", "reason": "A different paper."})  # fmt: skip
    assert links.link_newspapers().names["not in the directory"] == 1
