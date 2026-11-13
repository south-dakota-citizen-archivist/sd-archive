"""Reading what a map app sends: the map configuration, vector features and report pages."""

from __future__ import annotations

import json
import re
from functools import cache
from urllib.parse import urlsplit, urlunsplit

from records.sources._tables import LayoutChanged, document

# Report sections about the county or the site, not the feature
SKIP_SECTIONS = {"Contact Information", "Online Tax Payments"}
_YEAR = re.compile(r"^\d{4}$")
_TOGGLES = {"", "+", "-", "−", "="}  # expand/collapse and total markers


def map_config(html: str | bytes) -> dict:
    """The `mapConfig = {...}` object a map or report page defines."""
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    start = html.find("mapConfig = ")
    if start < 0:
        raise LayoutChanged("no mapConfig on the page")
    config, _ = json.JSONDecoder().raw_decode(html[start + len("mapConfig = ") :])
    return config


def report_url(config: dict, key: str) -> str:
    """A feature's report page, from DefaultReportUrl (its Q= parameter left out)."""
    from urllib.parse import quote

    from records.sources._map_app import BASE_URL

    template = config.get("DefaultReportUrl")
    if not template:
        raise LayoutChanged("the map configuration has no DefaultReportUrl")
    template = re.sub(r"&Q=-?\d+", "", template)
    return BASE_URL + template.replace("{0}", quote(key, safe=""))


def stable_url(url: str) -> str:
    """A blob URL without its query (the signature and expiry change on every request)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _text(el) -> str:
    return " ".join(el.text_content().split())


def tooltip(html: str | None) -> dict:
    """A feature's tooltip: its labeled values ("Owner": "...") and photo."""
    if not html or not html.strip():
        return {}
    root = document(f"<div>{html}</div>")
    out: dict = {}
    for b in root.iter("b"):
        label = _text(b)
        value = (b.tail or "").lstrip(":").strip()
        if label:
            out[label] = value
    photos = [stable_url(img.get("src")) for img in root.iter("img") if img.get("src")]
    if photos:
        out["photo"] = photos[0]
    return out


def features(answer: list[dict]) -> list[dict]:
    """GetVectorLayer's features: key, WKT geometry (in the map's projection), tooltip."""
    return [{"key": f["Key"], "display_key": f.get("DisplayKey") or f["Key"],
             "wkt": f.get("WktGeometry") or "", "tooltip": tooltip(f.get("TipHtml"))}
            for f in answer]  # fmt: skip


@cache
def _transformer(srid: int):
    from pyproj import Transformer

    source = f"ESRI:{srid}" if srid >= 100000 else f"EPSG:{srid}"
    return Transformer.from_crs(source, "EPSG:4326", always_xy=True)


def geometry(wkt: str, srid: int) -> bytes | None:
    """WKB of a feature's geometry in longitude/latitude, from WKT in the map's projection."""
    if not wkt:
        return None
    import numpy as np
    import shapely

    move = _transformer(srid).transform
    shape = shapely.transform(shapely.from_wkt(wkt),
                              lambda xy: np.column_stack(move(xy[:, 0], xy[:, 1])))  # fmt: skip
    return shapely.to_wkb(shape, output_dimension=2)


# --- report pages ---


def _two_column(table) -> dict:
    out = {}
    for tr in table.iter("tr"):
        cells = [td for td in tr if td.tag in ("td", "th")]
        if len(cells) >= 2:
            label = _text(cells[0])
            if label:
                out[label] = _text(cells[1])
    return out


def _grid(table) -> list[dict]:
    rows = [[_text(td) for td in tr if td.tag in ("td", "th")] for tr in table.iter("tr")]
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    # a column of markers ahead of the data ("+" to expand a row, "=" before a total)
    while len(rows[0]) > 1 and all((r[0] if r else "") in _TOGGLES for r in rows):
        rows = [r[1:] for r in rows]
    head = rows[0]
    if _YEAR.match(head[0]) and len(head) > 1:
        # one table per year, its rows labeled ("2025 | 1st Half | 2nd Half | Full Year")
        return [{"year": head[0], "item": r[0], **dict(zip(head[1:], r[1:], strict=False))}
                for r in rows[1:]]  # fmt: skip
    if len(head) > 1 and all(_YEAR.match(c) for c in head[1:]):
        # one column per year ("Assessed Year | 2026 | 2025 …"): a row per year
        return [{head[0] or "year": year, **{r[0]: r[i] for r in rows[1:] if len(r) > i}}
                for i, year in enumerate(head[1:], start=1)]  # fmt: skip
    return [dict(zip(head, r, strict=False)) for r in rows[1:]]


