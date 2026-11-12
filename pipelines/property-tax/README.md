# property-tax

South Dakota property tax valuations, levies and taxes by taxing district, from the
Department of Revenue's [property tax data page](https://sdproptax.info/DataLink/Data). An
**aggregate** dataset in the catalog (`property-tax-levies`): the unit is a taxing district
(county, city or town, township, school district, road district or another special district)
× year × fund × property class. The same page's sales files are a record set,
`property-sales`. It also reads the Department's annual
[Property Tax Statistical Reports](https://sddor.seamlessdocs.com/sc/statistics/) (PDF,
2018 on).

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
| Property Tax Statistical Report (PDF) | 2018 on | county, school district and municipality tables (below) |

**Years.** Taxes are assessed in one year and payable the next. Every table has both
`assessment_year` and `payable_year`. The files don't agree on which one they give:
- the older recap layouts (the 2021 and 2022 city, road, township and school recaps, and
  the county recaps) give the payable year in their file names
- the state system's files (2023 on, and Other Recap-2022) give the assessment year
  (`TaxYear`), as does PT-74

This was checked against the statistical reports, which give both. For example, School
Recap-2022 is the 2021 assessment (taxes payable 2022), and School Recap-2023 is the 2023
assessment (payable 2024). **So no Excel recap but Other Recap-2022 covers the 2022
assessment (taxes payable 2023).** The 2023 statistical report does.

The recaps change layout from year to year (nine layouts across the files). `parse.py`
recognizes each by its header row and stops on one it doesn't know.

Property classes are agricultural, owner-occupied, other, utility, mobile home and
owner-occupied mobile home. The 2023–2024 school recaps spell them out; the other files use
codes (`AG`, `OO`, `OT`, `UT`, `M`, `M-OO`, or `ag`, `oo`, …). `class_as_published` keeps
the code.

## The tables (`build.py`)

- `levies`: one row per year × district × fund × class, as far as each recap breaks them
  down. `fund` is empty where a file gives no funds, and `property_class` is empty where it
  gives no classes. Each row has its valuation, its levy (dollars per $1,000 of valuation) and
  its tax. `adds_up` says whether the tax is the valuation × the levy, allowing for the levy's
  rounding. Each row also has the county's GEOID, and the district's own GEOID for cities,
  towns and school districts when exactly one place has that name (a school district is
  matched by its DOE number where the file gives one). The district, fund and class IDs from
  the 2023-on files are kept.
- `county_recap`: the county recap as one row per year × county × field, with the field
  named as published (`AG_OUT_VAL`, `GEN_LEVY`, …), plus a `STATE SUMMARY` row. Fields that
  give a class's valuation or tax are also split into `property_class`,
  `inside_corporate_limits` and `measure`. `IN` and `OUT` are inside and outside corporate
  (city) limits: the statistical reports print the same figures under those labels.
- `pt74`: one row per year × district fund × taxing entity, with the four published rates
  and six published valuations.

From the statistical reports (each row names its report file and page):

- `report_schools`: each school district's part of a county, with valuations (ag,
  owner-occupied, other non-ag, centrally assessed), the ag, owner-occupied and other levies
  **for all funds**, and the taxes. It covers payable 2018 and 2022–2024, and fills the 2023
  gap and the funds the 2023–2024 school recaps leave out.
- `report_municipalities`: each city or town (its part of each county it's in), with
  population, valuation, the city, county and school levies, total tax rates by class and
  taxes paid. It covers payable 2022–2024.
- `report_county_recaps`: each county's recap lines with a valuation and a tax (and a levy
  where given), for payable 2018 and 2022–2024.
- `report_sales_ratios`: each county's assessment-to-sales ratio (percent; ag and non-ag),
  and the sales years it's based on, for every report 2018–2024.
- `report_ag_land`: each county's ag valuation, acres and assessed dollars per acre, for
  assessments 2018–2023.

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
- **The statistical reports aren't all text.** The 2019–2021 reports' district tables
  (schools, municipalities, townships, cities, county recaps) are scanned, and so are 18
  pages of the 2018 report. Some 2024 tables (effective tax rates, ag dollars per acre) are
  pictures. These aren't read yet (`checks.json` lists every page without text), nor are
  the reports' township, city, valuation and effective-rate tables.
- **Report lines not read:** a county recap line with one amount (a valuation or a tax, but
  the text doesn't say which) isn't read. Ten table rows are listed in `checks.json`: six 2018
  school rows with only three of four valuations, and four 2022 municipality rows with no
  population.
- **Report school taxes are a little above valuation × levies** (779 rows; the median is
  0.7% above, and 95% are within 4%). The report notes that bond levies aren't always
  included in the levies it gives, since districts overlap.
- **The reports against the Excel files**, for the same payable year: the school tables match
  in ag valuation for 325 of 340 rows (2022) and 213 of 339 (2024). The county recaps match
  for 64 of 67 counties (2018) and 65 of 67 (2022).
- The county recap's `STATE SUMMARY` row differs from the counties' sum for a few fields,
  mostly by a few dollars of rounding, and once by exactly 1,000.
