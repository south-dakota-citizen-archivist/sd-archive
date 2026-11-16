"""The review page's check of a statistical report row (hand.problems).

    uv run python pipelines/property-tax/checks.py < cells.json

Reads {"key": …, "cells": {column: value}} as JSON and prints the row's problems as a JSON
list.
"""

from __future__ import annotations

import json
import sys


def main() -> None:
    import hand

    found = json.load(sys.stdin)
    print(json.dumps(hand.problems(found["key"], found["cells"])))


if __name__ == "__main__":
    main()
