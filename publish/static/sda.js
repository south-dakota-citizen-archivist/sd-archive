// South Dakota Citizen Archivist: what every data page shares. Each page's app.js imports
// these ("sda", mapped to this file in the page's import map) and supplies its own data,
// labels and colors.
//
//   format: fmt, pct, compact, parseDate, longDate
//   tiles(container, items)                  a row of stat tiles
//   lineChart(box, options)                  a line chart with a crosshair and tooltip
//   countyPicker(options)                    the county search box (a combobox)
//   countyMap(options)                       the clickable county map, its outlines and tooltip
//   scrubber(options)                        a range input that steps through periods
//   urlCounty(counties) / setUrlCounty(geoid) the ?county= link (its five-digit FIPS code)
//   onWidthChange(box, redraw)               redraw a chart when its card changes width

export const fmt = new Intl.NumberFormat("en-US");
export const pct = (v, digits = 1) => `${v.toFixed(digits)}%`;
export const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";

export const parseDate = (s) => new Date(`${s}T12:00:00`);
export const longDate = (s) => parseDate(s).toLocaleDateString("en-US",
  { year: "numeric", month: "long", day: "numeric" });

export function compact(v) {
  if (v >= 1e6) return `${(v / 1e6).toFixed(v % 1e6 ? 1 : 0)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(v % 1e3 ? 1 : 0)}K`;
  return String(v);
}

export function niceStep(span, target) {
  const raw = span / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const n = raw / mag;
  return (n >= 5 ? 10 : n >= 2 ? 5 : n >= 1 ? 2 : 1) * mag;
}

export function svgEl(name, attrs, parent) {
  const node = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (parent) parent.append(node);
  return node;
}

// ---- closing tooltips on a touch screen -----------------------------------------------
// A touch screen has no pointer to leave, and a tap on the page doesn't move focus: a tap
// anywhere outside a tooltip's owner closes it. Each owner registers once (by key), so a
// chart drawn again replaces its own entry.
const outside = new Map();
document.addEventListener("pointerdown", (e) => {
  for (const { inside, close } of outside.values()) if (!inside(e.target)) close();
});

// ---- tiles ------------------------------------------------------------------------------
// items: [{label, value, sub, color}]; color (a CSS color) adds a swatch before the label
export function tiles(container, items) {
  container.innerHTML = "";
  for (const t of items) {
    const el = document.createElement("div");
    el.className = "card tile";
    el.innerHTML = `<div class="label">${t.color
      ? `<i class="swatch" style="background:${t.color}"></i>` : ""}${t.label}</div>
      <div class="value">${t.value}</div><div class="sub">${t.sub ?? ""}</div>`;
    container.append(el);
  }
}