_OWNER_ROW = re.compile(r"_(?:rptOwner|lstOwner)_ctl(\d+)_(.+)$")


def _owners(section) -> list[dict]:
    """Owners, from either kind of owner list an app renders: a repeater of name spans and
    search links with an address block (…_rptOwner_ctl00_…), or a table of labeled spans
    (…_lstOwner_ctl00_NameLabel, Address1Label…, ZipCodeLabel)."""
    rows: dict[int, dict] = {}
    for el in section.iter("span", "a"):
        m = _OWNER_ROW.search(el.get("id") or "")
        if not m:
            continue
        owner = rows.setdefault(int(m.group(1)), {"names": [], "address": []})
        part, text = m.group(2), _text(el)
        if part in ("lblOwnerType", "NameTypeLabel"):
            if text:
                owner["type"] = text
        elif part == "lblOwnerAddress":
            owner["address"] = [x for x in (" ".join(t.split()) for t in el.itertext()) if x]
        elif part.startswith("Address") and part.endswith("Label"):
            if text:
                owner["address"].append(text)
        elif part == "ZipCodeLabel":
            if text and owner["address"]:
                owner["address"][-1] = f"{owner['address'][-1]} {text}"
            elif text:
                owner["address"].append(text)
        elif (part.endswith(("_lblSearch", "_lnkSearch")) or part.startswith("NameLabel")) and (
            text and text not in owner["names"]
        ):
            # a name is plain text (…_lblSearch, NameLabel) or a search link (…_lnkSearch)
            owner["names"].append(text)
    return [rows[i] for i in sorted(rows)]


def _lines(section) -> list[str]:
    """A section without tables, as text: its list items, or else its lines."""
    content = section.cssselect(".module-content")
    if not content:
        return []
    for junk in content[0].iter("script", "style"):
        junk.drop_tree()
    items = [_text(li) for li in content[0].iter("li")]
    if items:
        return [i for i in items if i]
    text = "\n".join(content[0].itertext())
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def report(html: str | bytes) -> dict:
    """A report page, section by section, whatever sections a county shows:

    - a two-column table as {label: value}; a grid as rows; a table with a column per year
      as a row per year; a table headed by a year as its rows, each with the year
    - a section without tables (a list of properties in a sale) as its lines of text
    - the owners (names, address lines and, where given, the kind of owner), the photos,
      and the sections that need an account to see

    Search forms (sections with inputs) and sections about the site are left out."""
    root = document(html)
    sections = root.cssselect('section[id$="_mSection"]')
    if not sections and not root.cssselect(".module-header"):
        raise LayoutChanged("not a report page")
    out: dict = {"sections": {}, "owners": [], "photos": [], "restricted": []}
    for section in sections:
        title_el = section.cssselect(".module-header .title")
        title = _text(title_el[0]) if title_el else ""
        if not title or title in SKIP_SECTIONS:
            continue
        if section.cssselect('[id$="_pnlRestricted"]'):
            out["restricted"].append(title)
            continue
        if section.cssselect('[id*="_rptOwner_"], [id*="_lstOwner_"]'):
            out["owners"] = _owners(section)
            continue
        if section.cssselect("input, select"):
            continue  # a search form (recent sales nearby), not the feature's data
        if not section.cssselect("table") and section.cssselect('a[href*="/Account/"]'):
            continue  # log-in and subscription buttons
        tables = []
        for table in section.iter("table"):
            if table.getparent() is not None and table.getparent().tag in ("td",):
                continue  # a table inside a cell is part of its parent's value
            cls = table.get("class") or ""
            parsed = _two_column(table) if "two-column" in cls else _grid(table)
            if parsed:
                tables.append(parsed)
        out["photos"] += [stable_url(img.get("src")) for img in section.iter("img")
                          if (img.get("src") or "").startswith("http")]  # fmt: skip
        if len(tables) > 1 and all(isinstance(t, list) for t in tables):
            out["sections"][title] = [row for t in tables for row in t]  # a table per year
        elif tables:
            out["sections"][title] = tables[0] if len(tables) == 1 else tables
        elif lines := _lines(section):
            out["sections"][title] = lines
    out["photos"] = list(dict.fromkeys(out["photos"]))
    empty = root.xpath('//*[contains(text(), "No data available for the following modules")]')
    if empty:
        names = _text(empty[0]).split(":", 1)[-1]
        out["empty"] = [n.strip(" .") for n in names.split(",") if n.strip(" .")]
    return out
