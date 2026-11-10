from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from core import notify
from core.jobs import create_job, run_job
from core.models import Job
from core.tasks import next_run


def status(settings, tmp_path, monkeypatch) -> str:
    from core.management.commands import status as command

    monkeypatch.setattr(command, "workers", lambda: [])  # never ping a real broker
    settings.BACKUP_DIR = tmp_path / "backups"
    out = StringIO()
    call_command("status", stdout=out)
    return out.getvalue()


def test_status_reports_overdue_failed_and_resumable_work(
    dataset, settings, tmp_path, media_root, monkeypatch
):
    job = create_job("harvest", dataset)
    with pytest.raises(RuntimeError):
        run_job(job, lambda: (_ for _ in ()).throw(RuntimeError("server said no")))
    staged = media_root / "staging" / "harvest" / str(dataset.pk)
    staged.mkdir(parents=True)
    (staged / "batch-1-5-abc.pkl").write_bytes(b"x" * 1000)
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "dump.log").write_text("2026-11-10 02:30:07 OK   sd_gis_hub  26M\n")

    text = status(settings, tmp_path, monkeypatch)
    assert "failed    harvest" in text and "RuntimeError: server said no" in text
    assert f"crawl {dataset.source}: due" in text  # never crawled: due
    assert f"harvest of dataset {dataset.pk}: 1 batch(es) saved" in text
    assert "sd_gis_hub: OK 26M" in text
    assert "GB free" in text
    assert "No Celery worker is running" in text


def test_next_run_catches_up_once():
    last = timezone.now() - timezone.timedelta(days=3)
    due = next_run("0 5 * * *", last)
    # three missed mornings make one run due, at the first missed time
    assert due < timezone.now() and due - last < timezone.timedelta(days=1)


def test_notifications_on_a_new_failure_and_at_three_in_a_row(dataset, settings, monkeypatch):
    settings.NOTIFY = True
    shown = []
    monkeypatch.setattr(notify, "notify", lambda title, message: shown.append(title) or True)

    def fail():
        job = create_job("harvest", dataset)
        with pytest.raises(RuntimeError):
            run_job(job, lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    for _ in range(4):
        fail()
    assert shown == ["sd-archive: harvest failed", "sd-archive: harvest failed 3 times in a row"]

    run_job(create_job("harvest", dataset), lambda: {})  # fixed
    fail()
    assert shown[-1] == "sd-archive: harvest failed", "a new failure after a success"


def test_notify_passes_text_as_arguments(settings, monkeypatch):
    settings.NOTIFY = True
    ran = []
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(notify.subprocess, "run", lambda args, **kw: ran.append(args))
    assert notify.notify('He said "hi"', "a 'quoted' label")
    assert ran[0][-2:] == ['He said "hi"', "a 'quoted' label"]
    settings.NOTIFY = False
    assert not notify.notify("x", "y")


def test_failure_streak_counts_stalled_runs(dataset):
    for status in ("succeeded", "failed", "stalled"):
        Job.objects.create(kind="harvest", label="x", target_type="census.dataset",
                           target_id=dataset.pk, status=status)  # fmt: skip
    latest = Job.objects.filter(kind="harvest").order_by("-created_at").first()
    assert notify.failure_streak(latest) == 2