// ---- line chart -------------------------------------------------------------------------
// options:
//   times     each point's time (ms), ascending
//   series    [{key, label, color, values}] (values: a number or null per point)
//   yFormat   an axis label for a value (default: compact)
//   yMax      the axis's top, before rounding to a step (default: 8% over the largest value)
//   endLabels label each line at its end (true when there's more than one line)
//   tip(i)    the tooltip's HTML for point i
//   summary   the chart's text alternative
// returns {hide}
export function lineChart(box, o) {
  box.innerHTML = "";
  const labels = o.endLabels ?? o.series.length > 1;
  // drawn at the card's own width, so text stays its real size on a phone
  const W = Math.max(300, Math.round(box.clientWidth));
  const H = Math.round(Math.min(400, Math.max(260, W * 0.55)));
  const M = { top: 12, right: labels ? 84 : 20, bottom: 30, left: W < 480 ? 44 : 60 };
  const times = o.times;
  const x0 = Math.min(...times), x1 = Math.max(...times);
  const x = (t) => M.left + ((t - x0) / (x1 - x0 || 1)) * (W - M.left - M.right);
  const all = o.series.flatMap((s) => s.values).filter((v) => v !== null && v !== undefined);
  const yMax = o.yMax ?? (Math.max(...all, 0) * 1.08 || 1);
  const yStep = niceStep(yMax, 5);
  const yTop = Math.ceil(yMax / yStep) * yStep;
  const y = (v) => H - M.bottom - (v / yTop) * (H - M.top - M.bottom);
  const yFormat = o.yFormat || compact;

  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
    "aria-label": o.summary || "" });
  const grid = svgEl("g", { class: "grid" }, svg);
  const axis = svgEl("g", { class: "axis" }, svg);
  for (let v = 0; v <= yTop + 1e-9; v += yStep) {
    svgEl("line", { x1: M.left, x2: W - M.right, y1: y(v), y2: y(v) }, grid);
    svgEl("text", { x: M.left - 8, y: y(v) + 4, "text-anchor": "end" }, axis).textContent =
      yFormat(v);
  }
  const firstYear = new Date(x0).getFullYear(), lastYear = new Date(x1).getFullYear();
  const span = lastYear - firstYear;
  const yearStep = Math.max(span > 20 ? 5 : 1, niceStep(Math.max(span, 1), W < 480 ? 3 : 6));
  for (let yr = Math.ceil(firstYear / yearStep) * yearStep; yr <= lastYear; yr += yearStep) {
    const t = new Date(`${yr}-01-01T00:00:00`).getTime();
    if (t < x0 - 1) continue;
    svgEl("text", { x: x(t), y: H - 8, "text-anchor": "middle" }, axis).textContent = yr;
  }

  const lines = svgEl("g", { class: "series" }, svg);
  const ends = [];
  for (const s of o.series) {
    let d = "", pen = false, last = null;
    s.values.forEach((v, i) => {
      if (v === null || v === undefined) { pen = false; return; }
      d += `${pen ? "L" : "M"}${x(times[i]).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
      last = { v, y: y(v) };
    });
    svgEl("path", { d, stroke: s.color }, lines);
    if (last) ends.push({ s, ...last });
  }
  if (labels) {
    // direct labels at the line ends, nudged apart so they don't overlap
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) {
      if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;
    }
    for (const e of ends) {
      svgEl("text", { class: "end-label", x: W - M.right + 8, y: e.y + 4 }, svg).textContent =
        e.s.label;
    }
  }

  // crosshair and tooltip: the nearest point to the pointer (or the arrow keys)
  const cross = svgEl("line", { class: "crosshair", y1: M.top, y2: H - M.bottom,
    visibility: "hidden" }, svg);
  const dots = o.series.map((s) => svgEl("circle", { class: "dot", r: 4, fill: s.color,
    visibility: "hidden" }, svg));
  const tip = document.createElement("div");
  tip.className = "tooltip";
  tip.hidden = true;
  box.append(svg, tip);

  let current = null;
  const show = (i) => {
    current = i;
    const cx = x(times[i]);
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx);
    cross.setAttribute("visibility", "visible");
    o.series.forEach((s, k) => {
      const v = s.values[i];
      const shown = v !== null && v !== undefined;
      dots[k].setAttribute("visibility", shown ? "visible" : "hidden");
      if (shown) { dots[k].setAttribute("cx", cx); dots[k].setAttribute("cy", y(v)); }
    });
    tip.innerHTML = o.tip(i);
    tip.hidden = false;
    const rect = svg.getBoundingClientRect(), k = rect.width / W;
    tip.style.left = `${Math.min(cx * k + 12, rect.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `${M.top * k}px`;
  };
  const hide = () => {
    current = null; tip.hidden = true; cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
  };
  const nearest = (clientX) => {
    const rect = svg.getBoundingClientRect();
    const t = x0 + ((clientX - rect.left) / (rect.width / W) - M.left)
      / (W - M.left - M.right) * (x1 - x0);
    let best = 0;
    times.forEach((tt, i) => { if (Math.abs(tt - t) < Math.abs(times[best] - t)) best = i; });
    return best;
  };
  svg.addEventListener("pointermove", (e) => show(nearest(e.clientX)));
  // a tap without a drag moves no pointer: show the point nearest the finger
  svg.addEventListener("pointerdown", (e) => { if (e.pointerType === "touch") show(nearest(e.clientX)); });
  // a finger lifting off "leaves" too: on a touch screen the tooltip stays until a tap elsewhere
  svg.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") hide(); });
  outside.set(box, { inside: (t) => svg.contains(t), close: hide });
  svg.tabIndex = 0;
  // from the keyboard only: a tap focuses the chart too, and nothing would close it
  svg.addEventListener("focus", () => { if (svg.matches(":focus-visible")) show(times.length - 1); });
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const i = current ?? times.length - 1;
    show(Math.max(0, Math.min(times.length - 1, i + (e.key === "ArrowLeft" ? -1 : 1))));
  });
  return { hide };
}

// redraw when the box changes width (not height: a phone's toolbar showing and hiding)
export function onWidthChange(box, redraw) {
  let timer = null, last = box.clientWidth;
  window.addEventListener("resize", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      const w = box.clientWidth;
      if (Math.abs(w - last) > 8) { last = w; redraw(); }
    }, 150);
  });
}

