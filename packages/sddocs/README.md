# sddocs

Reading documents: each PDF page by its form fields, its text layer or OCR, with every page's reading cached; fillable forms read by position; several OCR engines and a way to compare them; tables; text and date clean-up. Plain Python, no Django.

```python
import sddocs

sddocs.kind("report.pdf")  # "form", "native", "scanned", "mixed" or "empty"

for page in sddocs.pages("report.pdf", cache=".cache/pages", version=1):
    page["n"], page["method"], page["text"]  # method: "fields", "text", "ocr" or "empty"
```

## Pages

`pages()` reads each page the best way it allows:
- **`fields`:** a filled-in form. `fields` lists every text field with a value and every checked box, with its position. The text comes from the page's text layer.
- **`text`:** a page with a text layer.
- **`ocr`:** a scanned page, read by `ocr_engine` (rapidocr by default). `conf` is the engine's mean confidence.
- **`empty`:** a scanned page, with no OCR available or asked for.

`words` holds positioned pieces in PDF points from the page's top left, the same as pdfplumber's.

**Caching:** with `cache=`, each page's reading is kept in a file keyed by the PDF's SHA-256, the page, the reader `version` and the OCR engine, so an interrupted run picks up where it stopped. Bump `version` when what you keep changes, and every page is read again.

## OCR

| engine | what | needs |
|---|---|---|
| `rapidocr` | PaddleOCR's models on ONNX Runtime; CPU, no system packages | the `ocr` extra |
| `tesseract` | the `tesseract` command | tesseract |
| `vision`, `livetext` | Apple's Vision and LiveText text recognition | macOS, the `macos` extra |
| `textra` | Apple's Vision through the `textra` command | macOS, textra |

- `ocr.register_engine(name, run, available)` adds another engine.
- `ocr.compare(pdf, pages, engines, reference=)` runs engines on the same pages and reports seconds, characters, mean confidence and, against hand-checked text, the character error rate.
- `ocr.searchable(pdf, out)` adds a text layer to a scanned PDF (ocrmypdf; the `searchable` extra).

## Forms, tables and text

- **`sddocs.forms`** reads fillable forms by position, because field names differ between versions of a form:
  - `widgets(page)` lists the filled-in fields
  - `label_left()` finds the label printed left of a text field
  - `option_right()` finds the option printed right of a checked box
  - `section_starts()` and `section_at()` find which section of the form a field is in
- **`sddocs.tables`:**
  - `tables(page)` returns every table on a page as rows of cleaned cells, ruled tables by default and tables laid out by spacing with `TEXT`
  - `rows_above()` reads a header printed outside a table's ruling
- **`sddocs.clean`** (the `text` extra): `fix_text()` repairs garbled encodings, and `parse_date()` reads US dates in any format.

## Use from another repo

```toml
[project]
dependencies = ["sddocs[ocr]"]

[tool.uv.sources]
sddocs = { git = "https://github.com/south-dakota-citizen-archivist/sd-archive", subdirectory = "packages/sddocs", tag = "sddocs-vX.Y.Z" }
```
