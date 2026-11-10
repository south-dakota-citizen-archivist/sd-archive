"""The geocoder, with the network faked. The OpenStreetMap test is whats-up-in-spearfish's
(tests/test_permits.py), adapted to the Geocoder class."""

from __future__ import annotations

import json
from datetime import date

import pytest

pytest.importorskip("usaddress")

from sdplaces import geocode  # noqa: E402
from sdplaces.geocode import Box, Geocoder, JsonCache  # noqa: E402

SPEARFISH = Box(44.40, 44.60, -104.05, -103.70)


class Resp:
    def __init__(self, body=None, text=""):
        self.body, self.text = body, text

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_box():
    assert SPEARFISH(44.49, -103.86)
    assert not SPEARFISH(53.34, 4.74)
    assert not SPEARFISH(None, None)
    assert geocode.SOUTH_DAKOTA(44.37, -100.35)  # Pierre


def test_cache_keys_bare_for_one_city_and_with_the_city_statewide():
    assert (
        Geocoder(city="Spearfish").key("2312 Platinum Drive, Spearfish, SD 57783")
        == "2312 PLATINUM DR"
    )
    statewide = Geocoder()
    assert statewide.key("100 Main St, Lead, SD") == "100 MAIN ST, LEAD"
    assert statewide.key("100 Main St, Huron, SD") == "100 MAIN ST, HURON"
    assert statewide.key("Main Street") == ""


def test_census_batch_keeps_matches_inside_the_area_and_caches_misses(tmp_path):
    asked = []

    class Session:
        def post(self, url, files=None, data=None, timeout=None):
            rows = list(
                __import__("csv").reader(__import__("io").StringIO(files["addressFile"][1]))
            )
            asked.extend(rows)
            out = []
            for i, street, *_ in rows:
                if street == "2312 PLATINUM DR":
                    out.append(f'"{i}","{street}","Match","Exact","x","-103.8169,44.4789"')
                elif street == "1 FAR AWAY RD":  # matched, but nowhere near Spearfish
                    out.append(f'"{i}","{street}","Match","Exact","x","-100.0,43.0"')
                else:
                    out.append(f'"{i}","{street}","No_Match"')
            return Resp(text="\n".join(out))

    cache = JsonCache(tmp_path / "_geocode_cache.json")
    g = Geocoder(city="Spearfish", zip="57783", within=SPEARFISH, cache=cache, session=Session())
    got = g.batch(["2312 Platinum Drive", "1 Far Away Rd", "9 Nowhere Ln", "Main Street"])
    assert got == {
        "2312 PLATINUM DR": [44.4789, -103.8169],
        "1 FAR AWAY RD": None,
        "9 NOWHERE LN": None,
    }
    assert asked[0][1:] == ["1 FAR AWAY RD", "Spearfish", "SD", "57783"]
    assert json.loads((tmp_path / "_geocode_cache.json").read_text())["9 NOWHERE LN"] is None

    asked.clear()
    g.batch(["2312 Platinum Drive", "9 Nowhere Ln"])
    assert asked == []  # everything already cached, misses included


def test_locate_streets_caches_answers_and_retries_misses_later(tmp_path, monkeypatch):
    monkeypatch.setattr(geocode, "NOMINATIM_DELAY", 0)
    asked = []
    answers = {
        "2312 PLATINUM DR": [
            {"lat": "44.4789", "lon": "-103.8169", "addresstype": "road", "address": {}}
        ],
        "10 MAIN ST": [
            {
                "lat": "44.49",
                "lon": "-103.86",
                "addresstype": "building",
                "address": {"house_number": "10"},
            }
        ],
        "1 FAR AWAY RD": [
            {"lat": "43.0", "lon": "-100.0", "addresstype": "road", "address": {}}
        ],  # not Spearfish
    }

    class Session:
        def get(self, url, params=None, **kw):
            key = params["q"].split(",")[0]
            asked.append(key)
            assert params["q"].endswith(", Spearfish, SD")
            return Resp(answers.get(key, []))

    osm = JsonCache(tmp_path / "_geocode_osm.json")
    g = Geocoder(city="Spearfish", within=SPEARFISH, osm_cache=osm, session=Session())
    found = g.locate_streets(
        ["2312 PLATINUM DR", "10 MAIN ST", "1 FAR AWAY RD"], today=date(2026, 11, 4)
    )
    assert found == {
        "2312 PLATINUM DR": [44.4789, -103.8169, "street"],
        "10 MAIN ST": [44.49, -103.86, "house"],
    }
    assert json.loads((tmp_path / "_geocode_osm.json").read_text())["1 FAR AWAY RD"] == {
        "miss": "2026-11-04"
    }

    asked.clear()
    g.locate_streets(["2312 PLATINUM DR", "1 FAR AWAY RD"], today=date(2026, 11, 20))
    assert asked == []  # answered, or missed too recently to ask again
    g.locate_streets(["1 FAR AWAY RD"], today=date(2026, 12, 20))
    assert asked == ["1 FAR AWAY RD"]  # a month on, a miss is tried again


def test_reads_spearfish_cache_files_as_they_are(tmp_path):
    (tmp_path / "_geocode_cache.json").write_text(
        '{"2312 PLATINUM DR":[44.4789,-103.8169],"9 NOWHERE LN":null}'
    )

    class NoNetwork:
        def post(self, *a, **kw):
            raise AssertionError("cached keys must not be looked up")

    g = Geocoder(
        city="Spearfish", cache=JsonCache(tmp_path / "_geocode_cache.json"), session=NoNetwork()
    )
    assert g.batch(["2312 Platinum Drive, Spearfish, SD 57783", "9 Nowhere Ln"]) == {
        "2312 PLATINUM DR": [44.4789, -103.8169],
        "9 NOWHERE LN": None,
    }
