# sales-tax

Sales tax in South Dakota by city and county, from the Department of Revenue's
[historical sales tax statistical reports](https://dor.sd.gov/businesses/taxes/sales-use-tax/historical-tax-statistical-reports/):

- **Municipal tax by city, month, tax and SIC industry** (Municipal Taxable Sales per City by
  SIC Code), and each city's tax due by month (Municipal Tax Due per City). In the catalog as
  `municipal-sales-tax`.
- **Tourism tax by county, month and SIC industry** (Tourism Tax Report). In the catalog as
  `tourism-tax`.

Both are **aggregate** datasets: the unit is a place × month (× tax × industry).

```sh
just sales-tax                                     # both steps
uv run python pipelines/sales-tax/fetch.py         # the workbooks → .cache/sales-tax/raw/
uv run python pipelines/sales-tax/build.py         # tables + checks → .cache/sales-tax/build/
```

## What the Department publishes

Each report comes out monthly (the month returns were filed), with fiscal- and calendar-year
summaries that aren't read here.

**Municipal tax by city and SIC code.** A PDF from January 2020, and an Excel workbook from
January 2022. This pipeline reads the workbooks. For each city, and a few other taxing
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

**Municipal tax due per city** (PDF, January 2020 on). Each city's tax due for the month, both
taxes together, and for the same month a year before, with the percent change. The ten
largest cities come first, in a table repeated at the top of each page; the rest follow
alphabetically.

**Tourism tax by county** (PDF, January 2020 on). The 1.5% tourism tax: each county's taxable
sales and tax due by industry (lodging, campgrounds, car rental, amusements and more), with
"Other" for industries too few to list, and a state total that includes them. "Out of State"
is listed like a county. The figures are by the seller's location ZIP code; they aren't city
collections. November 2021 isn't on the page.

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
  and its tax due for all taxes (`tax` = `all`), from the workbooks.
- `city_tax_due` (Parquet and CSV): each city's tax due by month from January 2020, and the
  year before's as that report gives it, with the percent change and the list the city is in
  (`largest` or `all`).
- `tourism` (Parquet and CSV): each county's tourism taxable sales and tax due by month and
  industry (`industry`, `other`), and the state's total (`state total`), with the county's
  GEOID.

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
- **The reports agree with each other.** Each city's tax due in the tax-due report equals its
  total in the SIC workbook for every month both cover (14,465 cities and months), and every
  "year before" figure equals that month's own report (17,438), so no figure has been
  revised since.
- **Tourism:** every line's tax due is 1.5% of its taxable sales (refunds are negative), and
  no month's counties add up to more than the state total.
- **Places:** Witten, Dewey, Buffalo Chip, "Sioux Falls - L", "Z-Various" and the DENR water
  projects have no city GEOID.
- **The month is the month returns were filed**, from the file name (and the link on the
  page). Some months appear under two years on the page, with the same file.
- **The March 2024 workbook** repeats a column heading under its header. It's skipped.
- Not read yet: the SIC workbooks' PDFs for 2020 and 2021, and the page's other reports (state
  taxable sales by city, contractors' excise tax, the remote sellers' and comparison
  reports).
