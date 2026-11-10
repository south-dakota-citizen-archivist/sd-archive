from platforms.beacon.clients import app_url, parse_clients, place_name

PICKER = """
<div id="areaMenuContent" role="listbox">
<div class="state-group" role="group">
  <div id="mru-group" class="state-group-label faves">Recently used</div>
  <div id="mru1225TestCountySD" class="faves" data-appid="1225">Test County,  SD</div>
</div>
<div class="state-group" data-state="Alabama" role="group">
  <div id="Alabama-label" class="state-group-label">Alabama</div>
  <div id="client334CityofFoleyAL" class="dropdown-option" data-appid="334">City of Foley, AL</div>
</div>
<div class="state-group" data-state="South Dakota" role="group">
  <div id="SouthDakota-label" class="state-group-label">South Dakota</div>
  <div id="client1225TestCountySD" class="dropdown-option" data-appid="1225">Test County,  SD</div>
  <div id="client95McCookCountySD" class="dropdown-option" data-appid="95">McCook County, SD</div>
</div>
</div>
"""


def test_parse_clients_skips_recently_used_and_labels():
    clients = parse_clients(PICKER)
    assert [(c.app_id, c.key, c.state) for c in clients] == [
        (334, "CityofFoleyAL", "Alabama"),
        (1225, "TestCountySD", "South Dakota"),
        (95, "McCookCountySD", "South Dakota"),
    ]
    assert clients[1].name == "Test County, SD"


def test_parse_clients_by_state():
    assert [c.app_id for c in parse_clients(PICKER, state="South Dakota")] == [1225, 95]


def test_place_name_and_urls():
    foley, test, _ = parse_clients(PICKER)
    assert place_name(test) == "Test County"
    assert place_name(foley) == "Foley"
    assert (
        test.url()
        == app_url("TestCountySD")
        == ("https://beacon.schneidercorp.com/Application.aspx?App=TestCountySD&PageType=Map")
    )
