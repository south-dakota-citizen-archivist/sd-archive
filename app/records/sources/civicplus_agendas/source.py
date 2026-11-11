"""Meeting agendas, packets and minutes from the CivicPlus Agenda Center of every government
in the registry that has one (records.sources._governments): one record per meeting, with
its board, date, title, files and video. Each board's year lists are read; years before last
year are kept once read (their meetings don't change), this year's and last year's are read
again. The agenda, packet and minutes are the record's documents."""

from __future__ import annotations

from datetime import date
from urllib.parse import urlsplit

from records.sources import DocumentLink, Found, Item, Source
from records.sources._civicplus import agendas
from records.sources._governments import using

STATE = "0400000US46"
DOCUMENTS = ("agenda", "packet", "minutes")


def year_list(fetcher, base: str, host: str, category: str, year: int, today: date) -> bytes:
    store = getattr(fetcher, "store", None)
    name = f"agendas/{host}/{category}/{year}.html"
    fixed = year < today.year - 1
    if fixed and store is not None and store.has(name):
        return store.read(name)
    html = fetcher.request("POST", f"{base}/AgendaCenter/UpdateCategoryList",
                           data={"year": str(year), "catID": category}, timeout=120,
                           headers={"X-Requested-With": "XMLHttpRequest",
                                    "Referer": f"{base}/AgendaCenter"}).content  # fmt: skip
    if store is not None:
        store.save(name, html, f"{base}/AgendaCenter", method="POST")
    return html


def items(fetcher, today: date | None = None) -> Found:
    today = today or date.today()
    out, counts = Found(), {}
    for gov in using("civicplus"):
        base = gov["setting"].rstrip("/")
        host = urlsplit(base).hostname
        home = fetcher.request("GET", f"{base}/AgendaCenter", timeout=120).content
        boards = agendas.categories(home)
        n = 0
        for category, board in boards.items():
            for year in agendas.years(home, category):
                for meeting in agendas.meetings(year_list(fetcher, base, host, category, year,
                                                          today), base):  # fmt: skip
                    data = {**meeting, "government": gov["name"], "board": board,
                            "category_id": category}  # fmt: skip
                    docs = [DocumentLink(meeting["files"][k], metadata={"label": k.title()})
                            for k in DOCUMENTS if meeting["files"].get(k)]  # fmt: skip
                    url = meeting["files"].get("agenda") or f"{base}/AgendaCenter"
                    out.append(Item(key=f"{host}:{meeting['agenda_id']}", data=data,
                                    source_url=url, documents=docs,
                                    places=[STATE, gov["place"]]))  # fmt: skip
                    n += 1
        counts[gov["name"]] = {"boards": len(boards), "meetings": n}
    out.checks = {"governments": counts}
    return out


SOURCE = Source(
    slug="civicplus-agendas", items=items, ingest="documents",
    name="Meeting agendas and minutes (CivicPlus Agenda Center)",
    dataset="local-government-meetings", publisher="Local governments (CivicPlus sites)",
    description="Agendas, packets and minutes of every board of the governments that publish "
                "them in a CivicPlus Agenda Center.",
)  # fmt: skip
