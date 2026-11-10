// Small helpers around MapLibre for the hub's pages.
// Basemap: OpenFreeMap (free, no API key).
const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
const SD_BOUNDS = [[-104.06, 42.48], [-96.44, 45.95]];
const ACCENT = "#2563eb";

function hubMap(elementId, { bounds = SD_BOUNDS } = {}) {
  const map = new maplibregl.Map({
    container: elementId,
    style: BASEMAP_STYLE,
    bounds,
    fitBoundsOptions: { padding: 20 },
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  return map;
}

// Fill, outline and point layers for one source, so any geometry type renders.
function addFeatureLayers(map, sourceId, { sourceLayer, onClick } = {}) {
  const base = sourceLayer ? { source: sourceId, "source-layer": sourceLayer } : { source: sourceId };
  map.addLayer({
    id: `${sourceId}-fill`, type: "fill", ...base,
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": ACCENT, "fill-opacity": 0.12 },
  });
  map.addLayer({
    id: `${sourceId}-line`, type: "line", ...base,
    filter: ["in", ["geometry-type"], ["literal", ["Polygon", "LineString"]]],
    paint: { "line-color": ACCENT, "line-width": 1 },
  });
  map.addLayer({
    id: `${sourceId}-point`, type: "circle", ...base,
    filter: ["==", ["geometry-type"], "Point"],
    paint: { "circle-color": ACCENT, "circle-radius": 4, "circle-stroke-color": "#fff", "circle-stroke-width": 1 },
  });
  if (onClick) {
    for (const suffix of ["fill", "line", "point"]) {
      const id = `${sourceId}-${suffix}`;
      map.on("click", id, (e) => onClick(e.features[0], e.lngLat));
      map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
    }
  }
}

function boundsOf(geojson) {
  const b = new maplibregl.LngLatBounds();
  const walk = (c) => (typeof c[0] === "number" ? b.extend(c) : c.forEach(walk));
  const geoms = geojson.type === "FeatureCollection"
    ? geojson.features.map((f) => f.geometry).filter(Boolean)
    : [geojson];
  geoms.forEach((g) => walk(g.coordinates));
  return b.isEmpty() ? null : b;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function propsTable(props, limit = 12) {
  const rows = Object.entries(props).filter(([, v]) => v !== null && v !== "" && v !== " ").slice(0, limit);
  return `<table class="text-xs">${rows.map(([k, v]) =>
    `<tr><th class="pr-2 text-left font-medium text-gray-500 align-top">${escapeHtml(k)}</th><td>${escapeHtml(v)}</td></tr>`).join("")}</table>`;
}

// Choropleth of counties by number of datasets; click opens the county page.
function countyChoropleth(map, url) {
  map.on("load", async () => {
    const data = await (await fetch(url)).json();
    const max = Math.max(1, ...data.features.map((f) => f.properties.n));
    map.addSource("counties", { type: "geojson", data });
    map.addLayer({
      id: "counties-fill", type: "fill", source: "counties",
      paint: {
        "fill-color": ["interpolate", ["linear"], ["get", "n"], 0, "#f3f4f6", 1, "#dbeafe", max, "#1d4ed8"],
        "fill-opacity": 0.75,
      },
    });
    map.addLayer({ id: "counties-line", type: "line", source: "counties", paint: { "line-color": "#fff", "line-width": 1 } });
    const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false });
    map.on("mousemove", "counties-fill", (e) => {
      const p = e.features[0].properties;
      map.getCanvas().style.cursor = "pointer";
      popup.setLngLat(e.lngLat).setHTML(`<b>${escapeHtml(p.name)}</b><br>${p.n} datasets`).addTo(map);
    });
    map.on("mouseleave", "counties-fill", () => { popup.remove(); map.getCanvas().style.cursor = ""; });
    map.on("click", "counties-fill", (e) => (window.location = `/places/${e.features[0].properties.slug}/`));
  });
}
