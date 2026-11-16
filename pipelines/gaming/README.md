# gaming

South Dakota's licensed gaming (Deadwood's casinos), from the Commission on Gaming's
statistics on the Department of Revenue's
[gaming page](https://dor.sd.gov/businesses/gaming/): by month since January 2018 (slot
machines by denomination, table games, sports wagering by sport, and the gaming tax and where
it goes) and by calendar year, 1989–2019, by game. An **aggregate** dataset in the catalog
(`gaming`): the unit is the state × month (or year) × game.

```sh
just gaming                                    # both steps
uv run python pipelines/gaming/fetch.py        # the reports → .cache/gaming/raw/
uv run python pipelines/gaming/build.py        # tables + checks → .cache/gaming/build/
```

## What the Commission publishes

**A two-page summary for each month.** Page 1 has slot machines by denomination and table
games by game (units, handle, statistical win, average payout), sports wagering's totals
(from September 2021), the handle comparison (the month and the calendar year to date, each
with the change from a year before), the tax calculation (total statistical win, the
deductions and additions, the taxable adjusted gross revenue and the 9% tax) and the tax's
distribution (1% to the state general fund, and 8% shared 40% to tourism, 10% to Lawrence
County and 50% to the Commission fund). Page 2 has sports wagering by sport. Since 2023 each
month is its own file; **2018–2022** are one file a year, each month's summary scanned and
bound together (found by the footer each page carries as text, "January 2018 South Dakota
Gaming Statistics").

**A calendar-year summary, 1989–2019** ("stats by denom", with a text layer): for each year
and game (blackjack, house- and player-banked poker, craps, roulette, keno, and slot machines
by denomination) the units, drop (table games) or coins in (slots), gross revenue, average
daily hold per unit and hold percentage, then the year's units, handle, table game and slot
machine revenue, total gross revenue and the number of retailers reporting revenue.

## The tables (`build.py`)

- `slots`: month × denomination (25 Dollars … 1 Cents, and City Slot): units, handle,
  statistical win, payout percent.
- `table_games`: month × game (Black Jack, House and Player Banked Poker, Craps, Roulette,
  Keno).
- `sports`: month × sport.
- `monthly`: one row per month: each section's totals, total handle, each section's handle
  for the year to date and its change from a year before, every tax line and the
  distribution.
- `annual_by_game`: year × game, 1989–2019 (`annual.py`).
- `annual`: the year's totals, 1989–2019.

Every row names its file's URL, and the monthly rows the `pages` of a yearly file and how
the report was read (`read`: `text`, or `ocr (…)`, below). Payout percentages printed as
"N/A" (player-banked poker, where the house takes a rake) or "#DIV/0!" (no handle) are empty.

## Reading the scans (`scans.py`)

A scanned month is read by three OCR engines: rapidocr, Apple Vision (through `textra`) and
Tesseract; the last two where they're installed. Each engine's pieces are grouped into lines
at three tolerances, since rows printed close together run into each other or split apart,
and the rapidocr and Vision pieces (which are table cells) are also read column by column,
for the months whose columns are printed out of line with the names. That makes up to a dozen
readings of a month. The first that passes every check below is used.

When none does, the readings are combined: each part (a row, a Totals line, a handle
comparison figure, a tax line) is taken from the best reading that has it, and where readings
differ on a part, the others' versions are tried, one or two at a time, until every check
passes. The checks tie each figure to others (rows to their Totals, each payout to its handle
and win, the tax lines to the taxable revenue and the 9%), so a part taken from another
reading is confirmed by the arithmetic. `read` says which reading each part came from.

The handle comparison's year-to-date figures and changes from a year before aren't covered
by any check within the month. From a scan, each is kept only where two engines read it
alike, or, when they don't, where one engine's reading is what the month before (the year
to date runs on from it) or the same month a year before (the change) gives. A scanned
figure that contradicts those is left empty. A month that no reading or combination gets
through every check is left out.

**Review.** The build also writes `review.json`, the queue for the app's review pages
(`/review/`): each month left out, as its tables with the closest reading's figures, every
engine's reading of each cell and the checks that fail; and each figure left unsettled.
Decisions are saved to `fixes.toml` here (`sddocs.review`: the cell, the value, the reading
it's from, and a reason) and applied on every build. A cell decided by hand is set in every
reading. A month with decisions is used even if a check still fails: what doesn't add up is
listed under "doesn't add up, settled by hand", and a figure settled by hand is kept when
other months contradict it, and noted. The monthly table's `by_hand` column has each cell
settled by hand and why; a decision on cell `*` says the table as read is right. The review
page's checks run `checks.py`.

OCR pieces are kept in `.cache/gaming/ocr/`, by file contents, so the parser can change
without reading again. Without `textra` and Tesseract, fewer months get through.

## Checks and caveats

Every month is checked, and `checks.json` lists every finding:
- the denominations and games add up to their totals, and the sports to sports wagering's
- each payout percentage is (handle − win) ÷ handle; a Totals line's covers only the games
  that have one, so not player-banked poker
- the handle comparison's current figures match the sections
- the year to date runs on from the month before, and each change from a year before is the
  month's handle against the same month's a year before
- the total statistical win is the sections' wins, the deductions and additions add up to the
  taxable adjusted gross revenue, the tax due is 9% of it, and each share of the distribution
  is right
- the yearly summary's game revenues add up to its totals, and each game's hold percentage
  is its revenue over its drop or coins in
- for 2018 and 2019, which both cover, the twelve months add up to the yearly summary, game by
  game (handle, drop or coins in, and revenue): they do, to the cent

Caveats:
- **Months left out:** April, August and September 2021; March, April, May, July and
  September 2022; and January 2024. In these, OCR merges or drops a figure the checks need
  and no reading has it right.
- **Scanned figures left empty:** a few year-to-date figures and changes from a year before
  the engines don't agree on and other months don't settle, and those that contradict the
  months around them: June 2018's table games year to date (misread), and September 2019's
  table games and November 2021's sports wagering year to date, each off from the running
  total by the same amount as the total handle's, which is left empty too.
- **September 2023's year to date for slots, and so the total handle's, is $10,000 less**
  than August's plus September's; October's is back in line (printed as published).
- The label for amended returns and audit adjustments changes from month to month
  ("Amended Returns", "Amended Returns/Court Settlement", "Audit Adjustments",
  "Adjustments for Amended Returns and Audit Findings"); all are
  `adjustments_amended_returns_and_audits`.
- In the yearly summary, **player-banked poker's Drop is printed equal to its revenue** (the
  house's rake), and its HANDLE includes the poker's wagers, so HANDLE is more than the
  games' drop and coins in. Units are yearly averages, rounded, so the games' come within a
  few of the year's. Its own sums are off by $3 in 2006 (table game revenue) and $300 in 2017
  (total gross revenue). Player-banked poker's average daily hold for 2018 and 2019 is printed
  "100.00%" and 2000's .01 slots "Combined with .05 slots in prior months"; those are empty,
  with what was printed in `note`.