// ---- county search box ------------------------------------------------------------------
// options: {input, list, statewide (a button), counties ({geoid: {name}}), onChoose(geoid|null)}
// returns {show(geoid)}: puts a county's name in the box without choosing it again
export function countyPicker({ input, list, statewide, counties, onChoose }) {
  let options = [], active = -1;

  const matching = (query) => {
    const q = query.trim().toLowerCase();
    return Object.entries(counties)
      .filter(([, c]) => !q || c.name.toLowerCase().includes(q))
      .sort((a, b) => a[1].name.localeCompare(b[1].name));
  };
  const open = () => {
    options = matching(input.value);
    active = options.length ? 0 : -1;
    list.innerHTML = options.map(([geoid, c], i) => `<li role="option" id="opt-${i}"
      data-geoid="${geoid}" aria-selected="${i === active}">${c.name}</li>`).join("")
      || '<li role="option" aria-disabled="true">No county by that name</li>';
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-activedescendant", active >= 0 ? `opt-${active}` : "");
  };
  const close = () => {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  };
  const setActive = (i) => {
    active = i;
    [...list.children].forEach((li, k) => li.setAttribute("aria-selected", k === i));
    const li = list.children[i];
    if (li) { li.scrollIntoView({ block: "nearest" }); input.setAttribute("aria-activedescendant", li.id); }
  };
  const choose = (geoid) => { close(); onChoose(geoid); };

  input.addEventListener("input", open);
  input.addEventListener("focus", open);
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) open();
      if (options.length) setActive((active + (e.key === "ArrowDown" ? 1 : -1) + options.length)
        % options.length);
    } else if (e.key === "Enter" && active >= 0 && options[active]) {
      e.preventDefault(); choose(options[active][0]);
    } else if (e.key === "Escape") { close(); }
  });
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-geoid]");
    if (li) { e.preventDefault(); choose(li.dataset.geoid); }
  });
  statewide?.addEventListener("click", () => choose(null));
  return { show: (geoid) => { input.value = geoid && counties[geoid] ? counties[geoid].name : ""; } };
}

