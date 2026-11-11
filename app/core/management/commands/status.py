"""`just status`: what's running, stalled, failed or overdue, and what will resume."""

import shutil
import socket
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.jobs import mark_stalled
from core.models import Job
from core.notify import failure_streak
from core.tasks import has_open_job, next_run

RECENT = timedelta(days=7)
# Due this long ago and still not started means the scheduler (beat) isn't running
SCHEDULER_SLACK = timedelta(minutes=5)


def ago(when) -> str:
    if when is None:
        return "never"
    seconds = (timezone.now() - when).total_seconds()
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds / size:.0f}{unit} ago"
    return "just now"


def broker_up() -> bool:
    url = urlparse(settings.CELERY_BROKER_URL)
    try:
        with socket.create_connection((url.hostname or "localhost", url.port or 6379), 1):
            return True
    except OSError:
        return False


def workers() -> list[str] | None:
    """Names of Celery workers that answer a ping; None if the broker is down."""
    if not broker_up():
        return None
    from config.celery import app

    try:
        return sorted(name for reply in app.control.ping(timeout=1.0) for name in reply)
    except Exception:  # noqa: BLE001 (any broker trouble means "can't tell")
        return []


def overdue(now) -> list[tuple[str, object]]:
    """Watched datasets and crawlable sources past due with no job open."""
    from census.models import Dataset, Source

    out = []
    sources = Source.objects.filter(active=True, platform__in=Source.CRAWLABLE)
    for s in sources.exclude(crawl_schedule=""):
        due = next_run(s.crawl_schedule, s.last_scheduled_at or s.last_crawled_at)
        if due and due + SCHEDULER_SLACK <= now and not has_open_job(s):
            out.append((f"crawl {s}", due))
    watched = Dataset.objects.filter(status=Dataset.Status.WATCHED, removed_at=None)
    for d in watched.exclude(harvest_schedule=""):
        due = next_run(d.harvest_schedule, d.last_scheduled_at or d.last_harvested_at)
        if due and due + SCHEDULER_SLACK <= now and not has_open_job(d):
            out.append((f"harvest {d}", due))
    return out


def staged() -> list[tuple[str, int, int]]:
    """Harvest downloads saved partway: (dataset id, batches, bytes)."""
    root = Path(settings.MEDIA_ROOT) / "staging" / "harvest"
    out = []
    for folder in sorted(root.glob("*")) if root.exists() else []:
        batches = list(folder.glob("batch-*.pkl"))
        out.append((folder.name, len(batches), sum(b.stat().st_size for b in batches)))
    return out


def documents() -> tuple[list[tuple[str, int]], int, int]:
    """Failed documents by record set, records stuck without help, readings awaiting review."""
    from django.db.models import Count

    from records.models import Document, Extraction, Record

    failed = list(
        Document.objects.filter(status=Document.Status.FAILED)
        .values("record__recordset__slug")
        .annotate(n=Count("pk"))
        .order_by("-n")
        .values_list("record__recordset__slug", "n")
    )
    stuck = Record.objects.stuck().count()
    waiting = Extraction.objects.filter(review=Extraction.Review.PENDING).count()
    return failed, stuck, waiting


def last_dumps() -> dict[str, str]:
    """The last line logged for each database by the nightly dump."""
    log = Path(settings.BACKUP_DIR) / "dump.log"
    if not log.exists():
        return {}
    out = {}
    for line in log.read_text().splitlines()[-50:]:
        parts = line.split()
        if len(parts) >= 4 and parts[2] in ("OK", "FAIL", "FAILED"):
            out[parts[3]] = line
        elif len(parts) >= 3 and parts[2].startswith("SKIP"):
            out["(all)"] = line
    return out


