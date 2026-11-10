# sdgis

Plain-Python clients for the platforms South Dakota GIS data is published on. No Django, so any project can use it: the sd-archive app, release repos, scripts, notebooks.

- **`sdgis.arcgis.client`**: one ArcGIS REST layer. Metadata, counts, object IDs, coded-value labels, last edit date, and `fetch_features()`, which downloads by object-ID range (with or without server paging), splits batches the server truncates, and returns a GeoDataFrame.
- **`sdgis.arcgis.directory`**: crawls a services directory (folders, MapServer and FeatureServer services) into a `DirectoryScan` of queryable layers and tables. It has timeouts, a time limit and per-host concurrency, and records stopped services as errors.
- **`sdgis.beacon`**: Schneider Beacon. Parses Beacon's client picker into the list of county and city apps, and builds their URLs. It makes no requests: Beacon sits behind a Cloudflare challenge.
- **`sdgis.testing`**: fake ArcGIS servers (`FakeServer`, `FakeDirectory`) and a saved Beacon picker, for tests in any project. Patch `sdgis.arcgis.client.make_session` to return a fake.

Site-specific notes (paging tricks, shared regional servers, secured folders) are in the `sd-scraping-playbook` skill in `~/civic-tech/.claude/skills/`.

## Use from another repo

```toml
[tool.uv.sources]
sdgis = { git = "https://github.com/south-dakota-citizen-archivist/sd-archive", subdirectory = "packages/sdgis", tag = "sdgis-v0.1.0" }
```
