from django.core.management.base import BaseCommand, CommandError

from records import sources
from records.compare import compare
from records.models import RecordSet


class Command(BaseCommand):
    help = "Fetch a record set's source and report how it differs from the stored records"

    def add_arguments(self, parser):
        parser.add_argument("slug", help="record set slug")
        parser.add_argument("--examples", type=int, default=3, help="examples per difference")

    def handle(self, *args, slug, examples, **options):
        if not RecordSet.objects.filter(slug=slug).exists():
            raise CommandError(f"unknown record set: {slug}")
        from sdfetch import Fetcher

        source = sources.get(slug)
        with Fetcher() as fetcher:
            items = list(source.items(fetcher))
        c = compare(slug, items, examples=examples)
        w = self.stdout.write
        w(f"found {c.found}, stored {c.stored}: {c.same} the same, {len(c.changed)} differ, "
          f"{len(c.only_found)} only at the source, {len(c.only_stored)} only stored")  # fmt: skip
        for label, keys in (("only at the source", c.only_found), ("only stored", c.only_stored)):
            for key in keys[: max(examples, 10)]:
                w(f"  {label}: {key}")
        for name, n in c.fields.most_common():
            w(f"  field {name}: {n} differ")
            for key, old, new in c.examples[name]:
                w(f"    {key}\n      stored: {old!r}\n      source: {new!r}")
