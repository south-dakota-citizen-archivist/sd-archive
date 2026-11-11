"""IRS Form 990-series e-file returns (XML): the filer, the period, the totals, and the people
the return names: officers, directors, trustees and key employees (with their titles, hours
and pay, every field as filed), the officer who signed, and the paid preparer."""

from __future__ import annotations

import re

from records.sources._tables import LayoutChanged

DOWNLOADS = "https://www.irs.gov/charities-non-profits/form-990-series-downloads"

# Groups that list people, by form: the 990's Part VII, the 990-EZ's Part IV, the 990-PF's
# Part VIII, and the highest-paid employees and contractors on each
PEOPLE_GROUPS = {
    "Form990PartVIISectionAGrp": "officer, director, trustee or key employee",
    "OfficerDirectorTrusteeEmplGrp": "officer, director, trustee or key employee",
    "OfficerDirTrstKeyEmplGrp": "officer, director, trustee or key employee",
    "CompensationHighestPaidEmplGrp": "highest-paid employee",
    "CompensationOfHghstPdCntrctGrp": "highest-paid contractor",
    "CompensationOfHghstPdEmplGrp": "highest-paid employee",
    "ContractorCompensationGrp": "highest-paid contractor",
}
# Totals, by the names the forms give them (the first one present is used)
TOTALS = {
    "total_revenue": ["CYTotalRevenueAmt", "TotalRevenueAmt", "TotalRevAndExpnssAmt"],
    "total_expenses": ["CYTotalExpensesAmt", "TotalExpensesAmt", "TotalExpensesRevAndExpnssAmt"],
    "total_assets_eoy": ["TotalAssetsEOYAmt"],
    "net_assets_eoy": ["NetAssetsOrFundBalancesEOYAmt", "TotNetAstOrFundBalancesEOYAmt"],
    "fmv_assets_eoy": ["FMVAssetsEOYAmt"],
    "contributions": ["CYContributionsGrantsAmt", "ContributionsGiftsGrantsEtcAmt"],
    "salaries": ["CYSalariesCompEmpBnftPaidAmt"],
    "employees": ["TotalEmployeeCnt"],
    "volunteers": ["TotalVolunteersCnt"],
}  # fmt: skip
NOT_A_NAME = {"NONE", "N/A", "NA", ""}


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _find(root, name: str):
    for el in root.iter():
        if _local(el.tag) == name:
            return el
    return None


def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _child(el, name: str) -> str:
    return _text(_find(el, name)) if el is not None else ""


def _name(el) -> tuple[str, str]:
    """(name, "person" or "business") of a group that names one."""
    person = _child(el, "PersonNm")
    if person:
        return person, "person"
    business = " ".join(x for x in (_child(el, "BusinessNameLine1Txt"),
                                    _child(el, "BusinessNameLine2Txt")) if x)  # fmt: skip
    return business, "business"


def _leaves(el) -> dict[str, str]:
    """Every value in a group, by its element name."""
    out = {}
    for child in el.iter():
        if child is el or len(child):
            continue
        name, value = _local(child.tag), " ".join((child.text or "").split())
        if name and value and name not in out:
            out[name] = value
    return out


def _amount(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse(xml: bytes) -> dict:
    from lxml import etree

    root = etree.fromstring(xml)
    if _local(root.tag) != "Return":
        raise LayoutChanged("not an IRS e-file return")
    header = _find(root, "ReturnHeader")
    filer = _find(header, "Filer")
    name, _ = _name(_find(filer, "BusinessName"))
    address = _find(filer, "USAddress")
    out: dict = {
        "return_type": _child(header, "ReturnTypeCd"),
        "return_version": root.get("returnVersion"),
        "tax_year": _child(header, "TaxYr"),
        "period_begin": _child(header, "TaxPeriodBeginDt"),
        "period_end": _child(header, "TaxPeriodEndDt"),
        "filed_at": _child(header, "ReturnTs"),
        "ein": _child(filer, "EIN"),
        "name": name,
        "in_care_of": _child(filer, "InCareOfNm") or None,
        "address": {k: _child(address, t) for k, t in (
            ("street", "AddressLine1Txt"), ("street2", "AddressLine2Txt"), ("city", "CityNm"),
            ("state", "StateAbbreviationCd"), ("zip", "ZIPCd"))} if address is not None else {},
        "amended": _child(root, "AmendedReturnInd") == "X",
        "website": _child(root, "WebsiteAddressTxt") or None,
        "mission": _child(root, "ActivityOrMissionDesc") or _child(root, "MissionDesc") or None,
        "principal_officer": _child(root, "PrincipalOfficerNm") or None,
    }  # fmt: skip
    for key, names in TOTALS.items():
        for n in names:
            el = _find(root, n)
            if el is not None and _text(el):
                out[key] = _amount(_text(el))
                break
        else:
            out[key] = None
    officer = _find(header, "BusinessOfficerGrp")
    out["signed_by"] = ({"name": _child(officer, "PersonNm"),
                         "title": _child(officer, "PersonTitleTxt"),
                         "date": _child(officer, "SignatureDt")}
                        if officer is not None else None)  # fmt: skip
    preparer = _find(header, "PreparerFirmGrp")
    out["preparer"] = ({"firm": _name(_find(preparer, "PreparerFirmName"))[0],
                        "person": _child(header, "PreparerPersonNm") or None}
                       if preparer is not None else None)  # fmt: skip
    people = []
    for el in root.iter():
        role = PEOPLE_GROUPS.get(_local(el.tag))
        if role is None:
            continue
        who, kind = _name(el)
        if who.upper() in NOT_A_NAME:
            continue
        fields = _leaves(el)
        people.append({"name": who, "kind": kind, "role": role,
                       "title": fields.get("TitleTxt") or None, "group": _local(el.tag),
                       "fields": fields})  # fmt: skip
    out["people"] = people
    return out


def zip_links(html: str) -> dict[int, list[str]]:
    """The download page's zip files, by year."""
    out: dict[int, list[str]] = {}
    for url, year in sorted(set(re.findall(r'href="(https://[^"]+/990/xml/(\d{4})/[^"]+\.zip)"',
                                           html))):  # fmt: skip
        out.setdefault(int(year), []).append(url)
    return out


def index_links(html: str) -> dict[int, str]:
    found = re.findall(r'href="(https://[^"]+/990/xml/(\d{4})/index_\d{4}\.csv)"', html)
    return {int(y): url for url, y in set(found)}
