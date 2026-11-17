from django.core.management.base import BaseCommand

from records.newspaper_links import link_newspapers
from records.record_links import link_sales


class Command(BaseCommand):
    help = (
        "Rebuild the links between records: property sales to the GIS features of the "
        "parcels they list, and newspaper statements to the papers in the press directory"
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="count, change nothing")
        parser.add_argument("--only", choices=["sales", "newspapers"], help="one kind of link")

    def handle(self, *args, dry_run, only, **options):
        verb = "would link" if dry_run else "linked"
        if only in (None, "sales"):
            out = link_sales(dry_run=dry_run)
            self.stdout.write(f"{out.sales:,} sales {verb} to GIS features ({out.links:,} links)")
        if only in (None, "newspapers"):
            out = link_newspapers(dry_run=dry_run)
            self.stdout.write(
                f"newspaper statements: {out.links:,} {verb} to the press "
                f"directory; names: {dict(out.names)}"
            )