class Command(BaseCommand):
    help = "What's running, stalled, failed or overdue, and what will resume."

    def handle(self, *args, **options):
        now = timezone.now()
        w = self.stdout.write
        warn, ok = self.style.WARNING, self.style.SUCCESS

        newly_stalled = mark_stalled()

        w(self.style.MIGRATE_HEADING("Workers"))
        names = workers()
        if names is None:
            w(warn("  Redis isn't reachable: no worker can run (brew services start redis)"))
        elif not names:
            w(warn("  No Celery worker is running (just worker); queued jobs wait"))
        else:
            w(ok(f"  {len(names)} worker(s): {', '.join(names)}"))

        w(self.style.MIGRATE_HEADING("Jobs"))
        running = Job.objects.filter(status=Job.Status.RUNNING)
        for job in running:
            w(f"  running   {job.kind:8} {job.label[:60]}  started {ago(job.started_at)}"
              f"  on {job.worker or '?'}")  # fmt: skip
        pending = Job.objects.filter(status=Job.Status.PENDING).order_by("created_at")
        if pending:
            oldest = ago(pending[0].created_at)
            w(f"  pending   {pending.count()} job(s), the oldest queued {oldest}")
        recent = Job.objects.filter(finished_at__gte=now - RECENT)
        for job in recent.filter(status=Job.Status.STALLED):
            fresh = " (just found)" if job in newly_stalled else ""
            w(warn(f"  stalled   {job.kind:8} {job.label[:60]}  {ago(job.finished_at)}{fresh}"))
        seen = set()
        for job in recent.filter(status=Job.Status.FAILED).order_by("-finished_at"):
            key = (job.kind, job.target_type, job.target_id)
            if key in seen:
                continue
            seen.add(key)
            streak = failure_streak(job)
            first = (job.error or "").splitlines()[0][:80] if job.error else ""
            times = f", {streak} in a row" if streak > 1 else ""
            w(warn(f"  failed    {job.kind:8} {job.label[:60]}  {ago(job.finished_at)}{times}"))
            if first:
                w(f"            {first}")
        if not (running or pending or recent.filter(status__in=["stalled", "failed"]).exists()):
            w(ok("  nothing running, pending, stalled or failed in the last week"))

        w(self.style.MIGRATE_HEADING("Overdue"))
        late = overdue(now)
        for label, due in late:
            w(warn(f"  {label}: due {ago(due)}"))
        if late:
            w("  Overdue work starts within a minute while `just beat` and `just worker` run.")
        else:
            w(ok("  nothing overdue"))

        w(self.style.MIGRATE_HEADING("Will resume"))
        saved = staged()
        for dataset_id, n, size in saved:
            w(f"  harvest of dataset {dataset_id}: {n} batch(es) saved ({size / 1e6:.1f} MB)")
        if not saved:
            w("  no partial downloads")

        w(self.style.MIGRATE_HEADING("Documents"))
        failed_docs, stuck, waiting = documents()
        for slug, n in failed_docs[:10]:
            w(warn(f"  {slug}: {n} failed document(s)"))
        if failed_docs:
            w("  `manage.py retry_documents --recordset <slug>` sends them back to try again.")
        if stuck:
            w(warn(f"  {stuck} record(s) stuck: every document failed or waits on a rejected or "
                   "unreviewed reading"))  # fmt: skip
        if waiting:
            w(f"  {waiting} reading(s) waiting on review")
        if not (failed_docs or stuck or waiting):
            w(ok("  no failed documents, stuck records or readings to review"))

        w(self.style.MIGRATE_HEADING("Disk"))
        free = shutil.disk_usage(settings.MEDIA_ROOT if Path(settings.MEDIA_ROOT).exists()
                                 else Path.home()).free / 1e9  # fmt: skip
        line = f"  {free:.0f} GB free (big downloads stop below {settings.DISK_MIN_FREE_GB:.0f} GB)"
        w(warn(line) if free < settings.DISK_MIN_FREE_GB else line)

        w(self.style.MIGRATE_HEADING("Backups"))
        dumps = last_dumps()
        for db, line in dumps.items():
            when = timezone.make_aware(timezone.datetime.fromisoformat(line[:19]))
            parts = line.split()
            detail = " ".join(parts[2:3] + parts[4:]) if db != "(all)" else " ".join(parts[2:])
            text = f"  {db}: {detail}  ({ago(when)})"
            w(warn(text) if "OK" not in line or now - when > timedelta(days=2) else text)
        if not dumps:
            w(warn(f"  no dump log in {settings.BACKUP_DIR}"))
