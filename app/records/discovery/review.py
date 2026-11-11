"""The model's review: is a candidate worth getting, what is it, how important. (Whether it's
still produced and how it would be got come from the rules.) And, from the few pages that
describe the government's records in prose, the records they name.

Every answer carries a quote; a quote not found in what the model was shown (the
candidate's evidence, or the page) drops the suggestion. The model is sdllm's default (a
local one) unless another is given.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .crawl import Page
from .signals import Candidate

HOW = Literal["scrape the site", "scrape the platform", "download files", "records request",
              "already covered", "unclear"]  # fmt: skip

INSTRUCTIONS = """You review candidate record sets found on a South Dakota local government's
website, for an archive of public records about what governments do and spend.

Worth getting: a series of records the government produces about its own business, one item
after another or year after year. Examples: meeting agendas and minutes, budgets, audits and
financial statements, claims or bills paid, contracts and bids awarded, building or other
permits, licenses, inspections, ordinances and resolutions, code violations, assessments,
election results, police or fire activity reports.

Not worth getting: employee benefits or handbooks, job descriptions or openings, forms and
applications to fill in, news posts and announcements, a single request for proposals,
brochures, maps of parks or facilities, contact pages, general information about services.

Priority: high for records about money, permits, enforcement or decisions that are published
regularly; medium for occasional reports and plans; low for the rest.

Use only what the candidate shows. Give a short quote copied exactly from the candidate text as
evidence."""


class Review(BaseModel):
    worth_getting: bool
    title: str = Field(description="what the record set is, e.g. 'City Council minutes'")
    description: str = Field(description="one sentence: what's in it and who keeps it")
    kind: Literal["records", "aggregate"] = Field(
        description="records: one row per item (permit, meeting, payment); aggregate: totals "
                    "about the place (counts, amounts per year)")  # fmt: skip
    priority: Literal["high", "medium", "low"]
    reason: str = Field(description="one sentence")
    evidence_quote: str = Field(description="a short quote copied exactly from the candidate")


class NamedRecord(BaseModel):
    title: str
    description: str
    how_to_get: HOW
    quote: str = Field(description="a quote copied exactly from the page that names it")


class PageRecords(BaseModel):
    records: list[NamedRecord]


PROSE_INSTRUCTIONS = """From this page of a South Dakota local government's website, list the
public records it says the government keeps, produces or will provide on request (registers,
permits, reports, minutes, licenses, inspection results, contracts, claims, rolls, logs…).
Leave out forms to fill in, general information and services. For each, give a quote copied
exactly from the page that names it. If the page names none, return an empty list."""


def facts(candidate: Candidate, this_year: int) -> dict:
    """What the rules can say without the model: whether it's still produced (from the years
    its documents name) and how it would be got (from how it was found)."""
    if candidate.years:
        current = "yes" if max(candidate.years) >= this_year - 1 else "no"
    else:
        current = "unclear"
    how = {"platform": "scrape the platform", "collection": "download files",
           "record type": "scrape the site"}[candidate.kind]  # fmt: skip
    return {"still_produced": current, "how_to_get": how}


def review(candidate: Candidate, model: str | None = None, this_year: int | None = None,
           **params) -> dict:  # fmt: skip
    from datetime import date

    import sdllm

    text = candidate.summary()
    found = sdllm.extract(Review, text, model=model, instructions=INSTRUCTIONS,
                          quote_required=False, **params).data  # fmt: skip
    quoted = sdllm.quote_found(found.evidence_quote, text)
    return {
        **found.model_dump(),
        **facts(candidate, this_year or date.today().year),
        "quote_checked": quoted,
    }


def prose(page: Page, model: str | None = None, max_chars: int = 8000, **params) -> list[dict]:
    import sdllm

    text = page.text[:max_chars]
    found = sdllm.extract(PageRecords, text, model=model, instructions=PROSE_INSTRUCTIONS,
                          quote_required=False, **params).data  # fmt: skip
    return [{**r.model_dump(), "url": page.url}
            for r in found.records if sdllm.quote_found(r.quote, text)]  # fmt: skip
