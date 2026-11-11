"""Write a record set's mentions again from its records (after a source starts writing them,
or changes how), replacing the ones it has."""

from django.core.management.base import BaseCommand, CommandError

from records import sources
from records.models import RecordSet


class Command(BaseCommand):
    help = "Rebuild the mentions of every record in a record set"

    def add_arguments(self, parser):
        parser.add_argument("slug", help="record set slug")
        parser.add_argument("--batch", type=int, default=5000)

    def handle(self, *args, slug, batch, **options):
        from itertools import batched

        from records.run import _mentions

        source = sources.get(slug)
        if source.mentions is None:
            raise CommandError(f"{slug}: the source writes no mentions")
        recordset = RecordSet.objects.get(slug=slug)
        keys = list(recordset.records.values_list("key", flat=True).order_by("key"))
        for chunk in batched(keys, batch):
            _mentions(source, recordset, list(chunk))
        self.stdout.write(self.style.SUCCESS(f"Rebuilt the mentions of {len(keys):,} records"))
