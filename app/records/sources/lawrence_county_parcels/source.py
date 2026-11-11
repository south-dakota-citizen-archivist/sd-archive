"""Lawrence County's parcels, from its Beacon app (LawrenceCountySD): each parcel's shape,
owners, summary, five years of valuation and its taxing units. Land, sales and building
details need a Beacon account and aren't read."""

from records.sources._beacon.source import parcels

SOURCE = parcels(
    "LawrenceCountySD", "lawrence-county-parcels", county="0500000US46081",
    name="Lawrence County parcels", publisher="Lawrence County Director of Equalization",
)  # fmt: skip
