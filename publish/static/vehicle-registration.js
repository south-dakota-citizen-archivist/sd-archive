// South Dakota vehicle registrations by type: tiles, a line chart of one type by year
// (registrations or per 1,000 residents), a county map of the rate in any year, and the table
// of every year.
import {
  $, compact, countyMap, countyPicker, fmt, lineChart, onWidthChange, pct, quintiles,
  scrubber, setUrlCounty, step, tiles, urlCounty,
} from "sda";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
  "September", "October", "November", "December"];
const ALL = "all";
const TILES = ["pickup_suv_van", "electric"];  // after all registrations and the rate
const state = { data: null, place: null, measure: ALL, mode: "count", year: null };
let picker, map, breaks = [];

const label = (m) => state.data.labels[m];
const placeName = () => (state.place ? `${state.data.counties[state.place].name} County`
  : "Statewide");
const area = (geoid) => (geoid ? state.data.counties[geoid] : state.data.state);
const people = (geoid) => (geoid ? state.data.population.counties[geoid] : state.data.population.state);

// a measure's values by year for an area; "all" is the ten vehicle types added up
function values(geoid, m) {
  const a = area(geoid);
  if (m !== ALL) return a[m];
  return state.data.years.map((_, i) => {
    const parts = state.data.vehicle_types.map((t) => a[t][i]);
    return parts.some((v) => v === null) ? null : parts.reduce((s, v) => s + v, 0);
  });
}
const perThousand = (v, pop) => (v === null || !pop ? null : (1000 * v) / pop);
function rates(geoid, m) {
  const pop = people(geoid);
  return values(geoid, m).map((v, i) => perThousand(v, pop[i]));
}
const shown = (v, mode = state.mode) => (v === null ? "n/a" : mode === "rate" && v < 10
  ? v.toFixed(1) : fmt.format(Math.round(v)));
function change(now, before) {
  if (now === null || !before) return "";
  const d = (100 * (now - before)) / before;
  return `${d >= 0 ? "+" : "−"}${pct(Math.abs(d))}`;
}

// ---- tiles ------------------------------------------------------------------------------
function partialAll(side, geoid) {
  const p = state.data.partial;
  if (!p || !p[side]) return null;
  const a = geoid ? p[side].counties[geoid] : p[side].state;
  return a ? state.data.vehicle_types.reduce((s, t) => s + a[t], 0) : null;
}

function renderTiles() {
  const years = state.data.years, i = years.length - 1;
  $("tiles-heading").innerHTML = `<span>${placeName()}</span> <span class="when"><span class="sep">· `
    + `</span>${years[i]}, the latest full year</span>`;
  const tile = (m) => {
    const v = values(state.place, m);
    return { label: label(m), value: shown(v[i], "count"),
      sub: v[i - 1] ? `${change(v[i], v[i - 1])} from ${years[i - 1]}` : "" };
  };
  const r = rates(state.place, ALL);
  tiles($("tiles"), [tile(ALL), { label: "Per 1,000 residents", value: shown(r[i], "rate"),
    sub: `${state.data.population.estimated_from[i]} population estimate` },
  ...TILES.map(tile)]);

  const p = state.data.partial, now = partialAll("current", state.place);
  const before = partialAll("previous", state.place);
  $("so-far").innerHTML = p && now !== null ? `<b>${p.year} so far</b> (January through `
    + `${MONTHS[p.through_month - 1]}): ${fmt.format(now)} registrations${before
      ? `, ${change(now, before)} from the same months of ${p.year - 1}` : ""}.` : "";
}

// ---- chart ------------------------------------------------------------------------------
function series() {
  return state.mode === "rate" ? rates(state.place, state.measure) : values(state.place, state.measure);
}

function renderChart() {
  const years = state.data.years, v = series();
  const counts = values(state.place, state.measure), r = rates(state.place, state.measure);
  $("chart-title").innerHTML = `<span>${placeName()}</span><span class="what"><span class="sep">: `
    + `</span>${label(state.measure).toLowerCase()}${state.mode === "rate"
      ? " per 1,000 residents" : ""}</span>`;
  lineChart($("chart"), {
    times: years.map((y) => new Date(`${y}-01-01T12:00:00`).getTime()),
    series: [{ key: state.measure, label: label(state.measure), color: "var(--accent)", values: v }],
    yFormat: state.mode === "rate" ? (x) => (x < 10 ? x.toFixed(1).replace(/\.0$/, "") : compact(x))
      : undefined,
    summary: `Line chart of ${label(state.measure).toLowerCase()} ${state.mode === "rate"
      ? "per 1,000 residents" : "registered"} by year in ${state.place ? placeName()
      : "South Dakota"}, ${years[0]} to ${years[years.length - 1]}: ${shown(v[0])} in ${years[0]}`
      + `, ${shown(v[v.length - 1])} in ${years[years.length - 1]}. The table below lists every year.`,
    tip: (i) => `<div class="t-head">${years[i]}</div>`
      + `<div class="t-row">Registered<b>${shown(counts[i], "count")}</b></div>`
      + `<div class="t-row">Per 1,000 residents<b>${shown(r[i], "rate")}</b></div>`
      + (i ? `<div class="t-row muted">From ${years[i - 1]}<b>${change(counts[i], counts[i - 1])
        || "n/a"}</b></div>` : ""),
  });
}

