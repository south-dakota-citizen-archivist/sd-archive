# voter-registration

South Dakota voter registration by county and party, from the Secretary of State's reports:
the totals for each election from 1976 to 2014, and monthly since December 2015. An
**aggregate** dataset in the catalog (`voter-registration`): the unit is a county (or the
state) × report date × party. Published at
[/voter-registration/](https://south-dakota-citizen-archivist.github.io/voter-registration/)
(the page is built by `publish/`).

```sh
just voter-registration                                  # both steps
uv run python pipelines/voter-registration/fetch.py      # new reports → snapshots/
uv run python pipelines/voter-registration/build.py      # tables + chart data → .cache/voter-registration/build/
```

The `Voter registration` workflow runs this daily. A new report's snapshot is committed, its
PDF goes on the
[voter-registration-sources](https://github.com/south-dakota-citizen-archivist/sd-archive/releases/tag/voter-registration-sources)
release, the tables on
[voter-registration-data](https://github.com/south-dakota-citizen-archivist/sd-archive/releases/tag/voter-registration-data),
and the site is rebuilt and published. A report that fails a check fails the run, and
nothing is published.

## What's here

- `snapshots/<date>.csv`: one file per report, a row per county with its registered voters by
  party as the report names them (`democratic`, `republican`, `libertarian`, `npa`,
  `npa_ind`, `inactive`, …). These are the record: monthly reports are added by `fetch.py`;
  the 1976–2014 election totals, and ten monthly reports from 2015–2018 that can't be read
  reliably, were entered by hand from the reports.
- `sources.json`: every link on the Secretary of State's page, its URL, the date in its
  text and the date printed in the report (they differ: the "July 28, 2026" link is the July
  31 report, the "August 1, 2026" link too, and a notice about a party's status links to the
  November 20, 2018 report), and how a hand-entered report was read.
- `elections.json`: which reports are an election's totals.
- `fixes.toml`: corrections to reports as published (none now), and the record of
  corrections made to the hand-entered snapshots.

## Reading a report (`parse.py`)

The report is a table over two pages: a dated title, a header naming the parties (their order
changes from report to report), a row per county and a Total row. Columns are read by their
headings. A report printed without table rules (July 31, 2026) is read by where its words line
up, with its headings from the header's text. Before a report is used it's checked: 66
counties, each party column adding up to the Total row, and each county's parties adding up
to its total active voters. A report that fails isn't added.

Checked against the hand-made snapshots, the parser reproduces 131 of the 143 monthly reports
exactly. It refuses the rest: the 2015–2016 layouts, a report with a garbled text layer
(May 2018) and one that doesn't add up (October 2, 2018: Bennett County's parties total 1,826
against 1,827 active voters, kept as reported).

## The tables (`build.py`)

- `registration`: one row per report date × county × party, as reported.
- `registration-simplified`: active voters only, by Republican, Democratic and other;
  Washabaugh County (merged into Jackson in 1983) left out, and Shannon County's rows under
  its current name, Oglala Lakota (46102).
- `chart.json`: the simplified table by date, statewide and by county, for the page.

Each row has the county's Census GEOID as of the report's date (Shannon is 46113 until
2015), the election (if any), the report's URL and its archived copy.

## Caveats

- Some early reports (1978–1982) count Washabaugh County with Jackson ("Jackson/Washabaugh");
  those rows are under Jackson.
- The September 1, 2026 report was first published with its Republican and Democratic
  columns headed the wrong way round, and replaced on September 14 with the columns right.
  The snapshot matches the corrected report; both PDFs are on the sources release.
- The 2014 general election's totals carry no date in their source; they're dated October 20,
  2014.
