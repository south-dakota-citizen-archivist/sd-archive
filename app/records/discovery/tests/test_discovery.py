"""Records discovery: a small fixture site crawled, its candidates compiled by rule, and a
review by a stand-in model (registered with sdllm, so no real model runs)."""

import json
from pathlib import Path

import pytest

from records.discovery import review, signals
from records.discovery.crawl import crawl, is_file, site_of

SITE = Path(__file__).parent / "fixtures" / "site"
HOME = "https://www.example.gov/"


class SiteFetcher:
    """The fixture site: /x is fixtures/site/x.html; anything else is missing."""

    store = None

    def __init__(self):
        self.asked = []

    def request(self, method, url, **kwargs):
        self.asked.append(url)
        path = url.removeprefix("https://www.example.gov/").strip("/") or "index"
        file = SITE / f"{path}.html"
        if not file.exists():
            raise OSError(f"404 {url}")
        return type("R", (), {"content": file.read_bytes()})()


def pages(**kwargs):
    return crawl(SiteFetcher(), HOME, **kwargs)


def test_the_crawl_stays_on_the_site_and_notes_files_and_portals():
    found = pages()
    assert [p.url.removeprefix(HOME) for p in found] == ["", "finance", "council", "benefits",
                                                          "deep"]  # fmt: skip
    home = found[0]
    assert {link.url for link in home.external} == {
        "https://examplesd.portal.civicclerk.com/",
        "https://beacon.schneidercorp.com/Application.aspx?App=ExampleCountySD",
        "https://other.example.com/",
    }
    assert len(found[1].files) == 5 and site_of(HOME) == "example.gov"
    assert is_file("https://x.gov/DocumentCenter/View/12/Budget") and not is_file(HOME)


def test_depth_and_page_budget():
    assert [p.url.removeprefix(HOME) for p in pages(max_depth=1)] == ["", "finance", "council",
                                                                       "benefits"]  # fmt: skip
    assert len(pages(max_pages=2)) == 2


def test_candidates():
    found = {(c.kind, c.name): c for c in signals.candidates(pages())}
    clerk = found[("platform", "CivicClerk")]
    assert clerk.urls == ["https://examplesd.portal.civicclerk.com/"]
    assert "Agendas and Minutes" in clerk.evidence
    assert found[("platform", "County map app")].covered_by == "gis-data"
    audits = found[("collection", "Finance Office")]
    assert audits.files == 5 and (min(audits.years), max(audits.years)) == (2021, 2025)
    assert ("record type", "meeting agendas and minutes") in found
    assert ("collection", "Benefits") not in found  # one file isn't a collection
    assert [p.url for p in signals.prose_pages(pages())][0] == HOME + "finance"


def test_what_the_rules_decide():
    audits = next(c for c in signals.candidates(pages()) if c.kind == "collection")
    assert review.facts(audits, 2026) == {"still_produced": "yes", "how_to_get": "download files"}
    assert review.facts(audits, 2030)["still_produced"] == "no"


@pytest.fixture
def stand_in(monkeypatch):
    """A model that answers from a script, by schema, and records what it was shown."""
    import sdllm

    shown = []

    def answer(model, messages, schema, **params):
        shown.append(messages[-1]["content"])
        if schema.__name__ == "Review":
            return json.dumps({"worth_getting": True, "title": "Audit reports",
                               "description": "Annual audits.", "kind": "records",
                               "priority": "high", "reason": "money",
                               "evidence_quote": "2023 Audit Report"})  # fmt: skip
        return json.dumps({"records": [
            {"title": "Register of claims", "description": "Claims paid.",
             "how_to_get": "records request", "quote": "official register of claims paid"},
            {"title": "Made up", "description": "Not on the page.",
             "how_to_get": "unclear", "quote": "a register of unicorns"},
        ]})  # fmt: skip

    sdllm.register_backend("standin", answer)
    return shown


def test_a_review_by_the_model(stand_in):
    found = pages()
    audits = next(c for c in signals.candidates(found) if c.kind == "collection")
    r = review.review(audits, model="standin/test", this_year=2026, cache=False)
    assert r["worth_getting"] and r["title"] == "Audit reports" and r["quote_checked"]
    assert r["still_produced"] == "yes" and r["how_to_get"] == "download files"
    named = review.prose(found[1], model="standin/test", cache=False)
    assert [n["title"] for n in named] == ["Register of claims"]  # the made-up one is dropped
