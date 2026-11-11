from django.core.management.base import BaseCommand

from records.models import Document


class Command(BaseCommand):
    help = (
        "Send failed documents back to the step they failed at (a file that was never "
        "stored is fetched again); `manage.py documents <slug>` then runs them"
    )

    def add_arguments(self, parser):
        parser.add_argument("--recordset", help="only this record set (slug)")
        parser.add_argument("--limit", type=int, help="at most this many documents")
        parser.add_argument("--dry-run", action="store_true", help="list them, change nothing")

    def handle(self, *args, recordset, limit, dry_run, **options):
        failed = Document.objects.filter(status=Document.Status.FAILED).order_by("pk")
        if recordset:
            failed = failed.filter(record__recordset__slug__iexact=recordset)
        if limit:
            failed = failed[:limit]
        docs = list(failed.select_related("record__recordset"))
        if not docs:
            self.stdout.write("No failed documents.")
            return
        for doc in docs:
            back = retry_status(doc)
            first = (doc.error or "").splitlines()[0][:100] if doc.error else ""
            self.stdout.write(f"  {doc.pk}  {doc.record.recordset.slug}  → {back}  {first}")
            if not dry_run:
                Document.objects.filter(pk=doc.pk).update(status=back, error="")
        verb = "Would retry" if dry_run else "Retrying"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(docs)} document(s)."))


def retry_status(doc: Document) -> str:
    """The step a failed document goes back to: fetch without a file, read without text,
    categorize without a category, else extract."""
    if not doc.path and not doc.legacy.get("prl_file"):
        return Document.Status.PENDING
    if not doc.text:
        return Document.Status.STORED
    if not doc.category_method:
        return Document.Status.READ
    return Document.Status.CATEGORIZED
