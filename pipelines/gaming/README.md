# gaming

South Dakota's licensed gaming (Deadwood's casinos) by month, from the Commission on Gaming's
monthly statistics on the Department of Revenue's
[gaming page](https://dor.sd.gov/businesses/gaming/): slot machines by denomination, table
games, sports wagering by sport, and the gaming tax and where it goes. An **aggregate** dataset
in the catalog (`gaming`): the unit is the state × month × game.

```sh
just gaming                                    # both steps
uv run python pipelines/gaming/fetch.py        # the reports → .cache/gaming/raw/
uv run python pipelines/gaming/build.py        # tables + checks → .cache/gaming/build/
```

## What the Commission publishes

A two-page summary for each month since January 2023. Page 1 has slot machines by
denomination and table games by game (units, handle, statistical win, average payout), sports
wagering's totals, the handle comparison (the month and the calendar year to date, each with
the change from a year before), the tax calculation (total statistical win, the deductions
and additions, the taxable adjusted gross revenue and the 9% tax) and the tax's distribution
(1% to the state general fund, and 8% shared 40% to tourism, 10% to Lawrence County and 50% to
the Commission fund). Page 2 has sports wagering by sport.

The page also has a file for each year from 2018 to 2022 and a 1989–2019 summary by game.
Those are pictures of the reports and aren't read yet.

## The tables (`build.py`)

- `slots`: month × denomination (25 Dollars … 1 Cents, and City Slot): units, handle,
  statistical win, payout percent.
- `table_games`: month × game (Black Jack, House and Player Banked Poker, Craps, Roulette).
- `sports`: month × sport.
- `monthly`: one row per month: each section's totals, total handle, each section's handle
  for the year to date and its change from a year before, every tax line and the
  distribution.

Every row names the report's URL and how it was read (`text`, or `ocr (rapidocr)` for a scan).
Payout percentages printed as "N/A" (player-banked poker, where the house takes a rake) or
"#DIV/0!" (no handle) are empty.

## Checks and caveats

Every month is checked, and `checks.json` lists every finding:
- the denominations and games add up to their totals, and the sports to sports wagering's
- each payout percentage is (handle − win) ÷ handle; a Totals line's covers only the games
  that have one, so not player-banked poker
- the handle comparison matches the sections, and the year to date runs on from the month
  before
- the total statistical win is the sections' wins, the deductions and additions add up to the
  taxable adjusted gross revenue, the tax due is 9% of it, and each share of the distribution
  is right

- **Five reports are scans.** They're read by OCR and used only if every check passes:
  October 2024 and February 2025 do. **August 2023, January 2024 and September 2024 are left
  out**: OCR splits January's rows across lines, and drops a row in the others, which the
  checks catch.
- **October 2023's year to date for slots, and so the total handle's, is $10,000 more** than
  September's plus October's (printed as published).
- The label for amended returns and audit adjustments changes from month to month
  ("Amended Returns", "Audit Adjustments", "Adjustments for Amended Returns and Audit
  Findings"); all are `adjustments_amended_returns_and_audits`.