// ---- county map -------------------------------------------------------------------------
// options: {box (the element holding the map's SVG), onChoose(geoid), tip(geoid) → HTML}
// returns {paint(fill(geoid) → CSS color, label(geoid) → text), select(geoid)}
export function countyMap({ box, onChoose, tip }) {
  const svg = box.querySelector("svg");
  const paths = [...box.querySelectorAll("path[data-geoid]")];
  // Outlines for the hovered and selected counties, drawn over the map and ignoring the
  // pointer, so the counties themselves never move (moving one under the pointer would
  // swallow the click)
  const makeRing = (cls) => {
    const r = document.createElementNS(SVGNS, "path");
    r.setAttribute("class", `ring ${cls}`);
    r.setAttribute("aria-hidden", "true");
    r.style.display = "none";
    return r;
  };
  const hoverRing = makeRing("ring-hover"), selectedRing = makeRing("ring-selected");
  const ring = (r, path) => {
    if (!path) { r.style.display = "none"; return; }
    r.setAttribute("d", path.getAttribute("d"));
    r.style.display = "";
  };

  const tipEl = document.createElement("div");
  tipEl.className = "tooltip";
  tipEl.hidden = true;
  box.append(tipEl);
  let tipFor = null;  // the county the tooltip is showing

  const showTip = (path, e) => {
    tipFor = path;
    tipEl.innerHTML = tip(path.dataset.geoid);
    tipEl.hidden = false;
    const b = box.getBoundingClientRect(), r = path.getBoundingClientRect();
    const px = e && e.clientX ? e.clientX - b.left : r.left + r.width / 2 - b.left;
    const py = e && e.clientY ? e.clientY - b.top : r.top + r.height / 2 - b.top;
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    tipEl.style.left = `${Math.max(0, Math.min(px + 12, b.width - w))}px`;
    tipEl.style.top = `${py + 16 + h > b.height ? Math.max(0, py - h - 12) : py + 16}px`;
  };
  const hideTip = () => { tipEl.hidden = true; tipFor = null; ring(hoverRing, null); };

  for (const path of paths) {
    path.setAttribute("tabindex", "0");
    path.setAttribute("role", "button");
    path.addEventListener("click", () => onChoose(path.dataset.geoid));
    path.addEventListener("pointerenter", (e) => { ring(hoverRing, path); showTip(path, e); });
    path.addEventListener("pointermove", (e) => showTip(path, e));
    path.addEventListener("pointerleave", (e) => { if (e.pointerType !== "touch") hideTip(); });
    path.addEventListener("focus", () => {
      if (path.matches(":focus-visible")) { ring(hoverRing, path); showTip(path); }
    });
    // (only its own tooltip: tapping the next county moves focus after showing that one's)
    path.addEventListener("blur", () => { if (tipFor === path) hideTip(); });
    path.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onChoose(path.dataset.geoid); }
    });
  }
  svg.append(hoverRing, selectedRing);
  outside.set(box, { inside: (t) => !!t.closest?.("path[data-geoid]") && box.contains(t),
    close: hideTip });

  return {
    paint(fill, label) {
      for (const path of paths) {
        path.style.fill = fill(path.dataset.geoid);
        path.setAttribute("aria-label", label(path.dataset.geoid, path.dataset.name));
      }
      if (tipFor) showTip(tipFor);
    },
    select(geoid) {
      let chosen = null;
      for (const p of paths) {
        const selected = p.dataset.geoid === geoid;
        p.classList.toggle("selected", selected);
        p.setAttribute("aria-pressed", selected);
        if (selected) chosen = p;
      }
      ring(selectedRing, chosen);
    },
  };
}

// steps for a sequential map: breaks at the quintiles of every value it will show, so the
// colors mean the same in every period
export function quintiles(values) {
  const v = values.filter((x) => x !== null && x !== undefined && Number.isFinite(x))
    .sort((a, b) => a - b);
  if (!v.length) return [];
  return [0.2, 0.4, 0.6, 0.8].map((q) => v[Math.min(v.length - 1, Math.floor(q * v.length))]);
}
export const step = (breaks, value) => breaks.filter((b) => value >= b).length + 1;

// ---- scrubber ---------------------------------------------------------------------------
// options: {range, output, latest (a button), label(i) → text, onChange(i)}
// returns {set(i)}
export function scrubber({ range, output, latest, label, onChange }) {
  const last = () => Number(range.max);
  const set = (i) => {
    range.value = i;
    const text = label(i);
    output.textContent = text;
    range.setAttribute("aria-valuetext", text);
    if (latest) latest.hidden = i === last();
    onChange(i);
  };
  range.addEventListener("input", () => set(Number(range.value)));
  latest?.addEventListener("click", () => set(last()));
  return { set };
}

// ---- the ?county= link ------------------------------------------------------------------
export function urlCounty(counties) {
  const fips = new URLSearchParams(location.search).get("county");
  const geoid = fips && `0500000US${fips}`;
  return geoid && counties[geoid] ? geoid : null;
}
export function setUrlCounty(geoid) {
  const url = new URL(location.href);
  if (geoid) url.searchParams.set("county", geoid.slice(-5));
  else url.searchParams.delete("county");
  history.replaceState(null, "", url);
}
