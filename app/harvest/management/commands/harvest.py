from django.core.management.base import BaseCommand, CommandError

from census.models import Dataset
from core.jobs import create_job, run_job
from harvest.service import harvest_dataset
from harvest.tasks import harvest_summary


class Command(BaseCommand):
    help = "Harvest datasets now (no Celery worker needed)"

    def add_arguments(self, parser):
        parser.add_argument("ids", nargs="*", type=int, help="dataset IDs (default: all watched)")
        parser.add_argument(
            "--force", action="store_true", help="save even if the count dropped past max_drop"
        )

    def handle(self, *args, ids, force, **options):
        datasets = (
            Dataset.objects.filter(pk__in=ids)
            if ids
            else Dataset.objects.filter(status=Dataset.Status.WATCHED)
        )
        if ids and len(datasets) != len(set(ids)):
            raise CommandError("unknown dataset ID(s)")

        failed = 0
        for dataset in datasets:
            self.stdout.write(f"Harvesting {dataset} ({dataset.pk}) ...")
            job = create_job("harvest", dataset, {"force": force})
            try:
                run_job(job, lambda d=dataset, j=job: harvest_summary(harvest_dataset(d, j, force)))
                self.stdout.write(self.style.SUCCESS(f"  {job.result}"))
            except Exception as e:
                failed += 1
                self.stdout.write(self.style.ERROR(f"  failed: {e}"))
        if failed:
            raise CommandError(f"{failed} harvest(s) failed")
