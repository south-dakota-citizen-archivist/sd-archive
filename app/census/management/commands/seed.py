from django.core.management.base import BaseCommand, CommandError

from census import seed


class Command(BaseCommand):
    help = "Load publishers, sources and watched datasets from seeds/sources.toml"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dump",
            action="store_true",
            help="Write the database to the file instead. The app does this after every change.",
        )

    def handle(self, *args, **options):
        if options["dump"]:
            changed = seed.dump()
            self.stdout.write(f"{seed.SEED_FILE} {'updated' if changed else 'already up to date'}")
            return
        try:
            counts = seed.load()
        except seed.SeedError as e:
            raise CommandError(str(e)) from e
        self.stdout.write(self.style.SUCCESS(f"Seeded {counts}"))
        if counts["watched"] < counts["watch_entries"]:
            self.stdout.write(
                "Some watched datasets aren't in the census yet. Crawl their sources, "
                "then run `seed` again."
            )
