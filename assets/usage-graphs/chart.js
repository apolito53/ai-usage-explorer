"use strict";

function modelLabel(model) { return model ? model.replace(/^claude-/, "") : "Unattributed"; }
function filteredCost(row, selected) {
  return selected === null ? row.total : Object.entries(row.models || {})
    .reduce((sum, [model, cost]) => sum + (selected.has(model) ? cost : 0), 0);
}
function chartValues(rows, selected, cumulative) {
  let total = 0;
  return rows.map(row => { const value = filteredCost(row, selected); total += value; return cumulative ? total : value; });
}
function chartScale(values) {
  const peak = Math.max(0, ...values);
  if (!peak) return 0.01;
  const target = Math.max(peak, 0.000004) / 4;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  return [1, 2, 5, 10].find(n => n * magnitude >= target) * magnitude * 4;
}
function headerTotals(usage, month, selected) {
  const current = usage.today.slice(0, 7);
  const today = (usage.months[current] || []).find(row => row.date === usage.today);
  return {
    month: selected === null && month === current ? usage.monthTotal :
      (usage.months[month] || []).reduce((sum, row) => sum + filteredCost(row, selected), 0),
    today: selected === null ? usage.todayTotal : today ? filteredCost(today, selected) : 0,
  };
}

if (typeof module !== "undefined") module.exports = { filteredCost, chartValues, chartScale, headerTotals };

