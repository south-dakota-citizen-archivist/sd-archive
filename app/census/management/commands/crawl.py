from django.core.management.base import BaseCommand, CommandError

from census.crawl import crawl_source
from census.models import Source
from census.tasks import crawl_summary
from core.jobs import create_job, run_job


class Command(BaseCommand):
    help = "Crawl sources now (no Celery worker needed) and update the census"

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="*", help="source slugs (default: all active)")

    def handle(self, *args, slugs, **options):
        sources = Source.objects.filter(active=True)
        if slugs:
            sources = Source.objects.filter(slug__in=slugs)
            missing = set(slugs) - set(sources.values_list("slug", flat=True))
            if missing:
                raise CommandError(f"unknown source(s): {', '.join(sorted(missing))}")

        failed = 0
        for source in sources:
            self.stdout.write(f"Crawling {source} ...")
            job = create_job("crawl", source)
            try:
                run_job(job, lambda s=source, j=job: crawl_summary(crawl_source(s, j)))
                self.stdout.write(self.style.SUCCESS(f"  {job.result}"))
            except Exception as e:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  failed: {e}"))
        if failed:
            raise CommandError(f"{failed} crawl(s) failed")
