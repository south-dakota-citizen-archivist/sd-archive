"""The Board of Pharmacy's yearly disciplinary action reports: a PDF a year, an action a block
of labeled lines.

    Name: Kent Munger
    City: De Smet, SD
    License Number: 4583
    License Status: Active
    Board Action: Three year probation
    Effective Date: March 17, 2016
    Description: Violated laws and rules … (on as many lines as it takes)
"""

from __future__ import annotations

import re

LABELS = {"Name": "name", "City": "location", "License Number": "license_number",
          "License Status": "license_status", "Board Action": "action",
          "Effective Date": "effective", "Description": "nature"}  # fmt: skip
LABEL = re.compile(rf"^({'|'.join(map(re.escape, LABELS))}):\s*(.*)$")


def actions(text: str) -> list[dict]:
    out, current, last = [], None, None
    for line in (" ".join(x.split()) for x in text.splitlines()):
        m = LABEL.match(line)
        if m and m[1] == "Name":
            current = {"lines": [line]}
            out.append(current)
        if current is None:
            continue  # the report's title
        if not m:
            if last == "nature" and line:  # the description goes on
                current["nature"] += " " + line
                current["lines"].append(line)
            continue
        if m[1] != "Name":
            current["lines"].append(line)
        last = LABELS[m[1]]
        current[last] = m[2].strip()
    for a in out:
        a["text"] = "\n".join(a.pop("lines"))
        place = re.match(r"^(?P<city>[^,]+?)(?:,\s*(?P<state>[A-Z]{2}))?$", a.get("location", ""))
        if place:
            a["city"], a["state"] = place["city"], place["state"]
        name, _, credential = a.get("name", "").partition(",")
        if CREDENTIAL.match(credential.strip()):  # "Mark Tokach, R.Ph."; not "…, LLP"
            a["name"], a["credential"] = name.strip(), credential.strip()
        a["license_number"] = (a.get("license_number") or "").lstrip("#").strip() or None
        # a pharmacy's permit ("100-1925"), not a pharmacist's license
        a["holder"] = "establishment" if (a["license_number"] or "").startswith("100-") \
            else "person"  # fmt: skip
    return out


CREDENTIAL = re.compile(r"^(R\.?\s?Ph\.?|Pharm\.?\s?D\.?|Technician)$", re.I)
