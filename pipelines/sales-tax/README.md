# sales-tax

Municipal sales tax in South Dakota: each city's taxable sales and tax due by month, tax and
SIC industry, from the Department of Revenue's
[historical sales tax statistical reports](https://dor.sd.gov/businesses/taxes/sales-use-tax/historical-tax-statistical-reports/)
("Municipal Taxable Sales per City by SIC Code"). An **aggregate** dataset in the catalog
(`municipal-sales-tax`): the unit is a city × month × tax × industry.

```sh
just sales-tax                                     # both steps
uv run python pipelines/sales-tax/fetch.py         # the workbooks → .cache/sales-tax/raw/
uv run python pipelines/sales-tax/build.py         # tables + checks → .cache/sales-tax/build/
```

## What the Department publishes

A report for each month of returns filed: a PDF from January 2020, and an Excel workbook from
January 2022 (this pipeline reads the workbooks). For each city, and a few other taxing
entities (the DENR water projects, "Z-Various"), it has a section for each municipal tax
(General, and Gross Receipts: the municipal gross receipts tax). Each section gives taxable
sales and tax due by SIC division, major group and four-digit industry, and the total of
each. A recap section gives the same by division, and the city's recap gives its tax due by
division for all its taxes.

The tax due is the city tax reported for that city by every seller, including sellers
elsewhere (in or out of state) that deliver into the city. Where fewer than three licenses
report in a classification, the amounts are "N/A". Industries too few to list are
grouped as "Other" in their major group, and divisions too few to list as "ZZ Other" in
the recap.

## The tables (`build.py`)

- `city_sic` (Parquet): every line of every month's report, one row per month × city × tax ×
  line, with its `level`:
  - `industry`, `other`
  - `major group`, `division`
  - `recap division`, `tax total`
  - `city recap division`, `city total` (these two give tax due only)

  Each row has the division, major group and SIC code it belongs to, the taxable sales and
  tax due, and `suppressed` for "N/A" (the amounts are left empty). Sum one level; the
  levels overlap.
- `city_taxes` (Parquet and CSV): each city's taxable sales and tax due by month for each tax,
  and its tax due for all taxes (`tax` = `all`).

Each row has the city's GEOID where exactly one South Dakota city or town has its name, and
names its file, its row there and the file's URL.

## Checks and caveats

`checks.json` lists every finding.

- **Every level adds up**, in every month, wherever nothing in the sum is suppressed. A
  major group's lines add up to its total, a division's major groups to its total, a tax's
  recap divisions to the tax's total, and a city's taxes and its recap's divisions to its
  total tax due.
- **Totals include suppressed amounts.** A major group's total is more than its listed
  lines when one is "N/A". The suppressed amounts aren't worked out from the totals.
- **Places:** Witten, Dewey, Buffalo Chip, "Sioux Falls - L", "Z-Various" and the DENR water
  projects have no city GEOID.
- **The month is the month returns were filed**, from the file name (and the link on the
  page). Some months appear under two years on the page, with the same file.
- **The March 2024 workbook** repeats a column heading under its header. It's skipped.
- Not read yet: the PDFs for 2020 and 2021, and the page's other reports (tourism tax by
  county, state taxable sales by city, contractors' excise tax, the comparisons).