if (typeof document !== "undefined") {
  const byId = id => document.getElementById(id);
  const svgNS = "http://www.w3.org/2000/svg";
  const palette = ["#209b87", "#598cdb", "#db9b30", "#ac7fc6", "#d86778", "#9aab43"];
  const colors = new Map([["", "#899b95"]]);
  const state = { usage: null, month: null, view: "month", day: null, interval: 60,
    selected: null, monthlyCumulative: false, dayCumulative: false, rows: [], dayData: null,
    inspected: null, loadId: 0, loading: false, refreshing: false, requestKey: "" };
  let plot = null;
  const money = n => new Intl.NumberFormat(undefined, { style: "currency", currency: "USD",
    minimumFractionDigits: n > 0 && n < .01 ? 4 : 2, maximumFractionDigits: n > 0 && n < .01 ? 4 : 2 }).format(n);
  const localDate = iso => new Date(iso + "T12:00:00");
  const monthName = value => localDate(value + "-01").toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const dateName = value => localDate(value).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  const cumulative = () => state.view === "month" ? state.monthlyCumulative : state.dayCumulative;
  function color(model) {
    if (!colors.has(model)) { const n = colors.size - 1; colors.set(model, palette[n] || `hsl(${n * 137.508 % 360}, 52%, 55%)`); }
    return colors.get(model);
  }
  function svg(tag, attrs = {}, text = "") {
    const node = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (text) node.textContent = text;
    return node;
  }
  function setStatus(message, error = false) {
    byId("status").textContent = message;
    byId("status").classList.toggle("error", error);
  }
  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 8000);
    try {
      const response = await fetch(path, { cache: "no-store", ...options, signal: controller.signal });
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      return response.status === 202 ? null : await response.json();
    } finally { clearTimeout(timer); }
  }
  function syncModels() {
    const models = [...new Set([...state.usage.models, ...(state.selected || [])])].sort();
    const key = JSON.stringify(models);
    const list = byId("model-list");
    if (list.dataset.models !== key) {
      list.replaceChildren(); list.dataset.models = key;
      for (const model of models) {
        const row = document.createElement("div"); row.className = "model-row";
        const check = document.createElement("input"); check.type = "checkbox"; check.dataset.model = model;
        check.setAttribute("aria-label", "Include " + modelLabel(model));
        check.addEventListener("change", () => {
          if (state.selected === null) state.selected = new Set(models);
          if (check.checked) state.selected.add(model); else state.selected.delete(model);
          render();
        });
        const button = document.createElement("button"); button.type = "button";
        button.title = "Show only " + modelLabel(model);
        const swatch = svg("svg", { class: "swatch", viewBox: "0 0 12 12", "aria-hidden": "true" });
        swatch.append(svg("rect", { width: 12, height: 12, rx: 3, fill: color(model) }));
        const text = document.createElement("span"); text.textContent = modelLabel(model);
        button.append(swatch, text); button.addEventListener("click", () => { state.selected = new Set([model]); render(); });
        row.append(check, button); list.append(row);
      }
    }
    for (const check of list.querySelectorAll("input")) check.checked = state.selected === null || state.selected.has(check.dataset.model);
    const caption = state.selected === null ? "All" : state.selected.size === 0 ? "None" :
      state.selected.size === 1 ? modelLabel([...state.selected][0]) : `${state.selected.size} selected`;
    byId("model-caption").textContent = `Models: ${state.selected === null ? "All" : state.selected.size}`;
    byId("selection").textContent = `${caption === "All" ? "All models" : caption} · USD`;
  }
  function syncControls() {
    const usage = state.usage;
    const current = usage.today.slice(0, 7);
    const months = Object.keys(usage.months).sort().reverse();
    const chosen = state.month || current;
    if (byId("month").dataset.months !== JSON.stringify(months)) {
      byId("month").replaceChildren(...months.map(month => new Option(monthName(month), month)));
      byId("month").dataset.months = JSON.stringify(months);
    }
    byId("month").value = chosen;
    byId("month-control").hidden = state.view !== "month";
    byId("interval-control").hidden = state.view === "month";
    byId("month-view").textContent = state.view === "day" ? "← Monthly" : "Monthly";
    byId("month-view").setAttribute("aria-pressed", state.view === "month");
    byId("today-view").setAttribute("aria-pressed", state.view === "today");
    byId("cumulative").checked = cumulative();
    byId("heading").textContent = state.view === "month" ? monthName(chosen) :
      `${state.view === "today" ? "Today · " : ""}${dateName(state.view === "today" ? usage.today : state.day)}`;
    const totals = headerTotals(usage, chosen, state.selected);
    byId("month-caption").textContent = chosen === current ? "Month to date" : monthName(chosen);
    byId("month-total").textContent = money(totals.month);
    byId("today-total").textContent = money(totals.today);
    byId("view-caption").textContent = state.view === "month" ? "Selected month" : "Timestamped usage";
    byId("project").hidden = !usage.project; byId("project").textContent = usage.project ? "Project: " + usage.project : "";
    syncModels();
  }
  function detail() {
    const rows = state.rows;
    const old = byId("chart").querySelector(".cursor"); if (old) old.remove();
    if (!rows.length) { byId("detail").textContent = "No readings to inspect."; return; }
    const index = Math.min(rows.length - 1, state.inspected === null ? rows.length - 1 : state.inspected);
    const row = rows[index], values = chartValues(rows, state.selected, cumulative());
    const label = state.view === "month" ? dateName(row.date) : row.range;
    let text = `${cumulative() ? "Cumulative · " : ""}${label} · ${money(values[index])}`;
    if (!cumulative()) {
      const parts = Object.entries(row.models || {}).filter(([model]) => state.selected === null || state.selected.has(model));
      if (parts.length) text += " · " + parts.map(([model, cost]) => `${modelLabel(model)} ${money(cost)}`).join(" · ");
    }
    if (state.view === "month") text += row.details ? " · Enter or click for day details" : " · Daily total only";
    byId("detail").textContent = text;
    if (plot && state.inspected !== null) {
      const x = plot.left + (index + .5) * plot.width / rows.length;
      byId("chart").append(svg("line", { x1: x, x2: x, y1: plot.top, y2: plot.bottom, class: "cursor" }));
    }
  }
  function draw() {
    const chart = byId("chart"); chart.replaceChildren(); plot = null;
    const width = Math.max(250, chart.clientWidth), height = chart.clientHeight;
    chart.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const values = chartValues(state.rows, state.selected, cumulative());
    const ceiling = chartScale(values);
    const left = Math.max(62, money(ceiling).length * 7 + 12), right = width - 12, top = 20, bottom = height - 34;
    const w = right - left, h = bottom - top;
    plot = { left, width: w, top, bottom };
    for (let i = 0; i <= 4; i++) {
      const y = bottom - h * i / 4;
      chart.append(svg("line", { x1: left, x2: right, y1: y, y2: y, class: "grid" }));
      chart.append(svg("text", { x: left - 10, y: y + 4, "text-anchor": "end" }, money(ceiling * i / 4)));
    }
    if (values.length) {
      const step = w / values.length;
      const points = values.map((value, i) => [left + (i + .5) * step, bottom - h * value / ceiling]);
      if (cumulative()) {
        const coords = points.map(p => p.join(",")).join(" ");
        chart.append(svg("polygon", { points: `${points[0][0]},${bottom} ${coords} ${points[points.length - 1][0]},${bottom}`, fill: "var(--accent)", opacity: .18 }));
        chart.append(svg("polyline", { points: coords, fill: "none", stroke: "var(--accent)", "stroke-width": 2.5 }));
        if (points.length === 1) chart.append(svg("circle", { cx: points[0][0], cy: points[0][1], r: 4, fill: "var(--accent)" }));
      } else {
        state.rows.forEach((row, i) => {
          let stacked = 0;
          for (const [model, cost] of Object.entries(row.models || {}).sort()) {
            if (state.selected !== null && !state.selected.has(model)) continue;
            const bh = h * cost / ceiling;
            chart.append(svg("rect", { x: left + i * step + step * .14, y: bottom - h * stacked / ceiling - bh,
              width: Math.max(.1, step * .72), height: bh, fill: color(model) }));
            stacked += cost;
          }
          if (state.view === "month" && row.details && values[i] > 0) chart.append(svg("rect", {
            x: left + i * step + step * .14, y: bottom - h * values[i] / ceiling,
            width: step * .72, height: h * values[i] / ceiling, fill: "none", stroke: "var(--text)", "stroke-width": 1,
          }));
        });
      }
      const stride = Math.max(1, Math.ceil(values.length / Math.max(2, Math.floor(w / 80))));
      for (let i = 0; i < values.length; i += stride) {
        const label = state.view === "month" ? String(Number(state.rows[i].date.slice(8))) : state.rows[i].label;
        chart.append(svg("text", { x: left + (i + .5) * step, y: bottom + 24, "text-anchor": "middle" }, label));
      }
    }
    if (!values.some(value => value > 0)) {
      const message = state.loading ? "Loading day details…" : state.dayData?.error ? "Day details unavailable" :
        state.selected !== null && state.selected.size === 0 ? "No models selected" : "No recorded spending";
      chart.append(svg("text", { x: left + w / 2, y: top + h / 2, "text-anchor": "middle" }, message));
    }
    detail();
  }
  function render() {
    if (!state.usage) return;
    syncControls();
    const month = state.month || state.usage.today.slice(0, 7);
    state.rows = state.view === "month" ? state.usage.months[month] || [] : state.dayData?.series || [];
    const total = state.rows.reduce((sum, row) => sum + filteredCost(row, state.selected), 0);
    byId("view-total").textContent = state.loading || state.dayData?.error ? "—" : money(total);
    let note = state.view === "month" ? "Colors show models. Outlined bars have day details: click or press Enter. Arrow keys inspect values." :
      state.dayData?.error || "Local time · " + (cumulative() ? "Running total since midnight" : "Spending per interval");
    if (state.view !== "month" && state.dayData && !state.dayData.error) {
      const day = state.view === "today" ? state.usage.today : state.day;
      const daily = (state.usage.months[day.slice(0, 7)] || []).find(row => row.date === day);
      if (daily && Math.abs(filteredCost(daily, state.selected) - total) >= .01)
        note += `\nTimestamped total: ${money(total)} · daily total: ${money(filteredCost(daily, state.selected))}`;
      if (state.dayData.unpriced) note += `\n${state.dayData.unpriced} usage records could not be priced.`;
      if (day === state.usage.today) note += " · Today is still in progress";
    }
    byId("note").textContent = note;
    draw();
  }
  async function loadDay() {
    const id = ++state.loadId;
    if (!state.usage || state.view === "month") { state.loading = false; state.dayData = null; render(); return; }
    const day = state.view === "today" ? state.usage.today : state.day;
    const key = `${day}/${state.interval}`;
    if (state.requestKey !== key) { state.dayData = null; state.inspected = null; }
    state.requestKey = key; state.loading = true; render();
    try {
      const data = await request(`day?date=${encodeURIComponent(day)}&interval=${state.interval}`);
      if (id !== state.loadId) return;
      state.dayData = data;
    } catch (_error) {
      if (id !== state.loadId) return;
      if (!state.dayData) state.dayData = { series: [], error: "Could not load day details. Keep the menu-bar app running and try Refresh now." };
      setStatus("Could not update day details. Showing the last available data.", true);
    } finally { if (id === state.loadId) { state.loading = false; render(); } }
  }
  let polling = false;
  async function poll() {
    if (polling) return;
    polling = true;
    try {
      const response = await request("data");
      state.refreshing = response.refreshing;
      byId("refresh").disabled = response.refreshing;
      byId("refresh").textContent = response.refreshing ? "Refreshing…" : "Refresh now";
      if (response.usage) {
        const changed = state.usage?.updated !== response.usage.updated;
        state.usage = response.usage;
        if (state.month && !(state.month in state.usage.months)) state.month = null;
        if (changed) await loadDay(); else render();
      }
      const updated = state.usage ? "Updated " + new Date(state.usage.updated).toLocaleString() : "Waiting for usage…";
      setStatus(response.status ? `${response.status}${state.usage ? " · " + updated : ""}` : updated,
        Boolean(response.status && !response.refreshing && state.usage));
    } catch (_error) {
      setStatus("Cannot reach the menu-bar app. Open Usage graphs from its menu again after restarting it. Last available data is shown.", true);
      byId("refresh").disabled = false; byId("refresh").textContent = "Refresh now";
    } finally { polling = false; }
  }
  function changeView(view) { state.view = view; state.inspected = null; loadDay(); }
  function openDay() {
    if (state.view !== "month" || state.inspected === null) return;
    const row = state.rows[state.inspected];
    if (row?.details) {
      state.day = row.date;
      state.month = row.date.slice(0, 7) === state.usage.today.slice(0, 7) ? null : row.date.slice(0, 7);
      changeView("day");
    }
  }
  byId("month-view").addEventListener("click", () => changeView("month"));
  byId("today-view").addEventListener("click", () => changeView("today"));
  byId("month").addEventListener("change", event => {
    state.month = event.target.value === state.usage.today.slice(0, 7) ? null : event.target.value;
    state.inspected = null; render();
  });
  byId("interval").addEventListener("change", event => { state.interval = Number(event.target.value); loadDay(); });
  byId("cumulative").addEventListener("change", event => {
    if (state.view === "month") state.monthlyCumulative = event.target.checked; else state.dayCumulative = event.target.checked;
    render();
  });
  byId("all-models").addEventListener("click", () => { state.selected = null; render(); });
  byId("no-models").addEventListener("click", () => { state.selected = new Set(); render(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape") byId("models").open = false; });
  document.addEventListener("click", event => { if (!byId("models").contains(event.target)) byId("models").open = false; });
  byId("refresh").addEventListener("click", async () => {
    byId("refresh").disabled = true; setStatus("Refreshing…");
    try { await request("refresh", { method: "POST", headers: { "Content-Type": "application/json" } }); }
    catch (_error) { setStatus("Could not request a refresh. Check that the menu-bar app is running.", true); }
    finally { byId("refresh").disabled = false; poll(); }
  });
  function inspect(event) {
    if (!plot || !state.rows.length) return;
    const x = event.clientX - byId("chart").getBoundingClientRect().left;
    state.inspected = Math.max(0, Math.min(state.rows.length - 1, Math.floor((x - plot.left) / plot.width * state.rows.length)));
    detail();
  }
  byId("chart").addEventListener("pointermove", inspect);
  byId("chart").addEventListener("click", event => { inspect(event); openDay(); });
  byId("chart").addEventListener("keydown", event => {
    if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const last = state.rows.length - 1;
      state.inspected = event.key === "Home" ? 0 : event.key === "End" ? last :
        Math.max(0, Math.min(last, (state.inspected === null ? last : state.inspected) + (event.key === "ArrowLeft" ? -1 : 1)));
      detail();
    } else if (event.key === "Enter") { event.preventDefault(); openDay(); }
    else if (event.key === "Escape" && state.view === "day") changeView("month");
  });
  window.addEventListener("resize", () => { if (state.usage) draw(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  setInterval(() => { if (!document.hidden) poll(); }, 5000);
  poll();
}
