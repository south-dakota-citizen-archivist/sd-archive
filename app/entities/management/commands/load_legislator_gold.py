"""Legislators' session profiles as mentions, each tied by hand to the legislator it is.

gold/legislators.csv maps every session profile ID on sdlegislature.gov to the legislator's
canonical ID (the Historical Listing's): checked by hand, so profiles that share one are known
to be the same person. That makes them a gold set for scoring name matching.
"""

import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from entities import mentions

GOLD = Path(__file__).resolve().parents[2] / "gold" / "legislators.csv"
DATASET = "legislature"
API = "https://sdlegislature.gov/api/SessionMembers/Detail/{}"


def crosswalk(path: Path = GOLD) -> dict[str, str]:
    with path.open(newline="") as f:
        return {r["legislator_profile_id"]: r["legislator_id_canon"] for r in csv.DictReader(f)}


def profile_mentions(profile: dict, canon: str) -> list:
    pid = str(profile["legislator_profile_id"])
    address = ", ".join(
        p for p in (profile.get("address1"), profile.get("address2"), profile.get("city"),
                    " ".join(x for x in (profile.get("state"), profile.get("zipcode")) if x))
        if p
    )  # fmt: skip
    context = {k: profile.get(k) for k in ("year", "session_id", "chamber", "district", "party",
                                           "city", "counties", "occupation", "term")}  # fmt: skip
    return mentions.from_text(
        profile["name"], dataset=DATASET, record_key=pid, field="name", role="legislator",
        url=API.format(pid), context={k: v for k, v in context.items() if v},
        properties={"address": [address]} if address else {},
        gold_entity_id=f"sd-legislator:{canon}",
    )  # fmt: skip


class Command(BaseCommand):
    help = "Load legislators' session profiles as mentions with hand-checked identities (gold)"

    def add_arguments(self, parser):
        parser.add_argument(
            "profiles", type=Path,
            help="folder of sd-legislature-legislator-<profile id>.json files",
        )  # fmt: skip

    def handle(self, *args, profiles: Path, **options):
        xwalk = crosswalk()
        files = sorted(profiles.glob("sd-legislature-legislator-*.json"))
        if not files:
            raise CommandError(f"no profile files in {profiles}")
        rows, missing, disagree = [], [], []
        for path in files:
            profile = json.loads(path.read_text())
            pid = str(profile["legislator_profile_id"])
            canon = xwalk.get(pid)
            if canon is None:
                missing.append(pid)
                continue
            listed = profile.get("legislator_canonical_id")
            if listed is not None and str(listed) != canon:
                disagree.append(pid)
            rows += profile_mentions(profile, canon)
        n = mentions.replace(DATASET, rows, keys=xwalk)
        people = len({m.gold_entity_id for m in rows})
        self.stdout.write(self.style.SUCCESS(
            f"{n:,} mentions from {len(files):,} profiles, {people:,} legislators"
        ))  # fmt: skip
        for label, ids in (("not in the crosswalk", missing),
                           ("disagree with the crosswalk", disagree)):  # fmt: skip
            if ids:
                self.stdout.write(self.style.WARNING(f"{len(ids)} profiles {label}: {ids[:10]}"))
