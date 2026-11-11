"""A committee's page on sdcfr.sdsos.gov → the committee and its filings. Pure."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlsplit

from records.sources._tables import LayoutChanged, document, text
from sddocs.clean import parse_date

__all__ = ["LayoutChanged", "cfemail", "committee"]

PREFIX = "MainContent_SchRsltCommitteInfo1_"
FIELDS = {
    "committee_name": "lblciHeader", "candidate_name": "lblCandidateNameTop",
    "status": "lblStatus", "committee_type": "lblciType", "address": "lblciAddress",
    "phone": "lblciTelephoneNumber", "website": "lblciWebsiteURL",
    "chair_name": "lblciChair2", "chair_address": "lblciChairAddress2",
    "chair_phone": "lblciChairPhone2", "chair_email": "lblciChairEmail2",
    "treasurer_name": "lblciTreasure2", "treasurer_address": "lblciTreasurerAddress2",
    "treasurer_phone": "lblciTreasurerPhone2", "treasurer_email": "lblciTreasurerEmail2",
}  # fmt: skip
SECTIONS = {"Uploaded/Paper Filings": "uploaded", "Committee Filings": "online"}


def cfemail(encoded: str) -> str:
    """An address hidden by Cloudflare's email protection (data-cfemail: a key byte, then the
    address's bytes XORed with it)."""
    data = bytes.fromhex(encoded)
    return bytes(b ^ data[0] for b in data[1:]).decode("utf-8", errors="replace")


def _value(el) -> str | None:
    hidden = el.cssselect("[data-cfemail]")
    if hidden:
        return cfemail(hidden[0].get("data-cfemail"))
    value = text(el)
    return None if value in ("", "(none)") else value


def committee(html: str | bytes, url: str) -> tuple[dict, list[dict]]:
    """The committee's details, and its filings in page order: label, report, filed date,
    whether uploaded (scanned) or filed online, and the document URL."""
    root = document(html)
    info = {}
    for field, suffix in FIELDS.items():
        found = root.cssselect(f"#{PREFIX}{suffix}")
        info[field] = _value(found[0]) if found else None
    if not info["committee_name"]:
        raise LayoutChanged(f"{url}: no committee name")
    if info["candidate_name"]:
        info["candidate_name"] = (
            info["candidate_name"].removeprefix("Candidate Name:").strip() or None
        )
    query = parse_qs(urlsplit(url).query)
    info["cid"], info["rid"] = query.get("cid", [None])[0], query.get("rid", [None])[0]

    filings, section = [], None
    for el in root.iter():
        label = text(el) if el.tag in ("td", "span", "th") and len(el) <= 1 else ""
        if label in SECTIONS:
            section = SECTIONS[label]
            continue
        if el.tag != "tr":
            continue
        link = next((a for a in el.iter("a") if "Document.aspx" in (a.get("href") or "")), None)
        if link is None or el.xpath(".//tr"):  # a row holding a nested table
            continue
        cells = [text(td) for td in el.findall("td")]
        href = urljoin(url, link.get("href"))
        doc_id = parse_qs(urlsplit(href).query).get("DocumentID", [None])[0]
        filed = re.sub(r"\s+", " ", cells[-1]) if cells else ""
        on = parse_date(filed) if filed else None
        filings.append({
            "document_id": doc_id, "url": href, "label": text(link),
            "report": cells[1] if len(cells) >= 3 else None,
            "filed": filed, "filed_on": on.isoformat() if on else None,
            "kind": section or "unknown",
            "format": parse_qs(urlsplit(href).query).get("type", [None])[0],
        })  # fmt: skip
    return info, filings
