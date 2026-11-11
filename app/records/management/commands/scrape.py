import contextlib

from django.core.management.base import BaseCommand, CommandError

from core.jobs import create_job, run_job
from records import sources
from records.models import RecordSet
from records.run import ensure_recordset, run_source


class Command(BaseCommand):
    help = "Run record sets' sources now: fetch their items and store the latest"

    def add_arguments(self, parser):
        parser.add_argument("slugs", nargs="+", help="record set slugs")
        parser.add_argument(
            "--force", action="store_true", help="store even if the count dropped past max_drop"
        )

    def handle(self, *args, slugs, force, **options):
        recordsets = {rs.slug: rs for rs in RecordSet.objects.filter(slug__in=slugs)}
        for slug in set(slugs) - set(recordsets):
            # a source new to the archive declares its record set
            with contextlib.suppress(ModuleNotFoundError, RecordSet.DoesNotExist):
                recordsets[slug] = ensure_recordset(sources.get(slug))
        if unknown := sorted(set(slugs) - set(recordsets)):
            raise CommandError(f"unknown record set(s): {', '.join(unknown)}")
        failed = 0
        for slug in slugs:
            self.stdout.write(f"Running {slug} ...")
            job = create_job("scrape", recordsets[slug], {"force": force})
            try:
                run_job(job, lambda s=slug, j=job: summary(run_source(s, job=j, force=force)))
                self.stdout.write(self.style.SUCCESS(f"  {job.result}"))
            except Exception as e:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  failed: {e}"))
        if failed:
            raise CommandError(f"{failed} run(s) failed")


def summary(run) -> dict:
    return {"run": run.pk, "fetched": run.fetched_count, "added": run.added,
            "changed": run.changed, "removed": run.removed, "unchanged": run.unchanged}  # fmt: skip
