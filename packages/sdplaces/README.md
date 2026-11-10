# sdplaces

South Dakota places, past and present, keyed by Census GEOID, plus the ID systems agencies use for them and statewide address normalization and geocoding. Plain Python, no Django.

```python
import sdplaces

sdplaces.county("Lawrence")  # Place(kind='county', geoid='46081', name='Lawrence County', …)
sdplaces.county("LAWRENCE COUNTY")  # the same: case, " County" and punctuation don't matter
sdplaces.county("Shannon")  # Place(geoid='46113', valid_to=2015-05-01, successor='46102')
sdplaces.county("Shannon", current=True)  # Oglala Lakota County, 46102
sdplaces.place("Spearfish")  # the city
sdplaces.get("46081")  # any place by GEOID
sdplaces.counties(as_of="2010-01-01")  # the 66 counties in force on that date
sdplaces.children("46081", kind="place")  # cities and towns in Lawrence County
sdplaces.from_id("sos_election_county", 57, as_of="2024-11-05")  # an agency's code → Place
sdplaces.from_id("doe_district", "06001")  # Aberdeen 06-1 school district

df = sdplaces.add_geoid(df, name_column="county", kind="county", date_column="date")
```

## What's in it

- **Places** (`data/places.csv`): the state, counties (including Shannon, Washabaugh and other former counties, with the dates they were in force and their successors), incorporated cities and towns, Census designated places, tribal areas, school districts, and state senate and house districts. Each has its GEOID, name, short name, slug, parent, Open Civic Data ID and validity dates.
- **Aliases** (`data/aliases.csv`): spellings records use ("Mc Cook", "BonHomme", "Fall River Co"), each with where it was seen.
- **Crosswalks** (`data/crosswalks/`): agency codes mapped to GEOIDs, with the evidence for each row, and dates where a code's meaning changed. `sos_election_county` holds the election system's county codes. `doe_district` holds the Department of Education's district numbers.

**Names are matched exactly, never guessed.** A name that isn't a place or a known alias raises `UnknownPlace`, with suggestions for the nearest names. A code that meant different places at different times raises `AmbiguousPlace` unless `as_of=` is given.

## Boundaries (`geo` extra)

```python
sdplaces.boundaries("county")  # GeoDataFrame of current county boundaries, EPSG:4326
sdplaces.boundaries("county", 2014)  # an older year: includes Shannon County
```

Shapes come from the Census cartographic boundary files. Each file is downloaded once into `$SDPLACES_CACHE` (default `~/.cache/sdplaces`), and its rows are filtered to sdplaces' own places.

## Addresses and geocoding (`address`, `geocode` extras)

```python
from sdplaces import address, geocode

address.parse("2545 & 2555 CLEAR SPRING RD")  # two Address objects
address.key("515 27th St N, Spearfish, SD")  # "515 N 27TH ST"
address.loose("1440 N MAIN ST")  # "1440 MAIN ST", for when directions disagree

geocode.Geocoder(city="Spearfish", cache=geocode.JsonCache(path)).batch(addresses)
```

- `address` normalizes addresses to a key. It handles ranges and lists of numbers, units, highway and county-road names, trailing town names and coordinates, and rejects descriptions that aren't addresses ("Lot 1 Block 9…").
- `geocode` tries the Census batch geocoder first, then OpenStreetMap's Nominatim at one request a second. It rejects results outside an area you give it, caches answers, and retries misses after 30 days.

## Rebuilding the data

`scripts/build_data.py` regenerates `places.csv` from the Census Gazetteer and boundary files, and Open Civic Data IDs from the `ocd-division-ids` repository. Former counties, aliases and crosswalks are edited by hand, each row with its source. The tests check:
- the 66 current counties
- unique GEOIDs
- that every alias and crosswalk row points at a real place
- the known quirks, such as Shannon/Oglala Lakota by date and Washabaugh → Jackson

## Use from another repo

```toml
[tool.uv.sources]
sdplaces = { git = "https://github.com/south-dakota-citizen-archivist/sd-archive", subdirectory = "packages/sdplaces", tag = "sdplaces-vX.Y.Z" }
```
