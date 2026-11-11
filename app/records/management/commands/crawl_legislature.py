from django.core.management.base import BaseCommand, CommandError

from core.jobs import create_job, run_job
from records.models import RecordSet
from records.sources._legislature.crawl import Crawl, raw_root


class Command(BaseCommand):
    help = (
        "Crawl sdlegislature.gov's API into media/raw/legislature/ (resumes; earlier sessions "
        "are kept, the current one refetched daily)"
    )

    def add_arguments(self, parser):
        parser.add_argument("--steps", default=",".join(Crawl.STEPS),
                            help=f"comma-separated: {', '.join(Crawl.STEPS)}")  # fmt: skip
        parser.add_argument("--sessions", help="only these SessionIds (comma-separated)")

    def handle(self, *args, steps, sessions, **options):
        from sdfetch import Fetcher

        chosen = [s.strip() for s in steps.split(",") if s.strip()]
        if bad := [s for s in chosen if s not in Crawl.STEPS]:
            raise CommandError(f"unknown step(s): {', '.join(bad)}")
        only = [int(s) for s in sessions.split(",")] if sessions else None
        target = RecordSet.objects.filter(slug="legislature-sessions").first()
        if target is None:
            raise CommandError("no legislature-sessions record set (run import_prl first)")
        job = create_job("crawl-legislature", target, {"steps": chosen, "sessions": only})
        with Fetcher(raw_root(), delay=1.0) as fetcher:
            run_job(job, lambda: Crawl(fetcher).run(chosen, only))
        self.stdout.write(self.style.SUCCESS(f"  {job.result}"))
