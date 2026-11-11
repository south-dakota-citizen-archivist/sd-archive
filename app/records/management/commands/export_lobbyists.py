"""South Dakota's lobbyist registrations as CSV files:

- lobbyists-private.csv: one row per private lobbyist registration (a lobbyist, an employer,
  a year), with the lobbyist's name in parts and both addresses in parts
- lobbyists-private-filings.csv: one row per filing listed on a registration (authorizations,
  expense reports), with a link to the filing
- lobbyists-public.csv: one row per public lobbyist, agency and year

Registrations the Secretary of State no longer lists are included, with the date they were
first missed in `dropped_at`. Corrections to what the state publishes are in `corrections`."""

import csv
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

import sdnames
from records.models import Record

ADDRESS = ["street", "city", "state", "zip", "country"]
PRIVATE = [
    "registration_guid", "url", "year", "registration_number", "lobbyist_name",
    "lobbyist_name_given", "lobbyist_name_middle", "lobbyist_name_family",
    "lobbyist_name_suffix", "lobbyist_status", "lobbyist_type", "lobbyist_employment_date",
    "lobbyist_phone", "lobbyist_email", "lobbyist_occupation", "lobbyist_address",
    *(f"lobbyist_address_{p}" for p in ADDRESS), "employer_name", "employer_agent_name",
    "employer_registration_date", "employer_authorization_date", "employer_lobbying_subjects",
    "employer_registration_status", "employer_address",
    *(f"employer_address_{p}" for p in ADDRESS), "filings", "corrections", "first_seen",
    "dropped_at",
]  # fmt: skip
FILINGS = ["registration_guid", "year", "lobbyist_name", "employer_name", "filing_type",
           "filing_date", "filing_number", "filing_url"]  # fmt: skip
PUBLIC = ["year", "lobbyist_name", "agency", "agency_address",
          *(f"agency_address_{p}" for p in ADDRESS), "listed_as", "first_seen",
          "dropped_at"]  # fmt: skip


def _when(value) -> str:
    return value.isoformat(timespec="seconds") if value else ""


def private_rows(records):
    """(registration row, its filing rows) for each private registration."""
    for url, data, first_seen, dropped_at in records:
        name = sdnames.person(data.get("lobbyist_name") or "", "given_first")
        row = {f: data.get(f) for f in PRIVATE} | {
            "url": url,
            "lobbyist_name_given": name.given, "lobbyist_name_middle": name.middle,
            "lobbyist_name_family": name.family, "lobbyist_name_suffix": name.suffix,
            "filings": len(data.get("filings") or []),
            "corrections": json.dumps(data["corrections"]) if data.get("corrections") else "",
            "first_seen": _when(first_seen), "dropped_at": _when(dropped_at),
        }  # fmt: skip
        filings = [{**{f: data.get(f) for f in FILINGS[:4]}, **{f: x.get(f) for f in FILINGS[4:]}}
                   for x in data.get("filings") or []]  # fmt: skip
        yield row, filings


def public_rows(records):
    for data, first_seen, dropped_at in records:
        yield {f: data.get(f) for f in PUBLIC} | {
            "listed_as": data.get("listed_as") or "public",
            "first_seen": _when(first_seen), "dropped_at": _when(dropped_at),
        }  # fmt: skip


def _write(path: Path, fields: list[str], rows) -> int:
    n = 0
    with path.open("w", newline="") as f:
        out = csv.DictWriter(f, fields)
        out.writeheader()
        for row in rows:
            out.writerow(row)
            n += 1
    return n


class Command(BaseCommand):
    help = "Write the private and public lobbyist registrations as CSV files"

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(Path(settings.MEDIA_ROOT) / "exports" /
                                                 "lobbyists"))  # fmt: skip

    def handle(self, *args, out, **options):
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        private = (
            Record.objects.filter(recordset__slug="private-lobbyists")
            .order_by("-data__year", "data__lobbyist_name", "key")
            .values_list("key", "data", "first_seen", "dropped_at")
        )
        registrations, filings = [], []
        for row, rows in private_rows(private.iterator()):
            registrations.append(row)
            filings += rows
        public = (
            Record.objects.filter(recordset__slug="public-lobbyists")
            .order_by("-data__year", "data__lobbyist_name", "data__agency")
            .values_list("data", "first_seen", "dropped_at")
        )
        for name, fields, rows in (
            ("lobbyists-private.csv", PRIVATE, registrations),
            ("lobbyists-private-filings.csv", FILINGS, filings),
            ("lobbyists-public.csv", PUBLIC, public_rows(public.iterator())),
        ):
            n = _write(out / name, fields, rows)
            self.stdout.write(f"  {name}: {n:,} rows")
        self.stdout.write(self.style.SUCCESS(f"Wrote 3 files to {out}"))
