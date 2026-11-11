"""Read a Department of Revenue title and registration statistics PDF: the state totals page
and each county's page, every data line (a month 1–12 and the Total in a calendar-year
report; one line per year, "CY 2025", in the historical report), as published.

The column headings are checked on every page, so a change to the report's layout stops
the build instead of shifting numbers into the wrong columns."""

from __future__ import annotations

import io
import re

MEASURES = ["passenger_low_speed", "pickup_suv_van", "truck", "bus", "trailer", "motorcycle",
            "off_road", "recreational", "snowmobile", "boat", "commercial_plate",
            "gross_vehicle_weight", "irp_with_prorate_plate", "electric", "titles"]  # fmt: skip
# words of the column headings, in order, as they come out of the page's text
HEADINGS = ["Passenger", "Pickup", "Com-", "Gross", "Titles", "Motor-", "Off-", "Recrea-",
            "Snow-", "with", "Elec-", "/Low-", "/SUV/", "Truck", "Bus", "Trailer", "Boat",
            "mercial", "Vehicle", "cycle", "Road", "tional", "mobile", "Prorate", "tric",
            "Speed", "Van", "Plate", "Weight", "Plate"]  # fmt: skip
LINE = re.compile(r"^(?P<label>\d{1,2}|Total|CY\s+\d{4})(?P<numbers>(?:\s+[\d,]+){15})\s*$")
STATE = "State Totals"


class LayoutChanged(ValueError):
    pass


def _area(text: str) -> str | None:
    first = text.strip().splitlines()[0].strip() if text.strip() else ""
    if first == STATE:
        return STATE
    m = re.match(r"Titles & Registrations:\s*(.+)$", first)
    return m.group(1).strip() if m else None


def page_rows(text: str) -> tuple[str | None, list[dict]]:
    """(area, rows) of one page: the county's name (or "State Totals") and each data line."""
    area = _area(text)
    if area is None:
        return None, []
    lines = [line.strip() for line in text.splitlines()]
    start = next((i for i, line in enumerate(lines) if line.split()[:1] in (["Month"], ["Year"])),
                 None)  # fmt: skip
    end = next((i for i, line in enumerate(lines) if LINE.match(line)), len(lines))
    heading = " ".join(lines[start:end]).split()[1:] if start is not None else []
    if heading != HEADINGS:
        raise LayoutChanged(f"{area}: the column headings aren't the ones expected")
    rows = []
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        values = [int(v.replace(",", "")) for v in m["numbers"].split()]
        label = " ".join(m["label"].split())
        rows.append({"label": label, **dict(zip(MEASURES, values, strict=True))})
    return area, rows


def read(pdf_bytes: bytes) -> list[dict]:
    """Every data line of every state or county page: area, page number, label, measures."""
    import pdfplumber

    out = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            area, rows = page_rows(page.extract_text() or "")
            out += [{"area": area, "page": page.page_number, **r} for r in rows]
    return out
