// South Dakota voter registration by party: tiles, a line chart (voters or share), a county
// picker (search, or a map shaded by leading party in any report) and the table of reports.
import {
  $, countyMap, countyPicker, fmt, lineChart, longDate, onWidthChange, parseDate, pct,
  scrubber, setUrlCounty, tiles, urlCounty,
} from "sda";

const PARTIES = ["republican", "democratic", "other"];
const LABEL = { republican: "Republican", democratic: "Democratic", other: "Other" };
const ELECTION = { primary: "Primary", general: "General", special: "Special",
  "presidential primary": "Presidential primary" };
const state = { data: null, place: null, mode: "count", report: null };
// steps of the leading party's lead (points of active voters), the same for every report
const LEAD_STEPS = [10, 25, 40, 55];
const RAMP = { republican: "rep", democratic: "dem" };
let picker, map;

const placeName = () => (state.place ? `${state.data.counties[state.place].name} County`
  : "Statewide");

function rowsFor(place) {
  const s = place ? state.data.counties[place] : state.data.state;
  return state.data.dates.map((date, i) => {
    const values = Object.fromEntries(PARTIES.map((p) => [p, s[p][i]]));
    const known = PARTIES.filter((p) => values[p] !== null);
    const total = known.length ? known.reduce((a, p) => a + values[p], 0) : null;
    return { date, i, values, total };
  });
}

const shown = (r, p) => {
  const v = r.values[p];
  if (v === null) return "n/a";
  return state.mode === "percent" ? pct((100 * v) / r.total) : fmt.format(v);
};

// ---- tiles ------------------------------------------------------------------------------
function renderTiles(rows) {
  const last = [...rows].reverse().find((r) => r.total);
  if (!last) { tiles($("tiles"), []); return; }
  // the place and date go in the heading above, so each tile keeps one short line
  $("tiles-heading").innerHTML = `<span>${placeName()}</span> <span class="when"><span class="sep">· `
    + `</span>latest report, ${longDate(last.date)}</span>`;
  tiles($("tiles"), [
    { label: "Active voters", value: fmt.format(last.total), sub: "All parties" },
    ...PARTIES.map((p) => {
      const v = last.values[p] ?? 0;
      return { label: LABEL[p], value: fmt.format(v), color: `var(--${p})`,
        sub: `${pct((100 * v) / last.total)} of active voters` };
    }),
  ]);
}

// ---- chart ------------------------------------------------------------------------------
function renderChart(rows) {
  const value = (r, p) => {
    const v = r.values[p];
    if (v === null || !r.total) return null;
    return state.mode === "percent" ? (100 * v) / r.total : v;
  };
  const all = rows.flatMap((r) => PARTIES.map((p) => value(r, p))).filter((v) => v !== null);
  lineChart($("chart"), {
    times: rows.map((r) => parseDate(r.date).getTime()),
    series: PARTIES.map((p) => ({ key: p, label: LABEL[p], color: `var(--${p})`,
      values: rows.map((r) => value(r, p)) })),
    yMax: state.mode === "percent" ? Math.max(60, Math.ceil(Math.max(...all) / 10) * 10)
      : undefined,
    yFormat: state.mode === "percent" ? (v) => `${v}%` : undefined,
    summary: chartSummary(rows),
    tip: (i) => {
      const r = rows[i], election = state.data.elections[r.i];
      return `<div class="t-head">${longDate(r.date)}${election
        ? ` · ${ELECTION[election] || election}` : ""}</div>` + PARTIES.map((p) =>
        `<div class="t-row"><i class="swatch" style="background:var(--${p})"></i>
          ${LABEL[p]}<b>${shown(r, p)}</b></div>`).join("")
        + `<div class="t-row muted">Active voters<b>${r.total ? fmt.format(r.total)
          : "n/a"}</b></div>`;
    },
  });
}

function chartSummary(rows) {
  const known = rows.filter((r) => r.total);
  const a = known[0], b = known[known.length - 1];
  const where = state.place ? placeName() : "South Dakota";
  const part = (r) => PARTIES.map((p) => `${LABEL[p]} ${fmt.format(r.values[p] ?? 0)}`)
    .join(", ");
  return `Line chart of registered voters by party in ${where}, ${longDate(a.date)} to `
    + `${longDate(b.date)}. First report: ${part(a)}. Latest: ${part(b)}. `
    + "The table below lists every report.";
}

