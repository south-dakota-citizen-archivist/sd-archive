"""GIS finders: ArcGIS Online search and web maps, government domains, and the candidates
they leave for review. Answers are stand-ins shaped like the real ones (ArcGIS Online's
search and item data, crt.sh, CISA's .gov list)."""

import pytest

from census import finders
from census.finders import arcgis_online, hosts
from census.models import Source, SourceCandidate

LOCAL = [[-103.9, 44.3], [-103.5, 44.6]]  # Lawrence County
NATIONAL = [[-125.0, 24.0], [-66.0, 49.0]]
SIOUX_FALLS = "https://services.arcgis.com/YpECgwASS4iMvgsA/ArcGIS/rest/services"


class Fake:
    """Answers GETs from a function of (url, params)."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def json(self, url, params=None, **kwargs):
        self.asked.append((url, params or {}))
        return self.answer(url, params or {})

    def text(self, url, params=None, **kwargs):
        return self.json(url, params, **kwargs)


def test_local_extents():
    assert arcgis_online.local(LOCAL)
    assert not arcgis_online.local(NATIONAL)
    assert arcgis_online.local([[-100.3, 44.4], [-100.3, 44.4]])  # a point in Pierre
    assert not arcgis_online.local(None) and not arcgis_online.local([])
    assert not arcgis_online.local([[-97.0, 43.0], [-95.0, 44.0]])  # mostly Minnesota and Iowa


def test_services_roots():
    root = arcgis_online.services_root
    assert root("https://gis.example.gov/arcgis/rest/services/Parcels/MapServer/3") == (
        "https://gis.example.gov/arcgis/rest/services"
    )
    assert root(f"{SIOUX_FALLS}/Zoning/FeatureServer/0") == (
        "https://services.arcgis.com/YpECgwASS4iMvgsA/arcgis/rest/services"
    )  # one spelling
    assert root("https://example.com/tiles/{z}/{x}/{y}.png") is None and root("") is None


def test_web_map_layers_include_groups_and_tables():
    definition = {"operationalLayers": [
        {"url": "https://a.gov/arcgis/rest/services/A/MapServer"},
        {"layerType": "GroupLayer", "layers": [
            {"url": "https://b.gov/server/rest/services/B/FeatureServer/0"}]},
        {"layerType": "ArcGISFeatureLayer", "featureCollection": {}}],
        "tables": [{"url": "https://c.gov/arcgis/rest/services/C/MapServer/4"}]}  # fmt: skip
    assert arcgis_online.layer_urls(definition) == [
        "https://a.gov/arcgis/rest/services/A/MapServer",
        "https://b.gov/server/rest/services/B/FeatureServer/0",
        "https://c.gov/arcgis/rest/services/C/MapServer/4",
    ]


def test_a_search_past_the_cap_is_split_by_date():
    def answer(url, params):
        start, end = (int(x) for x in params["q"].split("modified:[")[1].rstrip("]").split(" TO "))
        n = 15_000 if end - start > 10 * arcgis_online.DAY_MS else 3  # wide ranges are too big
        if params["num"] == 1:
            return {"total": n}
        return {"results": [{"id": f"{start}-{i}"} for i in range(n)], "nextStart": -1}

    found = arcgis_online.search_all(Fake(answer), "type:x", 0, 40 * arcgis_online.DAY_MS)
    assert len(found) == 4 * 3  # four ten-day parts, three items each


def test_arcgis_online_leads(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    items = {
        "Feature Service": [
            {"id": "s1", "type": "Feature Service", "title": "Parcels", "owner": "gis.fallriver",
             "extent": LOCAL, "url": "https://services5.arcgis.com/FallRiver1/arcgis/rest/"
                                    "services/Parcels/FeatureServer"},
            {"id": "s2", "type": "Feature Service", "title": "US Rivers", "owner": "esri",
             "extent": NATIONAL, "url": "https://services.arcgis.com/Esri/arcgis/rest/services/"
                                        "Rivers/FeatureServer"}],
        "Map Service": [],
        "Web Map": [
            {"id": "m1", "type": "Web Map", "title": "Town map", "owner": "brosz",
             "extent": LOCAL, "modified": 5}],
    }  # fmt: skip
    definition = {"operationalLayers": [
        {"url": "https://gis.townsd.gov/arcgis/rest/services/Town/MapServer/2"},
        {"url": "https://hazards.fema.gov/arcgis/rest/services/NFHL/MapServer/28"},
        {"url": "https://services5.arcgis.com/FallRiver1/arcgis/rest/services/Roads/FeatureServer/0"},
    ]}  # fmt: skip

    def answer(url, params):
        if url == arcgis_online.SEARCH:
            kind = params["q"].split('"')[1]
            found = items[kind]
            return (
                {"total": len(found)} if params["num"] == 1 else {"results": found, "nextStart": -1}
            )
        if url.endswith("/m1/data"):
            return definition
        if "/portals/" in url:
            return {"name": "Fall River County"}
        raise AssertionError(url)

    get = Fake(answer)
    leads, stats = arcgis_online.find(get)
    by_root = {lead.url: lead for lead in leads}
    assert set(by_root) == {"https://services5.arcgis.com/FallRiver1/arcgis/rest/services",
                            "https://gis.townsd.gov/arcgis/rest/services"}  # fmt: skip
    fall_river = by_root["https://services5.arcgis.com/FallRiver1/arcgis/rest/services"]
    assert fall_river.title == "Fall River County" and len(fall_river.evidence) == 2
    assert by_root["https://gis.townsd.gov/arcgis/rest/services"].title == "brosz"
    assert stats["skipped_hosts"] == {"hazards.fema.gov": 1} and stats["in_south_dakota"] == 2
    # the web map's definition is kept, so a second run doesn't ask again
    arcgis_online.find(get)
    assert sum(url.endswith("/m1/data") for url, _ in get.asked) == 1


def test_host_names_to_try():
    names = hosts.names_to_try("townsd.gov", {"gis2.townsd.gov"}, wildcard=False)
    assert names[0] == "gis2.townsd.gov" and "gis.townsd.gov" in names
    assert names[-2:] == ["www.townsd.gov", "townsd.gov"]
    assert hosts.names_to_try("any.gov", set(), wildcard=True) == ["www.any.gov", "any.gov"]
    assert hosts.base("WWW.Co.Pennington.SD.US") == "co.pennington.sd.us"


def test_host_finder(db):
    def answer(url, params):
        if url == hosts.CRT:
            return [{"name_value": "*.townsd.gov\nmaps.townsd.gov\nmail.townsd.gov"}]
        if url == "https://maps.townsd.gov/server/rest/services":
            return {"currentVersion": 11.1, "folders": ["Public"], "services": []}
        raise OSError("404")

    up = {"maps.townsd.gov", "www.townsd.gov", "townsd.gov"}
    leads, stats = hosts.find(Fake(answer), domains={"townsd.gov": "CISA .gov list: Town"},
                              resolve=up.__contains__)  # fmt: skip
    (lead,) = leads
    assert lead.url == "https://maps.townsd.gov/server/rest/services"
    assert lead.evidence[0]["host_from"] == "certificate log"
    assert stats["resolved"] == 3 and stats["from_certificates"] == 1


def test_cisa_domains():
    csv = ("Domain name,Domain type,Organization name,Suborganization name,City,State,"
           "Security contact email\nPIERRESD.GOV,City,City of Pierre,,Pierre,SD,(blank)\n"
           "29palms.gov,City,City of Twentynine Palms,,Twentynine Palms,CA,(blank)\n")  # fmt: skip
    assert hosts.cisa_domains(Fake(lambda url, params: csv)) == {
        "pierresd.gov": "CISA .gov list: City of Pierre"}  # fmt: skip


@pytest.fixture
def registered(db):
    return Source.objects.create(name="Sioux Falls", slug="sioux-falls", url=SIOUX_FALLS,
                                 platform=Source.Platform.ARCGIS_ONLINE)  # fmt: skip


def test_recording_candidates(registered):
    lead = finders.Lead("https://gis.townsd.gov/arcgis/rest/services", "ArcGIS Online",
                        title="Town", evidence=[{"item": "m1"}, {"item": "m2"}])  # fmt: skip
    known = finders.Lead(SIOUX_FALLS.replace("ArcGIS", "arcgis"), "ArcGIS Online")
    assert finders.record([lead, known]) == {"new": 1, "seen_again": 0, "already_a_source": 1}
    candidate = SourceCandidate.objects.get()
    assert candidate.n_evidence == 2 and candidate.platform == Source.Platform.ARCGIS_SERVER

    candidate.status = SourceCandidate.Status.REJECTED
    candidate.save()
    again = finders.Lead("http://gis.townsd.gov/arcgis/rest/services", "Government domains",
                         evidence=[{"host": "gis.townsd.gov"}])  # fmt: skip
    assert finders.record([again])["seen_again"] == 1
    candidate.refresh_from_db()
    assert candidate.status == "rejected"  # stays rejected
    assert candidate.found_via == "ArcGIS Online, Government domains"
    assert candidate.n_evidence == 3


def test_review_page_and_adding_a_candidate(client, registered, monkeypatch):
    candidate = SourceCandidate.objects.create(
        url="https://gis.townsd.gov/arcgis/rest/services", title="Town", found_via="hosts",
        evidence={"counts": {"hosts": 1}, "examples": {"hosts": [
            {"host": "gis.townsd.gov", "host_from": "usual host name",
             "domain_from": "CISA .gov list: Town", "listed": 4}]}})  # fmt: skip
    page = client.get("/gis/candidates/").content.decode()
    assert "gis.townsd.gov" in page and "usual host name" in page and "Run finders" in page
    assert "found_via=hosts" in page

    client.post(f"/gis/candidates/{candidate.pk}/review/", {"action": "reject"})
    candidate.refresh_from_db()
    assert candidate.status == "rejected"
    assert "gis.townsd.gov" not in client.get("/gis/candidates/").content.decode()
    client.post(f"/gis/candidates/{candidate.pk}/review/", {"action": "reopen"})
    candidate.refresh_from_db()
    assert candidate.status == "pending"

    # adding the server through the form marks the candidate accepted
    from census import views
    from census.sources import Discovery

    found = Discovery(url=candidate.url, platform=Source.Platform.ARCGIS_SERVER)
    form = {"name": "Town", "publisher": "", "jurisdiction": "", "found_via": "hosts"}
    errors, source = views.create_source(found, form, [])
    candidate.refresh_from_db()
    assert not errors and candidate.status == "accepted" and candidate.source == source


def test_running_finders_queues_a_job(client, db, monkeypatch):
    queued = []
    monkeypatch.setattr("core.jobs.enqueue", lambda job, task: queued.append(job) or job)
    client.post("/gis/candidates/find/", {"finder": ["hosts"]})
    (job,) = queued
    assert job.kind == "find" and job.args == {"finders": ["hosts"]}


def candidate(url, title="", owners=(), n=1):
    from census.models import SourceCandidate

    return SourceCandidate.objects.create(
        url=url, title=title, found_via="ArcGIS Online",
        evidence={"counts": {"ArcGIS Online": n},
                  "examples": {"ArcGIS Online": [{"item": "x", "title": "South Dakota roads",
                                                  "owner": o} for o in owners]}})  # fmt: skip


def test_candidates_sorted_by_publisher(db):
    from census import candidates as triage

    def kind(c):
        return triage.classify(c)[0]

    assert kind(candidate("https://gis.lincolncountysd.gov/arcgis/rest/services")) == (
        triage.SD_GOVERNMENT)  # fmt: skip
    assert kind(candidate("https://dotgis.sd.gov/rest/services")) == triage.SD_GOVERNMENT
    assert kind(candidate("https://gis.ne.gov/rest/services")) == triage.GOVERNMENT
    assert kind(candidate("https://services1.arcgis.com/a/arcgis/rest/services", "CityofTea")) == (
        triage.NAMED)  # fmt: skip
    assert kind(candidate("https://services1.arcgis.com/b/arcgis/rest/services", "x",
                          ["BIT.GIS.Dev"])) == triage.NAMED  # fmt: skip
    # an item's title naming South Dakota says nothing about who publishes it
    assert kind(candidate("https://services1.arcgis.com/c/arcgis/rest/services", "Esri",
                          ["mikel2"])) == triage.OTHER  # fmt: skip
    # "danr" inside a word isn't DANR
    assert kind(candidate("https://services1.arcgis.com/d/arcgis/rest/services",
                          "USDA NRCS ArcGIS Online")) == triage.OTHER  # fmt: skip


def test_candidate_pages(client, db, monkeypatch):
    c = candidate("https://gis.example.gov/arcgis/rest/services", "Example", ["owner1"], n=3)
    other = candidate("https://services1.arcgis.com/z/arcgis/rest/services", "Someone")
    page = client.get("/gis/candidates/").content.decode()
    assert page.index("Example") < page.index("Someone")  # governments first
    c.scan = {"services": [{"name": "Roads", "type": "MapServer", "url": "u", "folder": "",
                            "error": ""}],
              "layers": [{"url": f"{c.url}/Roads/MapServer/0", "service": "Roads",
                          "service_type": "MapServer", "folder": "", "name": "Centerlines",
                          "kind": "layer", "geometry_type": "Polyline", "count": 5619,
                          "description": "", "fields": [{"name": "NAME", "type":
                                                         "esriFieldTypeString", "alias": ""}],
                          "edited_at": None, "error": ""}],
              "folder_errors": {}, "timed_out": False, "features": 5619}  # fmt: skip
    c.scanned_at = c.created_at
    c.save()
    detail = client.get(f"/gis/candidates/{c.pk}/").content.decode()
    assert "Centerlines" in detail and "5,619" in detail and "Map a sample" in detail
    assert "?f=json" not in detail  # the directory links to its own pages
    # a sample only from a layer in this directory
    bad = client.get(f"/gis/candidates/{c.pk}/sample/?url=https://elsewhere.example/x/MapServer/0")
    assert bad.status_code == 400
    client.post("/gis/candidates/reject/", {"pk": [other.pk], "notes": "a student's maps"})
    other.refresh_from_db()
    assert other.status == "rejected" and other.notes == "a student's maps"


def test_scanning_a_candidate(db, monkeypatch):
    from census import candidates as triage
    from sdgis.arcgis import directory

    c = candidate("https://gis.example.gov/arcgis/rest/services")
    found = directory.DirectoryScan(root=c.url, services=[directory.ServiceInfo(
        name="Roads", type="MapServer", url=f"{c.url}/Roads/MapServer")],
        layers=[directory.LayerInfo(url=f"{c.url}/Roads/MapServer/0", service="Roads",
                                    service_type="MapServer", name="Centerlines",
                                    geometry_type="esriGeometryPolyline", count=12)])  # fmt: skip
    monkeypatch.setattr(directory, "scan", lambda *a, **k: found)
    summary = triage.scan(c)
    assert summary["features"] == 12 and summary["layers"][0]["geometry_type"] == "Polyline"


def test_recency_moves_candidates_within_their_group(client, db):
    from datetime import date

    from census import candidates as triage
    from census.finders import note_latest

    assert triage.recency("2026-03-01", today=date(2026, 11, 16)) == triage.CURRENT
    assert triage.recency("2024-03-01", today=date(2026, 11, 16)) == triage.AGING
    assert triage.recency("2019-03-01", today=date(2026, 11, 16)) == triage.IDLE
    assert triage.recency(None) == triage.UNDATED

    stale = candidate("https://services1.arcgis.com/s/arcgis/rest/services", "City of Brandon",
                      n=50)  # fmt: skip
    fresh = candidate("https://services1.arcgis.com/f/arcgis/rest/services", "City of Tea", n=2)
    note_latest(stale, [{"item": "a", "modified": "2015-05-01", "title": "Old"}])
    note_latest(fresh, [{"item": "b", "modified": "2019-01-01"},
                        {"item": "c", "modified": f"{date.today().year}-01-02"}])  # fmt: skip
    note_latest(fresh, [{"item": "d", "modified": "2020-01-01"}])  # an older one doesn't win
    stale.save()
    fresh.save()
    assert fresh.evidence["latest"]["item"] == "c"
    page = client.get("/gis/candidates/").content.decode()
    # both name a South Dakota place: the one updated this year first, despite fewer items
    assert page.index("City of Tea") < page.index("City of Brandon")
    by_items = client.get("/gis/candidates/?order=items").content.decode()
    assert by_items.index("City of Brandon") < by_items.index("City of Tea")

    # once scanned, the layers' own last edit is the date
    stale.scan = {"layers": [{"edited_at": "2026-10-01T00:00:00"}]}
    assert triage.last_updated(stale) == ("2026-10-01", "data last edited")
