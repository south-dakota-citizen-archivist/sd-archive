import json

from django.core.management.base import BaseCommand

from records.sources.legislature_blue_book_bios.ocr import ENGINE, TIEBREAK, read_all


class Command(BaseCommand):
    help = "OCR the stored legislature biography images not yet read (resumable)"

    def add_arguments(self, parser):
        parser.add_argument("--engine", default=ENGINE, help=f"default {ENGINE}")
        parser.add_argument("--disputed", action="store_true",
                            help=f"only Blue Book pages whose readings disagree (with {TIEBREAK}: "
                                 "the third reading)")  # fmt: skip

    def handle(self, *args, engine, disputed, **options):
        from records.sources._legislature.build import raw_root
        from sdfetch import Store

        images = None
        if disputed:
            from records.sources.legislature_blue_book_bios import source

            images = sorted(i.key for i in source.items() if i.data.get("unconfirmed"))
            self.stdout.write(f"{len(images)} pages with readings that disagree")
        self.stdout.write(json.dumps(read_all(Store(raw_root()), engine, images)))
