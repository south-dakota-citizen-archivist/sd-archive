"""Copy the south-dakota-legislature-data repository's files into the raw store.

Its JSON (1997–2024) holds values the API no longer serves, such as legislators' home
addresses, so the legislature record sets read it alongside the crawl. Every committed file
under data/ and crawler/ is stored at media/raw/legislature-archive/<its path>, recorded
with its link on GitHub at the commit it was copied from.
"""

import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

REPO_URL = "https://github.com/cjwinchester/south-dakota-legislature-data"


def archive_root() -> Path:
    return Path(settings.MEDIA_ROOT) / "raw" / "legislature-archive"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout  # fmt: skip


class Command(BaseCommand):
    help = "Copy the legislature data repository's committed files into the raw store"

    def add_arguments(self, parser):
        parser.add_argument("repo", help="a clone of south-dakota-legislature-data")

    def handle(self, *args, repo, **options):
        from sdfetch import Store

        repo = Path(repo).expanduser()
        if git(repo, "status", "--porcelain", "--", "data", "crawler").strip():
            raise CommandError(f"{repo}: uncommitted changes under data/ or crawler/")
        commit = git(repo, "rev-parse", "HEAD").strip()
        committed = git(repo, "log", "-1", "--format=%cI").strip()
        store = Store(archive_root())
        files = [f for f in git(repo, "ls-files", "data", "crawler").splitlines() if f]
        copied = same = 0
        for name in files:
            body = (repo / name).read_bytes()
            if store.has(name) and store.read(name) == body:
                same += 1
                continue
            store.save(name, body, f"{REPO_URL}/blob/{commit}/{name}", allow_empty=True,
                       note={"commit": commit, "committed": committed})  # fmt: skip
            copied += 1
        self.stdout.write(f"  {copied} copied, {same} already stored, of {len(files)} files "
                          f"(commit {commit[:8]}, {committed})")  # fmt: skip
