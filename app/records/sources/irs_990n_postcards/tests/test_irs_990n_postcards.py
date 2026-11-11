"""990-N e-Postcards: real lines of the IRS's file (checked by hand), and a run."""

import io
import zipfile
from pathlib import Path

from entities.models import Mention
from records.models import Record, RecordSet
from records.run import run_source
from records.sources.irs_990_returns.source import EO_SD
from records.sources.irs_990n_postcards import source

TEXT = (Path(__file__).parent / "fixtures" / "epostcard-sample.txt").read_text()


def zipped() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("data-download-epostcard.txt", TEXT)
    return buf.getvalue()


def test_postcards():
    cards = list(source.postcards(TEXT))
    assert len(cards) == 9  # the file's two blank lines skipped
    abate = cards[1]
    assert (abate["ein"], abate["tax_year"]) == ("010635931", "2024")
    assert abate["name"] == "RUSHMORE CHAPTER OF ABATE INC"
    assert abate["officer_name"] == "Tina McCollar" and abate["mailing_city"] == "Rapid City"


class FixtureFetcher:
    store = None

    def request(self, method, url, **kwargs):
        body = zipped() if url == source.URL else b"EIN,NAME\n010844177,USBC\n"
        assert url in (source.URL, EO_SD)
        return type("R", (), {"content": body})()


def test_run(db, sd_places):
    RecordSet.objects.create(slug="irs-990n-postcards", name="990-N",
                             dataset="irs-990n-postcards")  # fmt: skip
    run = run_source("irs-990n-postcards", fetcher=FixtureFetcher())
    assert run.added == 8  # not the Montana club
    card = Record.objects.get(key="010635931 2024-01-01 2024-12-31")
    assert card.data["period_end"] == "2024-12-31" and card.data["terminated"] is False
    assert card.data["gross_receipts_not_over_50000"] is True
    assert card.places.filter(short_name="Rapid City").exists()
    assert Record.objects.get(key__startswith="043824003").data["terminated"] is True
    toastmasters = Mention.objects.filter(record_key__startswith="irs-990n-postcards:010638398")
    assert list(toastmasters.values_list("role", flat=True)) == ["organization"]  # "None"
    officer = Mention.objects.get(record_key="irs-990n-postcards:010635931 2024-01-01 2024-12-31",
                                  role="principal officer")  # fmt: skip
    assert officer.original == "Tina McCollar"
