"""State employees' positions and pay, from open.sd.gov's salary lookup.

The lookup only finds an exact last name, so the source searches every family name the
archive knows: the phone directory's, earlier salary records', and every person mentioned in
any record set. Each search includes former employees (shown with their final wage).

The site answers about 40 searches a day from one address, then shows the page without its
search form for about a day. So each run makes at most SEARCHES_PER_RUN searches, paced,
and stops early (keeping what it found) if the site refuses. Names go never-searched first,
then oldest-searched, skipping any searched in the last REFRESH_DAYS; each good response is
kept at media/raw/state-employee-salaries/searches/<date>/<name>.html, which is also how a
run knows when a name was last searched. Runs are partial (complete=False): a name nobody
searched this time doesn't mean its employees left.

Current employees are keyed by position number and name; former ones, who have no position
number, by name, pay basis and final wage.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from entities.mentions import from_text
from records.sources import Item, Source, Stopped

from . import parse

logger = logging.getLogger(__name__)

URL = "https://open.sd.gov/employees.aspx"
STATE = "0400000US46"
DELAY = 5.0  # seconds between searches
SEARCHES_PER_RUN = 35  # under the ~40 a day the site allows one address
REFRESH_DAYS = 90  # search a name again after this long


def key(s) -> str:
    name = f"{s.last_name}, {s.first_name} {s.middle_initial or ''}".strip()
    if s.active:
        return f"{s.position_number} {name}"
    return f"former {name} {s.pay_basis} {s.pay}"


def surnames() -> list[str]:
    """Every family name the archive knows, once each (case-insensitive), sorted."""
    from entities.models import Mention
    from records.models import Record

    names = set()
    # the directory's family_name, and last_name on records kept from before it was parsed
    for slug, field in (("state-employee-directory", "family_name"),
                        ("state-employee-directory", "last_name"),
                        ("state-employee-salaries", "last_name")):  # fmt: skip
        names.update(Record.objects.filter(recordset__slug=slug)
                     .values_list(f"data__{field}", flat=True))  # fmt: skip
    for props in Mention.objects.filter(schema="Person").values_list("properties", flat=True):
        names.update(props.get("lastName") or [])
    out: dict[str, str] = {}
    for n in sorted(" ".join(str(n or "").split()) for n in names):
        if n and re.search(r"[A-Za-z]", n) and len(n) <= 50:
            have = out.get(n.casefold())
            # one spelling per name, preferring "Rhoden" to "rhoden" or "RHODEN"
            if have is None or (n[:1].isupper() and not n.isupper() and have.islower()):
                out[n.casefold()] = n
    return sorted(out.values(), key=str.casefold)


def slug_for(last_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", last_name.casefold()).strip("-") or "x"


def last_searched(store) -> dict[str, date]:
    """When each name was last searched, from the stored responses."""
    out: dict[str, date] = {}
    root = store.root / "searches"
    for path in root.glob("*/*.html") if root.exists() else []:
        try:
            on = date.fromisoformat(path.parent.name)
        except ValueError:
            continue
        out[path.stem] = max(on, out.get(path.stem, on))
    return out


def latest_responses(store) -> dict[str, bytes]:
    """The latest stored response for each name searched."""
    newest: dict[str, tuple[date, object]] = {}
    root = store.root / "searches"
    for path in root.glob("*/*.html") if root.exists() else []:
        try:
            on = date.fromisoformat(path.parent.name)
        except ValueError:
            continue
        if path.stem not in newest or on > newest[path.stem][0]:
            newest[path.stem] = (on, path)
    return {name: path.read_bytes() for name, (_, path) in newest.items()}


def _add(out: dict[str, Item], s) -> None:
    k, i = key(s), 1
    while k in out and out[k].data != s.model_dump(mode="json"):
        i += 1
        k = f"{key(s)} ({i})"
    out[k] = Item(key=k, data=s.model_dump(mode="json"), source_url=URL, places=[STATE])


def queue(names: list[str], searched: dict[str, date], today: date) -> list[str]:
    """Names to search, never-searched first (in name order), then oldest-searched; names
    searched within REFRESH_DAYS wait."""
    due = [n for n in names if (on := searched.get(slug_for(n))) is None
           or today - on >= timedelta(days=REFRESH_DAYS)]  # fmt: skip
    return sorted(due, key=lambda n: (searched.get(slug_for(n), date.min), n.casefold()))


def search(fetcher, fields: dict, last_name: str) -> bytes:
    data = {**fields, "tb_LastName": last_name, "tb_FirstName": "", "cb_Inactive": "on",
            "btn_Employee": "Display Employee"}  # fmt: skip
    return fetcher.request("POST", URL, data=data).content


def items(fetcher, names: list[str] | None = None, *, budget: int = SEARCHES_PER_RUN,
          today: date | None = None) -> list[Item]:  # fmt: skip
    today = today or date.today()
    if hasattr(fetcher, "delays"):
        fetcher.delays = {**(fetcher.delays or {}), "open.sd.gov": DELAY}
    todo = queue(surnames() if names is None else names, last_searched(fetcher.store), today)
    # every name's latest stored search, so a search stored by a run that then failed is
    # still recorded; this run's searches replace them
    out: dict[str, Item] = {}
    searching = {slug_for(n) for n in todo[:budget]}
    for slug, content in latest_responses(fetcher.store).items():
        if slug not in searching:
            for s in parse.rows(content):
                _add(out, s)
    if not todo:
        return list(out.values())
    try:
        fields = parse.form_fields(fetcher.request("GET", URL).content)
    except parse.Blocked as e:
        raise Stopped(out.values(), f"{e}; {len(todo)} names wait (try again tomorrow)") from None
    for n, last_name in enumerate(todo[:budget], 1):
        content = search(fetcher, fields, last_name)
        try:
            found = parse.rows(content)
        except parse.Blocked as e:
            raise Stopped(out.values(), f"{e} after {n - 1} searches; "
                                        f"{len(todo) - n + 1} names wait") from None  # fmt: skip
        fetcher.store.save(f"searches/{today.isoformat()}/{slug_for(last_name)}.html", content,
                           URL, method="POST", note={"last_name": last_name})  # fmt: skip
        for s in found:
            _add(out, s)
    if len(todo) > budget:
        raise Stopped(out.values(), f"searched {budget} names (a day's budget); "
                                    f"{len(todo) - budget} wait")  # fmt: skip
    return list(out.values())


def mentions(record) -> list:
    d = record.data
    name = f"{d['first_name']} {d['middle_initial'] or ''} {d['last_name']}"
    return from_text(name, dataset=record.recordset.dataset, record_key=str(record),
                     field="name", role="state employee", order="given_first",
                     url=record.source_url,
                     context={"agency": d["agency"], "title": d["title"]})  # fmt: skip


def from_legacy(data: dict) -> dict:
    """The liberator kept current employees' rows, without agency."""
    return {"position_number": data.get("position_number"), "last_name": data.get("last_name"),
            "first_name": data.get("first_name"), "middle_initial": data.get("middle_initial"),
            "title": data.get("title"), "pay": data.get("salary"),
            "pay_basis": data.get("pay_basis")}  # fmt: skip


def legacy_key(record) -> str:
    from types import SimpleNamespace

    d = from_legacy(record.data)
    return key(SimpleNamespace(active=True, **d))


SOURCE = Source(slug="state-employee-salaries", items=items, mentions=mentions, complete=False,
                from_legacy=from_legacy, legacy_key=legacy_key)  # fmt: skip
