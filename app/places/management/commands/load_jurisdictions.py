from django.core.management.base import BaseCommand

from places import load


class Command(BaseCommand):
    help = (
        "Load South Dakota's places and boundaries from sdplaces: the state, counties, cities "
        "and towns, census designated places, tribal areas, school districts and legislative "
        "districts, including former ones"
    )

    def add_arguments(self, parser):
        parser.add_argument("kinds", nargs="*", help=f"some of {', '.join(load.KINDS)}")

    def handle(self, *args, kinds, **options):
        counts = load.load(tuple(kinds) or load.KINDS)
        self.stdout.write(self.style.SUCCESS(f"Loaded {counts}"))
