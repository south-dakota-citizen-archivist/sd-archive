from datetime import date
from pathlib import Path

import pytest

import sddocs
from sddocs import clean, forms, ocr

DATA = Path(__file__).parent / "data"
SPILL = DATA / "spill-2026.148.pdf"  # page 1 a filled-in form, page 2 a scanned letter


def scanned_pdf(path: Path, lines: list[str]) -> Path:
    """A one-page PDF that's only an image of text (no text layer)."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=56)
    for i, line in enumerate(lines):
        draw.text((150, 200 + i * 110), line, fill="black", font=font)
    img.save(path, "PDF", resolution=200)
    return path


def test_kind(tmp_path):
    assert sddocs.kind(SPILL) == "form"
    assert sddocs.kind(scanned_pdf(tmp_path / "s.pdf", ["BUILDING PERMIT"])) == "scanned"


def test_pages_choose_the_best_reading():
    p1, p2 = sddocs.pages(SPILL, ocr_engine=None)
    assert p1["method"] == "fields" and len(p1["fields"]) == 43
    assert "South Dakota Spill Report Form" in p1["text"] and p1["words"]
    assert p2["method"] == "empty"  # a scanned page, and no OCR asked for


@pytest.mark.skipif(not ocr.available("rapidocr"), reason="rapidocr isn't installed")
def test_scanned_page_by_ocr():
    [p2] = sddocs.pages(SPILL, only=[2])
    assert p2["method"] == "ocr" and p2["engine"] == "rapidocr"
    assert "NATURAL RESOURCES" in p2["text"] and p2["conf"] > 0.8
    w = p2["words"][0]
    assert 0 <= w["x0"] < w["x1"] <= 612 and 0 <= w["top"] < w["bottom"] <= 792  # PDF points


@pytest.mark.parametrize("engine", ["rapidocr", "tesseract", "vision", "livetext"])
def test_ocr_engines(engine, tmp_path):
    if not ocr.available(engine):
        pytest.skip(f"{engine} isn't available")
    pdf = scanned_pdf(tmp_path / "permit.pdf", ["BUILDING PERMIT 2024-0153", "Owner: Jane Smith"])
    [page] = sddocs.pages(pdf, ocr_engine=engine)
    assert page["method"] == "ocr"
    assert "2024-0153" in page["text"] and "Jane Smith" in page["text"]
    assert page["text"].index("PERMIT") < page["text"].index("Owner")  # top to bottom


def test_cache_resumes_by_page(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    first = list(sddocs.pages(SPILL, ocr_engine=None, cache=cache))
    monkeypatch.setattr(sddocs._pages.forms, "widgets", lambda page: 1 / 0)  # mustn't run
    assert list(sddocs.pages(SPILL, ocr_engine=None, cache=cache)) == first
    with pytest.raises(ZeroDivisionError):  # a new reader version reads the page again
        list(sddocs.pages(SPILL, ocr_engine=None, cache=cache, version=2))


def test_text():
    assert "Spill Report Form" in sddocs.text(SPILL, ocr_engine=None).split("\f")[0]


WORDS = [  # a line of a form: "Spill Date: (MM/DD/YY) [field]   ☑ UST  ☐ AST"
    {"text": "Spill", "x0": 20, "x1": 40, "top": 100, "bottom": 110},
    {"text": "Date:", "x0": 42, "x1": 62, "top": 100, "bottom": 110},
    {"text": "(MM/DD/YY)", "x0": 64, "x1": 100, "top": 100, "bottom": 110},
    {"text": "UST", "x0": 312, "x1": 330, "top": 100, "bottom": 110},
    {"text": "Pipeline", "x0": 312, "x1": 345, "top": 140, "bottom": 150},
    {"text": "Reported", "x0": 20, "x1": 60, "top": 40, "bottom": 50},
    {"text": "By:", "x0": 62, "x1": 75, "top": 40, "bottom": 50},
    {"text": "Material", "x0": 20, "x1": 60, "top": 130, "bottom": 140},
    {"text": "type", "x0": 62, "x1": 80, "top": 130, "bottom": 140},
]


def test_forms_by_position():
    field = {"x0": 105, "x1": 200, "top": 99, "bottom": 111}
    assert forms.label_left(WORDS, field) == "Spill Date"
    box = {"x0": 300, "x1": 310, "top": 100, "bottom": 110}
    assert forms.option_right(WORDS, box, ["AST", "UST"]) == "UST"
    starts = forms.section_starts(
        WORDS, [("reporter", ["Reported By"]), ("material", ["Material type"])]
    )
    assert [name for name, _ in starts] == ["reporter", "material"]
    assert forms.section_at(105, starts) == "reporter"
    assert forms.section_at(145, starts) == "material"
    assert forms.key("Spill Date (MM/DD/YY)") == "spill_date_mm_dd_yy"
    assert forms.text_value(b"\xfe\xff\x00H\x00i") == "Hi"


def test_clean():
    assert clean.fix_text("donâ€™t") == "don't"  # repaired, and its quote straightened
    assert clean.parse_date("3/4/21") == date(2021, 3, 4)
    assert clean.parse_date("March 4, 2021") == date(2021, 3, 4)
    assert clean.parse_date("not a date") is None


def test_compare_engines(tmp_path):
    lines = ["BUILDING PERMIT 2024-0153", "Owner: Jane Smith"]
    pdf = scanned_pdf(tmp_path / "permit.pdf", lines)
    usable = [e for e in ocr.engines() if ocr.available(e)][:2]
    if not usable:
        pytest.skip("no OCR engine available")
    rows = ocr.compare(pdf, [1], usable, reference={1: "\n".join(lines)})
    assert [r["engine"] for r in rows] == usable
    assert all(r["cer"] < 0.1 and r["seconds"] >= 0 for r in rows)


def test_error_rate():
    assert ocr.error_rate("Jane  Smith\n", "Jane Smith") == 0
    assert ocr.error_rate("Jane Smlth", "Jane Smith") == 0.1
