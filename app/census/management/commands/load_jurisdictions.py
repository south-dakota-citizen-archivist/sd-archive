from django.core.management.base import BaseCommand

from census import jurisdictions


class Command(BaseCommand):
    help = "Load South Dakota's state, county, city/town and tribal boundaries from the Census"

    def handle(self, *args, **options):
        counts = jurisdictions.load()
        self.stdout.write(self.style.SUCCESS(f"Loaded {counts}"))
