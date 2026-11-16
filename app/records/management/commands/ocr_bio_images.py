import json

from django.core.management.base import BaseCommand

from records.sources.legislature_blue_book_bios.ocr import read_all


class Command(BaseCommand):
    help = "OCR the stored legislature biography images not yet read (resumable)"

    def handle(self, *args, **options):
        from records.sources._legislature.build import raw_root
        from sdfetch import Store

        self.stdout.write(json.dumps(read_all(Store(raw_root()))))
