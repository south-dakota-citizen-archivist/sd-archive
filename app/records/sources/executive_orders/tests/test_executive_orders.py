"""Executive orders: the page's own tables of orders since 2020 (a real page, trimmed)."""

from pathlib import Path

from records.sources.executive_orders import source

PAGE = (Path(__file__).parent / "fixtures" / "page.html").read_bytes()


def test_the_tables():
    rows = source.tables(PAGE)
    first = rows[0]
    assert first["cells"] == {"Order Number": "2026-01", "Date Filed": "20260107",
                              "Title": "Windstorm Declaration"}  # fmt: skip
    assert first["url"].endswith("/assets/2026%20Executive%20Orders/2026-01.pdf")
    rescind = next(r for r in rows if r["cells"]["Order Number"] == "2025-08")
    assert len(rescind["files"]) == 4  # the order and the three sets it rescinds
