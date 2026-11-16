import json

from django.core.management.base import BaseCommand, CommandError

from census.finders.run import FINDERS, finder_job, run
from core.jobs import run_job


class Command(BaseCommand):
    help = "Look for GIS servers the census doesn't have yet; list them as candidates"

    def add_arguments(self, parser):
        parser.add_argument("finders", nargs="*", help=f"default: all ({', '.join(FINDERS)})")

    def handle(self, *args, finders, **options):
        if unknown := set(finders) - set(FINDERS):
            raise CommandError(f"unknown finder(s): {', '.join(sorted(unknown))}")
        finders = finders or list(FINDERS)
        job = finder_job(finders)
        run_job(job, lambda: run(finders))
        self.stdout.write(json.dumps(job.result, indent=2))
