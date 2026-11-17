# population

South Dakota's population by county and year: the US Census Bureau's July 1 estimates
(Population Estimates Program), 2010 on. An **aggregate** dataset in the catalog
(`population-estimates`): the unit is the state or a county × year. Pages use it for rates
per resident.

```sh
CENSUS_API_KEY=… uv run python pipelines/population/fetch.py   # → data/population.csv
```

`data/population.csv` is committed, so nothing needs the API (or a key) to use it. Rerun the
fetch when a newer vintage of the estimates reaches the API.

- **2010–2019** come from the vintage 2019 estimates, **2020 on** from the latest vintage in
  the API (2023), which revises those years. Each row names its vintage and the API query.
- **The counties add up to the state** in every year; the fetch stops if they don't.
- **Oglala Lakota County** is 46102 in every year (the estimates use today's codes).
