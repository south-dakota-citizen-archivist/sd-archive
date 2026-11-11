# motor-vehicles

South Dakota motor vehicle titles and registrations by county, from the Department of
Revenue's [title and registration statistics](https://sddor.seamlessdocs.com/sc/statistics/).
An **aggregate** dataset in the catalog (`motor-vehicle-registrations`): the unit is area
(the state or a county) × period × measure.

```sh
just motor-vehicles                                     # both steps
uv run python pipelines/motor-vehicles/fetch.py         # the reports → .cache/motor-vehicles/raw/
uv run python pipelines/motor-vehicles/build.py         # tables + checks → .cache/motor-vehicles/build/
```

## What the Department publishes

- **A report for each calendar year since 2018** (PDF), a page for the state and each of the
  66 counties, with a line for each month and a Total. The current year's report fills in
  month by month; months not reported yet show as zeros.
- **A historical report** (PDF) with each county's annual totals, currently 2015–2025.
- **Fifteen measures**: registrations of passenger/low-speed vehicles, pickups/SUVs/vans,
  trucks, buses, trailers, motorcycles, off-road vehicles, recreational vehicles,
  snowmobiles, boats, commercial plates, gross vehicle weight, IRP with prorate plates and
  electric vehicles, and titles. The report's second page defines each, with the statutes.

fetch.py keeps each report; a closed year's report is fetched once, the historical report
and this year's and last year's are fetched again each run.

## The tables (`build.py`)

- `annual`: one row per area × year × measure, with the area's Census GEOID (via
  `sdplaces`). Years in the historical report come from it; later years from their
  calendar-year report's Total, with `year_to_date` and `through_month` while the year is
  still being reported.
- `monthly`: one row per area × month × measure, from the calendar-year reports (2018 on),
  without the months not reported yet.

Every row names its report, the report's URL and the page.

## Checks and caveats

- **Each report's months add up to its Total**, for every area and measure.
- **The counties don't add up to the state total** for several measures, every year: the
  state total includes registrations no county is credited with. The gap is largest for
  trucks (2025: 46,932 across counties against 57,934 statewide) and IRP prorate plates,
  which suggests interstate fleets registered with the state. Both are kept as published;
  `checks.json` lists every gap.
- **The historical report revises earlier years.** For 2018–2022 its annual totals differ
  slightly from the calendar-year reports' Total lines (661 values; 302 of them in 2022,
  mostly gross vehicle weight). The annual table uses the historical report; `checks.json`
  lists every difference.
- **Oglala Lakota County** is 46102 throughout (every year here is after Shannon County was
  renamed in 2015).
- The column headings are checked on every page, so a change to the reports' layout stops
  the build instead of shifting numbers between columns.
