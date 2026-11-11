"""State grants and contracts, from open.sd.gov (SDCL 1-56-10 and 1-27-46): every grant and
contract each department lists, read department by department (the server can't answer for
all at once) and kind by kind (contracts, then grants), with each one's summary page (the
vendor's city and state, how it was solicited, the full description) and its document.

One record per grant or contract, keyed by department and contract number. A summary page is
read once and kept. The contract document (a PDF) is the record's document.
"""

from __future__ import annotations

from entities.mentions import from_text
from records.sources import DocumentLink, Found, Item, Source
from records.sources._places import city_place

from . import parse

STATE = "0400000US46"


def search(fetcher, fields: dict, department: str, kind: str) -> bytes:
    data = {**fields, "ddl_DoA": department, "tb_Vendor": "", "tb_Doc_Descp": "",
            "tb_Contract_number": "", "RadioGroup1": parse.TYPES[kind],
            "btn_Search": "Search"}  # fmt: skip
    return fetcher.request("POST", parse.URL, data=data, timeout=300).content


def summary_page(fetcher, url: str, agency: str, number: str) -> bytes:
    if getattr(fetcher, "store", None) is not None:
        safe = "".join(c if c.isalnum() or c in "-." else "_" for c in number)
        return fetcher.fetch(url, f"summary/{agency}/{safe}.html")
    return fetcher.request("GET", url).content


def items(fetcher) -> Found:
    page = fetcher.request("GET", parse.URL).content
    fields = parse.form_fields(page)
    out, counts = Found(), {}
    for code, _ in parse.departments(page):
        for kind in parse.TYPES:
            found = parse.results(search(fetcher, fields, code, kind))
            counts[f"{code.strip()} {kind}"] = len(found)
            for row in found:
                detail = {}
                if row["summary_url"]:
                    detail = parse.summary(summary_page(fetcher, row["summary_url"], row["agency"],
                                                        row["number"]))  # fmt: skip
                data = {**row, "type": kind, **{k: v for k, v in detail.items() if v}}
                data.setdefault("description", row["list_description"])
                city = (
                    city_place(data.get("vendor_city"))
                    if data.get("vendor_state") == "SD"
                    else None
                )
                docs = [DocumentLink(data["document_url"], metadata={"label": "Contract"})] if (
                    data.get("document_url")) else []  # fmt: skip
                out.append(Item(key=f"{row['agency']} {row['number']}", data=data,
                                source_url=row["summary_url"] or parse.URL, documents=docs,
                                places=[STATE, *([city] if city else [])]))  # fmt: skip
    out.checks = {"found": counts}
    return out


def mentions(record) -> list:
    d = record.data
    return from_text(d["vendor_name"], dataset=record.recordset.dataset, record_key=str(record),
                     field="vendor_name", role="vendor", url=record.source_url,
                     context={"agency": d.get("agency_name"), "type": d["type"]})  # fmt: skip


SOURCE = Source(
    slug="grants-contracts", items=items, mentions=mentions, ingest="documents",
    name="State grants and contracts", dataset="grants-contracts",
    publisher="South Dakota Bureau of Finance and Management",
    base_url=parse.URL,
    description="Every grant and contract state departments list on open.sd.gov, with the "
                "vendor, amount, begin date, how it was solicited, and the document.",
)  # fmt: skip
