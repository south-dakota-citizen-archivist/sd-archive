# property-tax

South Dakota property tax valuations, levies and taxes by taxing district, from the
Department of Revenue's [property tax data page](https://sdproptax.info/DataLink/Data). An
**aggregate** dataset in the catalog (`property-tax-levies`): the unit is a taxing district
(county, city or town, township, school district, road district or another special district)
× tax year × fund × property class. The same page's sales files are a record set,
`property-sales`.

```sh
just property-tax                                     # both steps
uv run python pipelines/property-tax/fetch.py         # the files → .cache/property-tax/raw/
uv run python pipelines/property-tax/build.py         # tables + checks → .cache/property-tax/build/
```

## What the Department publishes

| File | Years | What a row is |
|---|---|---|
| County Recap (Excel) | 2018–2022 | a county: valuations and taxes by property class, and each county levy |
| City/Town, Township, Road District, Other District Recap (Excel) | 2021 on (Other: 2022 on) | a district (and, from 2023, a fund) |
| School District Recap (Excel) | 2021 on | a school district's part of a county, by property class (and, in 2025, fund) |
| PT-74 (CSV) | 2023 on | a district fund × taxing entity (an area taxed by one combination of districts): rates and valuations by class |

The recaps change layout from year to year (nine layouts across the files). `parse.py`
recognizes each by its header row and stops on one it doesn't know.

Property classes are agricultural, owner-occupied, other, utility, mobile home and
owner-occupied mobile home. The 2023–2024 school recaps spell them out; the other files use
codes (`AG`, `OO`, `OT`, `UT`, `M`, `M-OO`, or `ag`, `oo`, …). `class_as_published` keeps
the code.

## The tables (`build.py`)

- `levies`: one row per tax year × district × fund × class, as far as each recap breaks them
  down. `fund` is empty where a file gives no funds, and `property_class` is empty where it
  gives no classes. Each row has its valuation, its levy (dollars per $1,000 of valuation) and
  its tax. `adds_up` says whether the tax is the valuation × the levy, allowing for the levy's
  rounding. Each row also has the county's GEOID, and the district's own GEOID for cities,
  towns and school districts when exactly one place has that name (a school district is
  matched by its DOE number where the file gives one). The district, fund and class IDs from
  the 2023-on files are kept.
- `county_recap`: the county recap as one row per tax year × county × field, with the field
  named as published (`AG_OUT_VAL`, `GEN_LEVY`, …), plus a `STATE SUMMARY` row. Fields that
  give a class's valuation or tax are also split into `property_class`, `in_out` and `measure`.
  The files don't say what `IN` and `OUT` mean, so `in_out` is kept as published.
- `pt74`: one row per tax year × district fund × taxing entity, with the four published rates
  and six published valuations.

Every row names its file, its row in that file and the file's URL.

## Checks and caveats

`checks.json` lists every finding:

- **The 2021 school recap's `OO_Levy` and `Oth_Levy` headings are the wrong way round.**
  Every row's `Tax` adds up only with the owner-occupied classes at the levy headed
  `Oth_Levy` and the other classes at the levy headed `OO_Levy`. The headed `OO_Levy` is the
  higher of the two in every row, the reverse of every other year. `levies` assigns them the
  way that adds up. That file gives only a total tax, so its per-class taxes are computed
  (`tax_computed`) and add up to that total in every row.
- **The 2023–2024 school recaps aren't broken down by fund, and don't cover every fund.** Their
  school taxes total about $420 million a year, against $800 million to $1 billion in the
  other years. That is close to PT-74's General fund alone. Most of their levies match a
  district's General fund levy in PT-74, but not all, so `fund` is left empty. Don't compare
  their totals with other years'.
- **Tax isn't always valuation × levy** (2,649 rows; `adds_up` is false). Of these, 1,440 have
  tax but a valuation of 0 (most in the 2023–2024 school and township recaps), and 435 have
  tax but a levy of 0 (most of them other districts). The rest are off by more than rounding.
- **2022 township recaps:** the funds' levies don't add up to the total levy in 189 rows.
- **PT-74 repeats rows with a different county ID** (2746 instead of the county's own). The
  rows are otherwise identical: 2,281 across the three files. `pt74` leaves the repeats out,
  so they aren't counted twice, and `checks.json` lists each one.
- **PT-74 against the recaps:** of the 6,271 recap rows with a fund (2023 on), 5,239 have a
  district fund of the same name in PT-74. Summed by district fund, PT-74's valuation equals
  the recap's in 4,543, and its tax is within half a percent in 5,016. Other districts agree
  least (about half). Some funds appear in PT-74 with no valuations. In the 2025 school recap, Plankinton's mobile home and other valuations are
  the reverse of PT-74's (the tax is the same, since both classes pay the same levy).
- **Places:** "LINCON" (the 2021 and 2022 city recaps) is read as Lincoln County. A city's
  rural service district ("Elkton RSD") isn't the city, so it gets no place GEOID. One county
  recap cell has the county's name twice ("MARSHALL" and "Marshall" on two lines); it is read
  as one.
- The county recap's `STATE SUMMARY` row differs from the counties' sum for a few fields,
  mostly by a few dollars of rounding, and once by exactly 1,000.
