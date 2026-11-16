import json

from django.core.management.base import BaseCommand

from records.sources.legislature_blue_book_bios.images import fetch_images


class Command(BaseCommand):
    help = "Download the legislature's biography page images not yet stored (resumable)"

    def add_arguments(self, parser):
        parser.add_argument("members", nargs="*", type=int, help="MemberIds (default: all)")

    def handle(self, *args, members, **options):
        from records.sources._legislature.build import raw_root
        from sdfetch import Fetcher

        counts = fetch_images(Fetcher(raw_root()), members or None)
        self.stdout.write(json.dumps({**counts, "failed": counts["failed"][:50],
                                      "n_failed": len(counts["failed"])}, indent=1))  # fmt: skip
