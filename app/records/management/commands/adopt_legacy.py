from django.core.management.base import BaseCommand, CommandError

from records.compare import adopt


class Command(BaseCommand):
    help = (
        "Before a source's first run: give its imported records the source's keys (legacy_key), "
        "merging imported duplicates of one item"
    )

    def add_arguments(self, parser):
        parser.add_argument("slug", help="record set slug")
        parser.add_argument("--dry-run", action="store_true", help="report, change nothing")

    def handle(self, *args, slug, dry_run, **options):
        try:
            out = adopt(slug, dry_run=dry_run)
        except ValueError as e:
            raise CommandError(str(e)) from None
        verb = "would be rekeyed" if dry_run else "rekeyed"
        self.stdout.write(f"{out.rekeyed} record(s) {verb}, {len(out.merged)} merged:")
        for key, old in out.merged.items():
            self.stdout.write(f"  {key} ← {', '.join(old)}")
