from pathlib import Path

from django.core.management.base import BaseCommand

from census import beacon


class Command(BaseCommand):
    help = (
        "Register a state's Beacon apps as sources, from the client picker saved from "
        "beacon.schneidercorp.com (default: seeds/beacon-clients.html). Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", type=Path, default=beacon.CLIENTS_FILE)
        parser.add_argument("--state", default="South Dakota")

    def handle(self, *args, path, state, **options):
        result = beacon.load(path, state=state)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result['clients']} Beacon apps in {state}; {result['created']} new sources"
            )
        )
        for name in result["unmatched"]:
            self.stdout.write(self.style.WARNING(f"No jurisdiction matches {name!r}"))
