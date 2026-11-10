"""macOS notifications for failed work, in place of hosted monitoring."""

import logging
import shutil
import subprocess

from django.conf import settings

from core.models import Job

logger = logging.getLogger(__name__)

# The text is passed as arguments, never pasted into the script, so quotes can't break it
_SCRIPT = [
    "-e", "on run argv",
    "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
    "-e", "end run",
]  # fmt: skip


def notify(title: str, message: str) -> bool:
    """Show a macOS notification. Does nothing where notifications are off (tests, Linux)."""
    if not settings.NOTIFY or not shutil.which("osascript"):
        return False
    try:
        subprocess.run(["osascript", *_SCRIPT, title, message[:200]], check=True,
                       capture_output=True, timeout=10)  # fmt: skip
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Notification not shown: %s", e)
        return False
    return True


def failure_streak(job: Job) -> int:
    """How many runs in a row, ending with this one, failed or stalled for the job's target."""
    if job.target_id is None:
        return 1
    streak = 0
    for status in (
        Job.objects.filter(kind=job.kind, target_type=job.target_type, target_id=job.target_id)
        .exclude(status__in=[Job.Status.PENDING, Job.Status.RUNNING])
        .order_by("-created_at")
        .values_list("status", flat=True)
    ):
        if status not in (Job.Status.FAILED, Job.Status.STALLED):
            break
        streak += 1
    return streak


def job_failed(job: Job) -> bool:
    """Notify on a new failure (the first after a success) and again at three in a row, not
    on every scheduled retry in between."""
    streak = failure_streak(job)
    if streak not in (1, 3):
        return False
    title = f"sd-archive: {job.kind} failed" + (" 3 times in a row" if streak == 3 else "")
    first_line = (job.error or "").splitlines()[0] if job.error else ""
    return notify(title, f"{job.label}\n{first_line}")
