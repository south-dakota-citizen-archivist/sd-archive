from django.core.management.base import BaseCommand, CommandError

from core.jobs import create_job, run_job
from records.documents import STEPS, process
from records.models import RecordSet


class Command(BaseCommand):
    help = "Move a record set's documents through fetch → read → categorize → extract"

    def add_arguments(self, parser):
        parser.add_argument("slug", help="record set slug")
        parser.add_argument(
            "--steps", default=",".join(STEPS), help=f"comma-separated: {', '.join(STEPS)}"
        )
        parser.add_argument("--limit", type=int, help="at most this many documents per step")
        parser.add_argument("--model", help="the model to categorize and extract with")

    def handle(self, *args, slug, steps, limit, model, **options):
        chosen = tuple(s.strip() for s in steps.split(",") if s.strip())
        if bad := [s for s in chosen if s not in STEPS]:
            raise CommandError(f"unknown step(s): {', '.join(bad)}")
        try:
            recordset = RecordSet.objects.get(slug=slug)
        except RecordSet.DoesNotExist:
            raise CommandError(f"unknown record set: {slug}") from None
        job = create_job("documents", recordset, {"steps": chosen, "limit": limit, "model": model})
        run_job(job, lambda: process(slug, steps=chosen, limit=limit, model=model))
        for step, tally in job.result.items():
            line = f"  {step:11} {tally['done']} done, {tally['failed']} failed"
            self.stdout.write(self.style.WARNING(line) if tally["failed"] else line)
