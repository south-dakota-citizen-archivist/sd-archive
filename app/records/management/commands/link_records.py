from django.core.management.base import BaseCommand

from records.record_links import link_sales


class Command(BaseCommand):
    help = "Rebuild the links from property sales to the GIS features of the parcels they list"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="count, change nothing")

    def handle(self, *args, dry_run, **options):
        out = link_sales(dry_run=dry_run)
        verb = "would link" if dry_run else "linked"
        self.stdout.write(f"{out.sales:,} sales {verb} to GIS features ({out.links:,} links)")
        self.stdout.write(f"  parcel IDs: {dict(out.ids)}")
        for county, n in out.by_county.items():
            self.stdout.write(f"  {county}: {n}")
