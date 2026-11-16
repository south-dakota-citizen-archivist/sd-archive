import pandas as pd

from sddocs.links import page_url, with_page_links


def test_a_pdf_link_opens_at_its_page():
    assert page_url("https://x.gov/report.pdf", 3) == "https://x.gov/report.pdf#page=3"
    assert page_url("https://x.gov/report.PDF?v=2", 4.0) == "https://x.gov/report.PDF?v=2#page=4"
    assert page_url("https://x.gov/report.pdf#page=9", 2) == "https://x.gov/report.pdf#page=2"
    assert page_url("https://x.gov/recap.xlsx", 2) == "https://x.gov/recap.xlsx"  # not a PDF
    assert page_url("https://x.gov/report.pdf", float("nan")) == "https://x.gov/report.pdf"


def test_a_tables_rows_open_at_their_pages():
    df = pd.DataFrame({"source_url": ["https://x.gov/a.pdf", "https://x.gov/b.xlsx"],
                       "page": [5, None]})  # fmt: skip
    assert with_page_links(df)["source_url"].tolist() == ["https://x.gov/a.pdf#page=5",
                                                         "https://x.gov/b.xlsx"]  # fmt: skip
    assert "source_url" in with_page_links(pd.DataFrame({"source_url": ["a"]}))
