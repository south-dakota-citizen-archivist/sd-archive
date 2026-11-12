"""SVG maps drawn at build time from the Census boundaries sdplaces loads."""

from __future__ import annotations

from html import escape


def county_svg(width: int = 640) -> str:
    """South Dakota's counties as one SVG: a path per county with data-geoid (fully
    qualified) and data-name, in an equal-area projection centered on the state (Albers,
    standard parallels 43° and 45.5°, central meridian 100°W), simplified."""
    import sdplaces

    counties = sdplaces.boundaries("county").to_crs(
        "+proj=aea +lat_1=43 +lat_2=45.5 +lat_0=44 +lon_0=-100 +datum=NAD83 +units=m +no_defs"
    )
    counties["geometry"] = counties.geometry.simplify(400, preserve_topology=True)
    minx, miny, maxx, maxy = counties.total_bounds
    scale = width / (maxx - minx)
    height = round((maxy - miny) * scale)

    def ring(coords) -> str:
        pts = [f"{(x - minx) * scale:.1f},{(maxy - y) * scale:.1f}" for x, y in coords]
        return "M" + "L".join(pts) + "Z"

    paths = []
    for row in counties.itertuples():
        geom = row.geometry
        polys = [geom] if geom.geom_type == "Polygon" else list(geom.geoms)
        d = "".join(ring(p.exterior.coords) + "".join(ring(i.coords) for i in p.interiors)
                    for p in polys)  # fmt: skip
        paths.append(f'<path d="{d}" data-geoid="0500000US{row.geoid}" '
                     f'data-name="{escape(row.short_name)}"><title>{escape(row.name)}</title>'
                     f"</path>")  # fmt: skip
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'role="group" aria-label="South Dakota counties">{"".join(paths)}</svg>')  # fmt: skip