// ---- table ------------------------------------------------------------------------------
function renderTable(rows) {
  $("figures").innerHTML = rows.slice().reverse().filter((r) => r.total).map((r) => {
    const source = state.data.sources[r.i], copy = state.data.archived[r.i];
    const election = state.data.elections[r.i];
    const links = source === copy ? `<a href="${copy}">Report</a>`
      : `<a href="${source}">Report</a> · <a href="${copy}">copy</a>`;
    return `<tr><td>${longDate(r.date)}</td><td class="left">${election
      ? ELECTION[election] || election : ""}</td>${PARTIES.map((p) => `<td>${shown(r, p)}</td>`)
      .join("")}<td>${fmt.format(r.total)}</td><td class="left">${links}</td></tr>`;
  }).join("");
}

// ---- map: each county shaded by its leading party in the chosen report ------------------
function standing(geoid, i) {
  const c = state.data.counties[geoid];
  if (!c) return null;
  const values = Object.fromEntries(PARTIES.map((p) => [p, c[p][i]]));
  if (PARTIES.some((p) => values[p] === null)) return null;
  const total = PARTIES.reduce((a, p) => a + values[p], 0);
  if (!total) return null;
  const ranked = [...PARTIES].sort((a, b) => values[b] - values[a]);
  const lead = (100 * (values[ranked[0]] - values[ranked[1]])) / total;
  return { values, total, leader: ranked[0], lead };
}

function fillFor(s) {
  if (!s) return "var(--county-fill)";
  const ramp = RAMP[s.leader];
  if (!ramp) return `var(--${s.leader})`;
  return `var(--${ramp}-${LEAD_STEPS.filter((t) => s.lead >= t).length + 1})`;
}

const leadText = (s) => `${LABEL[s.leader]} by ${s.lead.toFixed(1)} points`;

function reportLabel(i) {
  const date = state.data.dates[i], election = state.data.elections[i];
  return `${longDate(date)}${election ? ` (${ELECTION[election] || election})` : ""}`;
}

function renderMap() {
  const i = state.report;
  $("map-when").textContent = reportLabel(i);
  map.paint((geoid) => fillFor(standing(geoid, i)), (geoid, name) => {
    const s = standing(geoid, i);
    return `${name} County: ${s ? leadText(s) : "no figures in this report"}`;
  });
}

function mapTip(geoid) {
  const s = standing(geoid, state.report);
  const name = state.data.counties[geoid]?.name;
  return `<div class="t-head">${name} County</div>` + (s
    ? PARTIES.map((p) => `<div class="t-row"><i class="swatch" style="background:var(--${p})">`
      + `</i>${LABEL[p]}<b>${pct((100 * s.values[p]) / s.total)}</b></div>`).join("")
      + `<div class="t-row muted">${LABEL[s.leader]} lead<b>${s.lead.toFixed(1)} pts</b></div>`
    : '<div class="t-row muted">No figures in this report</div>');
}

// ---- choosing ---------------------------------------------------------------------------
function choose(geoid) {
  state.place = geoid && state.data.counties[geoid] ? geoid : null;
  picker.show(state.place);
  setUrlCounty(state.place);
  render();
}

function render() {
  const rows = rowsFor(state.place);
  $("chart-title").innerHTML = `<span>${placeName()}</span><span class="what"><span class="sep">: `
    + `</span>${state.mode === "percent" ? "share of active voters by party"
      : "active voters by party"}</span>`;
  map.select(state.place);
  renderTiles(rows);
  renderChart(rows);
  renderTable(rows);
}

document.querySelectorAll("#mode button").forEach((b) => {
  b.addEventListener("click", () => {
    state.mode = b.dataset.value;
    document.querySelectorAll("#mode button").forEach((o) =>
      o.setAttribute("aria-pressed", o === b));
    render();
  });
});

fetch(window.PAGE_DATA).then((r) => r.json()).then((data) => {
  state.data = data;
  picker = countyPicker({ input: $("county-input"), list: $("county-list"),
    statewide: $("statewide"), counties: data.counties, onChoose: choose });
  map = countyMap({ box: $("map-chart"), onChoose: choose, tip: mapTip });
  const slider = scrubber({ range: $("report-range"), output: $("report-out"),
    latest: $("report-latest"), label: reportLabel,
    onChange: (i) => { state.report = i; renderMap(); } });
  slider.set(data.dates.length - 1);
  state.place = urlCounty(data.counties);
  picker.show(state.place);
  render();
  onWidthChange($("chart"), () => renderChart(rowsFor(state.place)));
});
