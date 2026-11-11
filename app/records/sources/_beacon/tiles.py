"""Every feature of a Beacon layer, read by map extent.

GetVectorLayer answers at most LIMIT features for an extent (as many as fit, not a page of
them), so the layer's extent is cut into a grid and any tile that comes back full is split
in four and read again. A feature crossing tiles comes back from each; features are kept by
key. Each tile's answer is stored, so a stopped read resumes where it was.
"""

from __future__ import annotations

import json
from collections.abc import Callable

LIMIT = 500
MIN_SIZE = 50.0  # map units (feet here): a tile this small that is still full is an error


class TooDense(RuntimeError):
    """A tile at the smallest size still came back full."""


def grid(extent: dict, n: int) -> list[dict]:
    """The extent cut into n × n tiles."""
    w = (extent["maxx"] - extent["minx"]) / n
    h = (extent["maxy"] - extent["miny"]) / n
    return [{"minx": extent["minx"] + i * w, "miny": extent["miny"] + j * h,
             "maxx": extent["minx"] + (i + 1) * w, "maxy": extent["miny"] + (j + 1) * h}
            for j in range(n) for i in range(n)]  # fmt: skip


def quarters(box: dict) -> list[dict]:
    mx, my = (box["minx"] + box["maxx"]) / 2, (box["miny"] + box["maxy"]) / 2
    return [{"minx": box["minx"], "miny": box["miny"], "maxx": mx, "maxy": my},
            {"minx": mx, "miny": box["miny"], "maxx": box["maxx"], "maxy": my},
            {"minx": box["minx"], "miny": my, "maxx": mx, "maxy": box["maxy"]},
            {"minx": mx, "miny": my, "maxx": box["maxx"], "maxy": box["maxy"]}]  # fmt: skip


def name(box: dict) -> str:
    return "_".join(f"{box[k]:.0f}" for k in ("minx", "miny", "maxx", "maxy"))


def read(ask: Callable[[dict], list[dict]], extent: dict, *, n: int = 8, store=None,
         prefix: str = "tiles", limit: int = LIMIT) -> tuple[dict[str, dict], dict]:  # fmt: skip
    """Every feature in extent, by key, and counts (tiles read, split, stored ones reused).
    ask(box) is one GetVectorLayer call; store (an sdfetch Store) keeps each answer."""
    out: dict[str, dict] = {}
    counts = {"tiles": 0, "split": 0, "reused": 0}
    todo = grid(extent, n)
    while todo:
        box = todo.pop()
        path = f"{prefix}/{name(box)}.json"
        if store is not None and store.has(path):
            answer = json.loads(store.read(path))
            counts["reused"] += 1
        else:
            answer = ask(box)
            if store is not None:
                store.save(path, json.dumps(answer).encode(), method="POST", allow_empty=True)
        counts["tiles"] += 1
        if len(answer) >= limit:
            if box["maxx"] - box["minx"] < MIN_SIZE:
                raise TooDense(f"tile {name(box)} still has {len(answer)} features")
            counts["split"] += 1
            todo += quarters(box)
            continue
        for f in answer:
            out.setdefault(f["Key"], f)
    return out, counts
