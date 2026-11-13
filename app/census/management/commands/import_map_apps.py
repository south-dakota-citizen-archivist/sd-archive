from pathlib import Path

from django.core.management.base import BaseCommand

from census import mapapps


class Command(BaseCommand):
    help = (
        "Register a state's county map apps as sources, from the app picker saved from the "
        "apps' host (default: seeds/map-apps.html). Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", type=Path, default=mapapps.APPS_FILE)
        parser.add_argument("--state", default="South Dakota")

    def handle(self, *args, path, state, **options):
        result = mapapps.load(path, state=state)
        self.stdout.write(
            self.style.SUCCESS(
                f"{result['clients']} map apps in {state}; {result['created']} new sources"
            )
        )
        for name in result["unmatched"]:
            self.stdout.write(self.style.WARNING(f"No jurisdiction matches {name!r}"))
