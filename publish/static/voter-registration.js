// South Dakota voter registration by party: tiles, a line chart (voters or share), a county
// picker (search and map) and the table of every report. No dependencies.
(() => {
  "use strict";
  const PARTIES = ["republican", "democratic", "other"];
  const LABEL = { republican: "Republican", democratic: "Democratic", other: "Other" };
  const ELECTION = { primary: "Primary", general: "General", special: "Special",
    "presidential primary": "Presidential primary" };
  const fmt = new Intl.NumberFormat("en-US");
  const pct = (v) => `${v.toFixed(1)}%`;
  const $ = (id) => document.getElementById(id);
  const SVGNS = "http://www.w3.org/2000/svg";
  const state = { data: null, place: null, mode: "count" };

  const parseDate = (s) => new Date(`${s}T12:00:00`);
  const longDate = (s) => parseDate(s).toLocaleDateString("en-US",
    { year: "numeric", month: "long", day: "numeric" });

  function seriesFor(place) {
    return place ? state.data.counties[place] : state.data.state;
  }

  function rowsFor(place) {
    const s = seriesFor(place);
    return state.data.dates.map((date, i) => {
      const values = Object.fromEntries(PARTIES.map((p) => [p, s[p][i]]));
      const known = PARTIES.filter((p) => values[p] !== null);
      const total = known.length ? known.reduce((a, p) => a + values[p], 0) : null;
      return { date, i, values, total };
    });
  }

  // ---- tiles ---------------------------------------------------------------------------
  function renderTiles(rows) {
    const last = [...rows].reverse().find((r) => r.total);
    const tiles = $("tiles");
    tiles.innerHTML = "";
    if (!last) return;
    const add = (label, value, sub, party) => {
      const el = document.createElement("div");
      el.className = "card tile";
      el.innerHTML = `<div class="label">${party
        ? `<i class="swatch" style="background:var(--${party})"></i>` : ""}${label}</div>
        <div class="value">${value}</div><div class="sub">${sub}</div>`;
      tiles.append(el);
    };
    // the place and date go in the heading above, so each tile keeps one short line
    const where = state.place ? `${state.data.counties[state.place].name} County` : "Statewide";
    $("tiles-heading").innerHTML = `<span>${where}</span> <span class="when"><span class="sep">· `
      + `</span>latest report, ${longDate(last.date)}</span>`;
    add("Active voters", fmt.format(last.total), "All parties");
    for (const p of PARTIES) {
      const v = last.values[p] ?? 0;
      add(LABEL[p], fmt.format(v), `${pct((100 * v) / last.total)} of active voters`, p);
    }
  }

  // ---- chart ---------------------------------------------------------------------------
  function niceStep(span, target) {
    const raw = span / target;
    const mag = 10 ** Math.floor(Math.log10(raw));
    const n = raw / mag;
    return (n >= 5 ? 10 : n >= 2 ? 5 : n >= 1 ? 2 : 1) * mag;
  }

  function el(name, attrs, parent) {
    const node = document.createElementNS(SVGNS, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (parent) parent.append(node);
    return node;
  }

  function renderChart(rows) {
    const box = $("chart");
    box.innerHTML = "";
    // drawn at the card's own width, so text stays its real size on a phone
    const W = Math.max(300, Math.round(box.clientWidth));
    const H = Math.round(Math.min(400, Math.max(260, W * 0.55)));
    const M = { top: 12, right: 84, bottom: 30, left: W < 480 ? 44 : 60 };
    const value = (r, p) => {
      const v = r.values[p];
      if (v === null || !r.total) return null;
      return state.mode === "percent" ? (100 * v) / r.total : v;
    };
    const times = rows.map((r) => parseDate(r.date).getTime());
    const x0 = Math.min(...times), x1 = Math.max(...times);
    const x = (t) => M.left + ((t - x0) / (x1 - x0)) * (W - M.left - M.right);
    const all = rows.flatMap((r) => PARTIES.map((p) => value(r, p))).filter((v) => v !== null);
    const yMax = state.mode === "percent" ? Math.max(60, Math.ceil(Math.max(...all) / 10) * 10)
      : Math.max(...all) * 1.08;
    const yStep = niceStep(yMax, 5);
    const yTop = Math.ceil(yMax / yStep) * yStep;
    const y = (v) => H - M.bottom - (v / yTop) * (H - M.top - M.bottom);

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img",
      "aria-label": chartSummary(rows) });
    const grid = el("g", { class: "grid" }, svg);
    const axis = el("g", { class: "axis" }, svg);
    for (let v = 0; v <= yTop + 1e-9; v += yStep) {
      el("line", { x1: M.left, x2: W - M.right, y1: y(v), y2: y(v) }, grid);
      const t = el("text", { x: M.left - 8, y: y(v) + 4, "text-anchor": "end" }, axis);
      t.textContent = state.mode === "percent" ? `${v}%` : compact(v);
    }
    const firstYear = new Date(x0).getFullYear(), lastYear = new Date(x1).getFullYear();
    const target = W < 480 ? 3 : 6;
    const yearStep = Math.max(5, niceStep(lastYear - firstYear, target));
    for (let yr = Math.ceil(firstYear / yearStep) * yearStep; yr <= lastYear; yr += yearStep) {
      const t = el("text", { x: x(new Date(`${yr}-01-01T00:00:00`).getTime()), y: H - 8,
        "text-anchor": "middle" }, axis);
      t.textContent = yr;
    }

    const series = el("g", { class: "series" }, svg);
    const ends = [];
    for (const p of PARTIES) {
      let d = "", pen = false, last = null;
      rows.forEach((r, i) => {
        const v = value(r, p);
        if (v === null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(times[i]).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
        last = { v, y: y(v) };
      });
      el("path", { d, stroke: `var(--${p})` }, series);
      if (last) ends.push({ p, ...last });
    }
    // direct labels at the line ends, nudged apart so they don't overlap
    ends.sort((a, b) => a.y - b.y);
    for (let i = 1; i < ends.length; i++) {
      if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;
    }
    for (const e of ends) {
      const t = el("text", { class: "end-label", x: W - M.right + 8, y: e.y + 4 }, svg);
      t.textContent = LABEL[e.p];
    }

    // crosshair and tooltip: the nearest report to the pointer (or the arrow keys)
    const cross = el("line", { class: "crosshair", y1: M.top, y2: H - M.bottom,
      visibility: "hidden" }, svg);
    const dots = PARTIES.map((p) => el("circle", { class: "dot", r: 4, fill: `var(--${p})`,
      visibility: "hidden" }, svg));
    const tip = document.createElement("div");
    tip.className = "tooltip";
    tip.hidden = true;
    box.append(svg, tip);

    let current = null;
    const show = (i) => {
      current = i;
      const r = rows[i], cx = x(times[i]);
      cross.setAttribute("x1", cx); cross.setAttribute("x2", cx);
      cross.setAttribute("visibility", "visible");
      PARTIES.forEach((p, k) => {
        const v = value(r, p);
        dots[k].setAttribute("visibility", v === null ? "hidden" : "visible");
        if (v !== null) { dots[k].setAttribute("cx", cx); dots[k].setAttribute("cy", y(v)); }
      });
      const election = state.data.elections[r.i];
      tip.innerHTML = `<div class="t-head">${longDate(r.date)}${election
        ? ` · ${ELECTION[election] || election}` : ""}</div>` + PARTIES.map((p) => {
        const v = r.values[p];
        const shown = v === null ? "n/a" : state.mode === "percent"
          ? pct((100 * v) / r.total) : fmt.format(v);
        return `<div class="t-row"><i class="swatch" style="background:var(--${p})"></i>
          ${LABEL[p]}<b>${shown}</b></div>`;
      }).join("") + `<div class="t-row muted">Active voters<b>${r.total
        ? fmt.format(r.total) : "n/a"}</b></div>`;
      tip.hidden = false;
      const rect = svg.getBoundingClientRect(), k = rect.width / W;
      const left = cx * k + 12;
      tip.style.left = `${Math.min(left, rect.width - tip.offsetWidth - 4)}px`;
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
    svg.addEventListener("pointerleave", hide);
    svg.tabIndex = 0;
    svg.addEventListener("focus", () => show(rows.length - 1));
    svg.addEventListener("blur", hide);
    svg.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      e.preventDefault();
      const i = current ?? rows.length - 1;
      show(Math.max(0, Math.min(rows.length - 1, i + (e.key === "ArrowLeft" ? -1 : 1))));
    });
  }

  function compact(v) {
    if (v >= 1e6) return `${(v / 1e6).toFixed(v % 1e6 ? 1 : 0)}M`;
    if (v >= 1e3) return `${(v / 1e3).toFixed(v % 1e3 ? 1 : 0)}K`;
    return String(v);
  }

  function chartSummary(rows) {
    const known = rows.filter((r) => r.total);
    const a = known[0], b = known[known.length - 1];
    const where = state.place ? `${state.data.counties[state.place].name} County` : "South Dakota";
    const part = (r) => PARTIES.map((p) => `${LABEL[p]} ${fmt.format(r.values[p] ?? 0)}`)
      .join(", ");
    return `Line chart of registered voters by party in ${where}, ${longDate(a.date)} to `
      + `${longDate(b.date)}. First report: ${part(a)}. Latest: ${part(b)}. `
      + "The table below lists every report.";
  }

  // ---- table ---------------------------------------------------------------------------
  function renderTable(rows) {
    const body = $("figures");
    body.innerHTML = rows.slice().reverse().filter((r) => r.total).map((r) => {
      const source = state.data.sources[r.i], copy = state.data.archived[r.i];
      const election = state.data.elections[r.i];
      const cells = PARTIES.map((p) => {
        const v = r.values[p];
        return `<td>${v === null ? "n/a" : state.mode === "percent"
          ? pct((100 * v) / r.total) : fmt.format(v)}</td>`;
      }).join("");
      const links = source === copy ? `<a href="${copy}">Report</a>`
        : `<a href="${source}">Report</a> · <a href="${copy}">copy</a>`;
      return `<tr><td>${longDate(r.date)}</td><td class="left">${election
        ? ELECTION[election] || election : ""}</td>${cells}<td>${fmt.format(r.total)}</td>
        <td class="left">${links}</td></tr>`;
    }).join("");
  }

  // ---- county picker: search and map -------------------------------------------------
  const input = $("county-input"), list = $("county-list");
  let options = [], active = -1;

  function countyOptions(query) {
    const q = query.trim().toLowerCase();
    return Object.entries(state.data.counties)
      .filter(([, c]) => !q || c.name.toLowerCase().includes(q))
      .sort((a, b) => a[1].name.localeCompare(b[1].name));
  }

  function openList() {
    options = countyOptions(input.value);
    active = options.length ? 0 : -1;
    list.innerHTML = options.map(([geoid, c], i) => `<li role="option" id="opt-${i}"
      data-geoid="${geoid}" aria-selected="${i === active}">${c.name}</li>`).join("")
      || '<li role="option" aria-disabled="true">No county by that name</li>';
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-activedescendant", active >= 0 ? `opt-${active}` : "");
  }

  function closeList() {
    list.hidden = true;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function setActive(i) {
    active = i;
    [...list.children].forEach((li, k) => li.setAttribute("aria-selected", k === i));
    const li = list.children[i];
    if (li) { li.scrollIntoView({ block: "nearest" }); input.setAttribute("aria-activedescendant", li.id); }
  }

  input.addEventListener("input", openList);
  input.addEventListener("focus", openList);
  input.addEventListener("blur", () => setTimeout(closeList, 120));
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (list.hidden) openList();
      if (options.length) setActive((active + (e.key === "ArrowDown" ? 1 : -1) + options.length)
        % options.length);
    } else if (e.key === "Enter" && active >= 0 && options[active]) {
      e.preventDefault(); choose(options[active][0]);
    } else if (e.key === "Escape") { closeList(); }
  });
  list.addEventListener("mousedown", (e) => {
    const li = e.target.closest("li[data-geoid]");
    if (li) { e.preventDefault(); choose(li.dataset.geoid); }
  });
  $("statewide").addEventListener("click", () => choose(null));

  // Outlines for the hovered and selected counties, drawn over the map and ignoring the
  // pointer, so the counties themselves never move (moving one under the pointer would
  // swallow the click)
  const mapSvg = document.querySelector(".map svg");
  const makeRing = (cls) => {
    const r = document.createElementNS(SVGNS, "path");
    r.setAttribute("class", `ring ${cls}`);
    r.setAttribute("aria-hidden", "true");
    r.style.display = "none";
    return r;
  };
  const hoverRing = makeRing("ring-hover"), selectedRing = makeRing("ring-selected");
  function ring(r, path) {
    if (!mapSvg) return;
    if (!path) { r.style.display = "none"; return; }
    r.setAttribute("d", path.getAttribute("d"));
    r.style.display = "";
  }

  document.querySelectorAll(".map path[data-geoid]").forEach((path) => {
    path.setAttribute("tabindex", "0");
    path.setAttribute("role", "button");
    path.setAttribute("aria-label", `${path.dataset.name} County`);
    path.addEventListener("click", () => choose(path.dataset.geoid));
    path.addEventListener("pointerenter", () => ring(hoverRing, path));
    path.addEventListener("pointerleave", () => ring(hoverRing, null));
    path.addEventListener("focus", () => ring(hoverRing, path));
    path.addEventListener("blur", () => ring(hoverRing, null));
    path.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(path.dataset.geoid); }
    });
  });

  if (mapSvg) mapSvg.append(hoverRing, selectedRing);

  document.querySelectorAll(".segmented button").forEach((b) => {
    b.addEventListener("click", () => {
      state.mode = b.dataset.mode;
      document.querySelectorAll(".segmented button").forEach((o) =>
        o.setAttribute("aria-pressed", o === b));
      render();
    });
  });

  function choose(geoid) {
    state.place = geoid && state.data.counties[geoid] ? geoid : null;
    input.value = state.place ? state.data.counties[state.place].name : "";
    closeList();
    const url = new URL(location.href);
    if (state.place) url.searchParams.set("county", state.place.slice(-5));
    else url.searchParams.delete("county");
    history.replaceState(null, "", url);
    render();
  }

  function render() {
    const rows = rowsFor(state.place);
    const name = state.place ? `${state.data.counties[state.place].name} County` : "Statewide";
    $("chart-title").innerHTML = `<span>${name}</span><span class="what"><span class="sep">: `
      + `</span>${state.mode === "percent" ? "share of active voters by party"
        : "active voters by party"}</span>`;
    $("map-title").textContent = state.place ? `${name} selected` : "Choose a county";
    let chosen = null;
    document.querySelectorAll(".map path[data-geoid]").forEach((p) => {
      const selected = p.dataset.geoid === state.place;
      p.classList.toggle("selected", selected);
      p.setAttribute("aria-pressed", selected);
      if (selected) chosen = p;
    });
    ring(selectedRing, chosen);
    renderTiles(rows);
    renderChart(rows);
    renderTable(rows);
  }

  let resizing = null, lastWidth = 0;
  window.addEventListener("resize", () => {
    clearTimeout(resizing);
    resizing = setTimeout(() => {
      const w = $("chart").clientWidth;
      if (state.data && Math.abs(w - lastWidth) > 8) { lastWidth = w; renderChart(rowsFor(state.place)); }
    }, 150);
  });

  fetch(window.VR_DATA).then((r) => r.json()).then((data) => {
    state.data = data;
    const fips = new URLSearchParams(location.search).get("county");
    const geoid = fips && `0500000US${fips}`;
    state.place = geoid && data.counties[geoid] ? geoid : null;
    if (state.place) input.value = data.counties[state.place].name;
    render();
  });
})();