// ---- table ------------------------------------------------------------------------------
function renderTable() {
  const d = state.data, cols = [ALL, ...d.measures];
  const byMeasure = Object.fromEntries(cols.map((m) => [m, state.mode === "rate"
    ? rates(state.place, m) : values(state.place, m)]));
  const rows = d.years.map((y, i) => `<tr><td>${y}</td>${cols.map((m) =>
    `<td>${shown(byMeasure[m][i])}</td>`).join("")}<td class="left"><a href="${
    d.year_sources[i]}">Report</a></td></tr>`).reverse();
  const p = d.partial;
  if (p) {
    const a = state.place ? p.current.counties[state.place] : p.current.state;
    const all = partialAll("current", state.place);
    rows.unshift(`<tr class="partial"><td>${p.year} <span class="muted">(Jan–${
      MONTHS[p.through_month - 1].slice(0, 3)})</span></td>${cols.map((m) => `<td>${
      state.mode === "rate" ? "" : fmt.format(m === ALL ? all : a?.[m] ?? 0)}</td>`).join("")}`
      + `<td class="left"><a href="${p.source}">Report</a></td></tr>`);
  }
  $("figures").innerHTML = rows.join("");
}

// ---- map: each county's rate per 1,000 residents in the chosen year ---------------------
function renderMap() {
  const i = state.year, m = state.measure;
  $("map-when").textContent = `${label(m)}, ${state.data.years[i]}`;
  map.paint((geoid) => {
    const r = rates(geoid, m)[i];
    return r === null ? "var(--county-fill)" : `var(--seq-${step(breaks, r)})`;
  }, (geoid, name) => `${name} County: ${shown(rates(geoid, m)[i], "rate")} per 1,000 residents`);
}

function mapTip(geoid) {
  const i = state.year, m = state.measure, v = values(geoid, m), r = rates(geoid, m);
  return `<div class="t-head">${state.data.counties[geoid].name} County, ${state.data.years[i]}</div>`
    + `<div class="t-row">Registered<b>${shown(v[i], "count")}</b></div>`
    + `<div class="t-row">Per 1,000 residents<b>${shown(r[i], "rate")}</b></div>`
    + (i ? `<div class="t-row muted">From ${state.data.years[i - 1]}<b>${change(v[i], v[i - 1])
      || "n/a"}</b></div>` : "");
}

// the scale's steps: quintiles of the counties' rates in the latest full year, for the chosen
// type, kept for every year (so an earlier year's lighter map shows the change)
function setBreaks() {
  const last = state.data.years.length - 1;
  breaks = quintiles(Object.keys(state.data.counties).map((g) => rates(g, state.measure)[last]));
  const f = (x) => (x < 10 ? x.toFixed(1) : x < 1000 ? fmt.format(Math.round(x))
    : `${(x / 1000).toFixed(1)}K`);
  const b = breaks;
  $("scale-ticks").innerHTML = b.length ? [`&lt;${f(b[0])}`, `${f(b[0])}–${f(b[1])}`,
    `${f(b[1])}–${f(b[2])}`, `${f(b[2])}–${f(b[3])}`, `${f(b[3])}+`]
    .map((t) => `<span>${t}</span>`).join("") : "";
}

// ---- choosing ---------------------------------------------------------------------------
function choose(geoid) {
  state.place = geoid && state.data.counties[geoid] ? geoid : null;
  picker.show(state.place);
  setUrlCounty(state.place);
  render();
}

function render() {
  map.select(state.place);
  renderTiles();
  renderChart();
  renderTable();
}

$("measure").addEventListener("change", (e) => {
  state.measure = e.target.value;
  setBreaks();
  renderMap();
  renderChart();
});
document.querySelectorAll("#mode button").forEach((b) => {
  b.addEventListener("click", () => {
    state.mode = b.dataset.value;
    document.querySelectorAll("#mode button").forEach((o) => o.setAttribute("aria-pressed", o === b));
    renderChart();
    renderTable();
  });
});

fetch(window.PAGE_DATA).then((r) => r.json()).then((data) => {
  state.data = data;
  state.measure = $("measure").value;
  picker = countyPicker({ input: $("county-input"), list: $("county-list"),
    statewide: $("statewide"), counties: data.counties, onChoose: choose });
  map = countyMap({ box: $("map-chart"), onChoose: choose, tip: mapTip });
  setBreaks();
  const slider = scrubber({ range: $("report-range"), output: $("report-out"),
    latest: $("report-latest"), label: (i) => String(data.years[i]),
    onChange: (i) => { state.year = i; renderMap(); } });
  slider.set(data.years.length - 1);
  state.place = urlCounty(data.counties);
  picker.show(state.place);
  render();
  onWidthChange($("chart"), renderChart);
});
