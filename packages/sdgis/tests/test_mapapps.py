from sdgis.mapapps import BASE_URL
from sdgis.mapapps.clients import app_url, parse_apps, place_name
from sdgis.testing import PICKER


def test_parse_apps_skips_recently_used_and_labels():
    clients = parse_apps(PICKER)
    assert [(c.app_id, c.key, c.state) for c in clients] == [
        (334, "CityofFoleyAL", "Alabama"),
        (1225, "TestCountySD", "South Dakota"),
        (95, "McCookCountySD", "South Dakota"),
    ]
    assert clients[1].name == "Test County, SD"


def test_parse_apps_by_state():
    assert [c.app_id for c in parse_apps(PICKER, state="South Dakota")] == [1225, 95]


def test_place_name_and_urls():
    foley, test, _ = parse_apps(PICKER)
    assert place_name(test) == "Test County"
    assert place_name(foley) == "Foley"
    assert (
        test.url()
        == app_url("TestCountySD")
        == f"{BASE_URL}/Application.aspx?App=TestCountySD&PageType=Map"
    )
