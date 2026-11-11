"""open.sd.gov's grants and contracts: the search form, its results and a contract's summary
page. Pure."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from records.sources._tables import LayoutChanged, document, text

URL = "https://open.sd.gov/contracts.aspx"
TYPES = {"contract": "Radio2", "grant": "Radio3"}

__all__ = ["LayoutChanged", "departments", "form_fields", "results", "summary"]


def form_fields(html: bytes) -> dict[str, str]:
    root = document(html)
    fields = {i.get("name"): i.get("value") or "" for i in root.iter("input")
              if i.get("type") == "hidden" and i.get("name")}  # fmt: skip
    if "__VIEWSTATE" not in fields or not root.cssselect("select#ddl_DoA"):
        raise LayoutChanged("no search form")
    return fields


def departments(html: bytes) -> list[tuple[str, str]]:
    """(code as the form sends it, name) for each department; not "ALL", which the server
    can't answer."""
    out = []
    for o in document(html).cssselect("select#ddl_DoA option"):
        value, label = o.get("value"), text(o)
        if value and value != "0":
            out.append((value, label.rsplit(" (", 1)[0]))
    if not out:
        raise LayoutChanged("no departments in the form")
    return out


def _amount(value: str) -> str | None:
    try:
        return f"{Decimal(value.replace('$', '').replace(',', '')):.2f}"
    except InvalidOperation:
        return None


def results(html: bytes) -> list[dict]:
    """The grants or contracts a search found, each with its summary page's URL."""
    tables = document(html).cssselect("table#contracts")
    if not tables:
        return []
    out = []
    for tr in tables[0].cssselect("tr")[1:]:
        cells = [c for c in tr if c.tag == "td"]
        if len(cells) != 6:
            raise LayoutChanged(f"a result row has {len(cells)} cells")
        number, description, vendor, agency, begin, amount = (text(c) for c in cells)
        link = cells[0].find(".//a[@href]")
        code, _, name = agency.partition(" - ")
        out.append({
            "number": number, "list_description": description, "vendor_name": vendor,
            "agency": code.strip(), "agency_name": name.strip() or None,
            "begin_date": f"{begin[:4]}-{begin[4:6]}-{begin[6:]}" if len(begin) == 8 else begin,
            "amount": amount, "amount_value": _amount(amount),
            "summary_url": urljoin(URL, link.get("href")) if link is not None else None,
        })  # fmt: skip
    return out


SUMMARY = {"Vendor Name:": "vendor_name", "City:": "vendor_city", "State:": "vendor_state",
           "Solicitation Type:": "solicitation_type", "Description:": "description"}  # fmt: skip


def summary(html: bytes) -> dict:
    """A contract's summary page: the vendor's city and state, how it was solicited, the
    full description, and the contract document's URL."""
    root = document(html)
    out = {}
    for tr in root.cssselect("tr"):
        cells = [text(c) for c in tr if c.tag in ("td", "th")]
        if len(cells) == 2 and cells[0] in SUMMARY:
            out[SUMMARY[cells[0]]] = cells[1] or None
    if not out:
        raise LayoutChanged("no fields on the summary page")
    for a in root.iter("a"):
        href = a.get("href") or ""
        if "Document=" in href:
            out["document_url"] = href.split("Document=", 1)[1].replace("http://", "https://")
            break
    return out
