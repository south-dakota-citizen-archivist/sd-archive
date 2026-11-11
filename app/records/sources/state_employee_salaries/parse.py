"""The salary lookup's results table → rows. Pure: no fetching, no database."""

from __future__ import annotations

from records.sources._tables import LayoutChanged, document, text

from .schema import Salary

__all__ = ["Blocked", "LayoutChanged", "form_fields", "rows"]


class Blocked(LayoutChanged):
    """The site's answer to too many searches: the page without its search form. It lasts
    about a day for this address."""


# Each header cell also holds its tooltip ("Pos #Position Positions are defined by…")
COLUMNS = ["Pos #", "Last Name", "First Name", "MI", "Agency", "Title", "Salary/Wage",
           "Pay Basis"]  # fmt: skip
INACTIVE = "Inactive"


def form_fields(html: str | bytes) -> dict[str, str]:
    """The search form's hidden fields (view state), to post a search back."""
    root = document(html)
    fields = {i.get("name"): i.get("value") or "" for i in root.iter("input")
              if i.get("type") == "hidden" and i.get("name")}  # fmt: skip
    if not root.cssselect("input#tb_LastName"):
        raise Blocked("the page has no search form: the site is refusing searches")
    if "__VIEWSTATE" not in fields:
        raise LayoutChanged("the search form has no view state")
    return fields


def _amount(pay: str) -> float | None:
    try:
        return float(pay.replace("$", "").replace(",", ""))
    except ValueError:
        return None


def rows(html: str | bytes) -> list[Salary]:
    """The employees a search found; [] when there were none."""
    root = document(html)
    if not root.cssselect("input#tb_LastName"):
        raise Blocked("the results page has no search form: the site is refusing searches")
    tables = root.cssselect("table#stateemployeesalarylookup")
    if not tables:
        return []
    trs = tables[0].cssselect("tr")
    header = [text(c) for c in trs[0] if c.tag in ("td", "th")]
    if len(header) != len(COLUMNS) or not all(
        h.startswith(c) for h, c in zip(header, COLUMNS, strict=True)
    ):
        raise LayoutChanged(f"columns are {[h[:20] for h in header]}")
    out = []
    for n, tr in enumerate(trs[1:], start=2):
        cells = [c for c in tr if c.tag in ("td", "th")]
        if len(cells) != len(COLUMNS):
            raise LayoutChanged(f"row {n} has {len(cells)} cells")
        pos, last, first, mi, _, title, pay, basis = (text(c) for c in cells)
        code = cells[4].cssselect("a.title")
        definition = cells[4].cssselect("div.definition")
        agency = None
        if definition:
            heading = definition[0].cssselect("h4")
            agency = text(definition[0])[len(text(heading[0])) if heading else 0 :].strip()
        active = pos != INACTIVE
        out.append(Salary(
            active=active, position_number=pos if active else None, last_name=last,
            first_name=first, middle_initial=mi or None,
            agency_code=(text(code[0]) or None) if code else None,
            agency=agency if agency and agency != "No definition found." else None,
            title=title if active else None, pay=pay, pay_amount=_amount(pay), pay_basis=basis,
        ))  # fmt: skip
    return out
