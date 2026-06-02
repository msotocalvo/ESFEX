/* ESFEX Results Dashboard — Plotly frontend.
 *
 * State machine:
 *   QWebChannel handshake → bootstrap() → loader.get_meta()
 *      → populates system <select>, year range bounds, then refresh()
 *   refresh() → loader.get_overview(state)
 *      → updates KPI cards, repaints trajectory, repaints mix
 *
 * Crossfilter (year-range brush):
 *   Plotly emits `plotly_relayout` whenever the user drags a brush on
 *   the trajectory subplot. We extract the new xaxis range, clamp it
 *   to the available years, and re-fetch the overview with that range.
 *   The KPI cards and the mix chart then reflect the brushed window.
 *
 * Async model:
 *   Each loader method returns a JSON string (QWebChannel's safest
 *   wire format for nested dicts). We JSON.parse on receipt. All
 *   awaits go through a small `call` helper that promises-ifies the
 *   QWebChannel callback API.
 */
"use strict";

let bridge = null;
let meta = null;                  // {systems, years, system_default}
let theme = null;                 // {bg, text, accent, ...} from get_theme()
const state = {                   // current filter selection
    system: null,
    yearRange: null,              // [min, max] or null for all
};

// ─────────────────────────────────────────────────────────────────
// QWebChannel bootstrap
// ─────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
    setStatus("Connecting…");
    new QWebChannel(qt.webChannelTransport, channel => {
        bridge = channel.objects.loader;
        bootstrap().catch(err => {
            console.error("bootstrap failed", err);
            setStatus("Connection error: " + (err && err.message || err));
        });
    });
});

async function bootstrap() {
    // Theme first so the page repaints with the GUI's palette before
    // any data lands — avoids a flash of default-light styling on a
    // dark theme.
    try {
        theme = JSON.parse(await call(bridge.get_theme));
        applyTheme(theme);
    } catch (err) {
        console.warn("get_theme failed, using fallback CSS defaults", err);
    }

    meta = JSON.parse(await call(bridge.get_meta));
    if (!meta || !meta.systems || meta.systems.length === 0) {
        setStatus("No results loaded");
        return;
    }

    // System combo
    const sel = document.getElementById("system-select");
    sel.innerHTML = "";
    for (const s of meta.systems) {
        const opt = document.createElement("option");
        opt.value = s; opt.textContent = s;
        sel.appendChild(opt);
    }
    sel.value = meta.system_default || meta.systems[0];
    sel.addEventListener("change", () => {
        state.system = sel.value;
        state.yearRange = null;   // clear brush when system changes
        closeYearDetail();        // stale: detail was for the old system
        refresh();
    });
    state.system = sel.value;

    // Allow the host dialog to drive the system selection from its
    // sidebar combo. The host calls ``setActiveSystem(name)`` after
    // ``_on_system_changed`` to keep this view in sync; without it the
    // dashboard would keep rendering the previously-selected system
    // regardless of what the sidebar shows.
    window.setActiveSystem = function(name) {
        if (!name) return;
        // Update the (hidden) inner combo so it stays in sync, but
        // don't gate the state change on the option existing — the
        // host carries the canonical list and may know about systems
        // the bootstrap meta hasn't been refreshed for yet.
        const opt = Array.from(sel.options).find(o => o.value === name);
        if (opt) sel.value = name;
        const changed = state.system !== name;
        state.system = name;
        // Clear the brush — the previous system's range is meaningless
        // for the new one.
        state.yearRange = null;
        closeYearDetail();
        // Always refresh, even if name === state.system: the host may
        // be re-asserting the same system after a results-file reload
        // and the cards need to repaint regardless.
        refresh();
        return changed;
    };

    // Allow the host dialog to drive the year filter from the
    // top-toolbar slider. ``range`` is ``null`` (clear) or
    // ``[yMin, yMax]``.
    window.setYearRange = function(range) {
        if (range === null || range === undefined) {
            if (state.yearRange === null) return;
            state.yearRange = null;
        } else {
            const lo = Number(range[0]);
            const hi = Number(range[1]);
            if (!Number.isFinite(lo) || !Number.isFinite(hi)) return;
            if (state.yearRange
                && state.yearRange[0] === lo
                && state.yearRange[1] === hi) return;
            state.yearRange = [lo, hi];
        }
        closeYearDetail();
        refresh();
    };

    // Year range starts unfiltered (= full range).
    state.yearRange = null;
    updateRangeDisplay();

    // Reset button
    document.getElementById("reset-range").addEventListener("click", () => {
        state.yearRange = null;
        updateRangeDisplay();
        refresh();
        // Also tell Plotly to reset its zoom so the brush visualisation
        // matches the data state.
        Plotly.relayout("trajectory", {
            "xaxis.autorange": true,
        });
    });

    // Year drill-down close button
    const closeBtn = document.getElementById("year-detail-close");
    if (closeBtn) {
        closeBtn.addEventListener("click", closeYearDetail);
    }

    await refresh();
    setStatus("");
}

// ─────────────────────────────────────────────────────────────────
// Data refresh
// ─────────────────────────────────────────────────────────────────

async function refresh() {
    if (!bridge || !state.system) return;
    setStatus("Loading…");
    try {
        const payload = JSON.parse(await call(
            bridge.get_overview, JSON.stringify(state)
        ));
        const mode = payload.mode || (meta && meta.simulation_mode === "unit_commitment" ? "uc" : "planning");
        // UC and planning share the same DOM slots but render entirely
        // different content. Re-label the KPI cards and rebuild the
        // trajectory + mix panels each time so a system switch between
        // UC and planning swaps cleanly.
        applyKpiSchema(mode);
        renderKpis(payload.kpis || {});
        if (mode === "uc") {
            renderHourlyLmp(payload.trajectory || null);
            renderHourlyDispatch(payload.mix || null);
            // Year-detail drill-down only makes sense for multi-year
            // runs — hide it in UC.
            closeYearDetail();
        } else {
            renderTrajectory(payload.trajectory || null);
            renderMix(payload.mix || null);
        }
        renderCost(payload.cost || null);
        setStatus("");
    } catch (err) {
        console.error("refresh failed", err);
        setStatus("Refresh error: " + (err && err.message || err));
    }
}

// ─────────────────────────────────────────────────────────────────
// KPI schema — relabels cards in-place depending on the run mode.
// Planning uses the original {cost, re_share, co2, load_shed,
// investment}; UC swaps to {cost, avg_lmp, ens, re_share, battery_
// cycles}. Card data-key attrs are mutated so renderKpis can lookup
// against the right payload keys without a fork.
// ─────────────────────────────────────────────────────────────────

const _UC_KPI_SCHEMA = [
    { key: "cost",            label: "System Cost" },
    { key: "avg_lmp",         label: "Avg LMP" },
    { key: "ens",             label: "Unserved Energy" },
    { key: "re_share",        label: "RE Share" },
    { key: "battery_cycles",  label: "Battery Cycles" },
];
const _PLANNING_KPI_SCHEMA = [
    { key: "cost",       label: "Total Cost" },
    { key: "re_share",   label: "RE Share" },
    { key: "co2",        label: "CO₂ Emissions" },
    { key: "load_shed",  label: "Load Shed" },
    { key: "investment", label: "New Capacity" },
];

function applyKpiSchema(mode) {
    const schema = (mode === "uc") ? _UC_KPI_SCHEMA : _PLANNING_KPI_SCHEMA;
    const cards = document.querySelectorAll(".kpi");
    schema.forEach((spec, i) => {
        const card = cards[i];
        if (!card) return;
        card.dataset.key = spec.key;
        const labelEl = card.querySelector(".label");
        if (labelEl) labelEl.textContent = spec.label;
    });
    // Update the section headers too — the planning labels would be
    // misleading in UC mode.
    const trajHdr = document.querySelector(
        "#trajectory"
    )?.previousElementSibling;
    const mixHdr = document.querySelector("#mix")?.previousElementSibling;
    const costHint = document.getElementById("cost-hint");
    if (mode === "uc") {
        if (trajHdr) trajHdr.innerHTML =
            "Hourly LMP <span class='hint'>System-average locational marginal price + hourly load shedding</span>";
        if (mixHdr) mixHdr.innerHTML =
            "Hourly Dispatch <span class='hint'>Generation by technology, battery flows, curtailment, load shed</span>";
        if (costHint) costHint.textContent =
            "Operational cost breakdown for the UC horizon";
    } else {
        if (trajHdr) trajHdr.innerHTML =
            "System Trajectory <span class='hint'>Brush horizontally to filter • Click a point for year detail</span>";
        if (mixHdr) mixHdr.innerHTML =
            "Generation Mix Evolution <span class='hint'>Click a technology in the legend to toggle</span>";
        if (costHint) costHint.textContent =
            "Breakdown of the total system cost";
    }
}

// ─────────────────────────────────────────────────────────────────
// KPI cards
// ─────────────────────────────────────────────────────────────────

function renderKpis(kpis) {
    for (const card of document.querySelectorAll(".kpi")) {
        const key = card.dataset.key;
        const k = kpis[key];
        const valueEl = card.querySelector(".value");
        const deltaEl = card.querySelector(".delta");
        if (!k || k.value === null || k.value === undefined) {
            valueEl.textContent = "—";
            deltaEl.textContent = "";
            deltaEl.className = "delta";
            continue;
        }
        valueEl.textContent = k.value;
        if (k.delta && k.delta.text) {
            deltaEl.textContent = k.delta.text;
            deltaEl.className = "delta " + (k.delta.direction || "flat");
        } else {
            deltaEl.textContent = "";
            deltaEl.className = "delta";
        }
    }
}

// ─────────────────────────────────────────────────────────────────
// Trajectory chart (multi-axis + brushable)
// ─────────────────────────────────────────────────────────────────

function renderTrajectory(data) {
    const div = document.getElementById("trajectory");
    if (!data || !data.years || data.years.length === 0) {
        div.innerHTML = '<div class="empty">No trajectory data</div>';
        return;
    }

    const traces = [
        {
            x: data.years, y: data.cost_musd,
            type: "scatter", mode: "lines+markers",
            name: "Total Cost (M$)",
            line: { color: "#f4b942", width: 2 },
            yaxis: "y",
        },
        {
            x: data.years, y: data.re_pct,
            type: "scatter", mode: "lines+markers",
            name: "RE Share (%)",
            line: { color: "#2ecc71", width: 2 },
            yaxis: "y2",
        },
        {
            x: data.years, y: data.co2_mt,
            type: "scatter", mode: "lines+markers",
            name: "CO₂ (Mt)",
            line: { color: "#e74c3c", width: 2 },
            yaxis: "y3",
        },
    ];

    const layout = themedLayout({
        margin: { t: 30, r: 60, b: 40, l: 60 },
        xaxis: {
            title: "Year",
            // ``range`` lets us reflect the brushed window into the
            // chart's own visualisation. When state.yearRange is null
            // we let plotly auto-range.
            range: state.yearRange ? [state.yearRange[0], state.yearRange[1]] : undefined,
        },
        yaxis:  { title: "Cost (M$)",  side: "left",  color: "#f4b942" },
        yaxis2: { title: "RE (%)",     side: "right", overlaying: "y", color: "#2ecc71" },
        yaxis3: {
            title: "CO₂ (Mt)",
            side: "right", overlaying: "y", anchor: "free",
            position: 0.96, color: "#e74c3c", showgrid: false,
        },
        legend: { orientation: "h", x: 0, y: 1.1 },
    });

    Plotly.react("trajectory", traces, layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });

    // Wire the brush + click handlers exactly once per element.
    // .on() stacks if called repeatedly, so a flag attribute guards.
    if (!div.dataset.handlersBound) {
        div.on("plotly_relayout", onTrajectoryRelayout);
        div.on("plotly_click", onTrajectoryClick);
        div.dataset.handlersBound = "1";
    }
}

// ─────────────────────────────────────────────────────────────────
// Drill-down: click a year point → detail panel
// ─────────────────────────────────────────────────────────────────

function onTrajectoryClick(evt) {
    // evt.points is an array of points under the cursor (one per
    // trace at that x). They all share the same x = year, so any
    // point gives us the year.
    if (!evt || !evt.points || evt.points.length === 0) return;
    const year = Math.round(Number(evt.points[0].x));
    if (!Number.isFinite(year)) return;
    openYearDetail(year);
}

async function openYearDetail(year) {
    if (!bridge || !state.system) return;
    const panel = document.getElementById("year-detail");
    const label = document.getElementById("year-detail-label");
    label.textContent = String(year);
    panel.style.display = "";
    // Scroll the panel into view so the user sees the drill-down
    // result without hunting for it.
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    setStatus("Loading year " + year + "…");

    try {
        const detail = JSON.parse(await call(
            bridge.get_year_detail, state.system, year
        ));
        renderYearKpis(detail.kpis || {});
        renderYearDispatch(detail.dispatch || null);
        setStatus("");
    } catch (err) {
        console.error("year detail failed", err);
        setStatus("Year detail error: " + (err && err.message || err));
    }
}

function renderYearKpis(kpis) {
    const host = document.getElementById("year-detail-kpis");
    host.innerHTML = "";
    // Same five metrics as the top KPI bar, rendered as compact chips.
    const order = [
        ["cost", "Cost"], ["re_share", "RE"], ["co2", "CO₂"],
        ["load_shed", "Load Shed"], ["investment", "New Cap."],
    ];
    for (const [key, title] of order) {
        const k = kpis[key];
        const chip = document.createElement("div");
        chip.className = "mini-kpi";
        const val = (k && k.value != null) ? k.value : "—";
        chip.innerHTML =
            '<div class="label">' + title + '</div>' +
            '<div class="value">' + val + '</div>';
        host.appendChild(chip);
    }
}

function renderYearDispatch(dispatch) {
    const div = document.getElementById("year-detail-dispatch");
    if (!dispatch || !dispatch.hours || dispatch.hours.length === 0
        || !dispatch.series || dispatch.series.length === 0) {
        div.innerHTML = '<div class="empty">No hourly dispatch for this year</div>';
        return;
    }
    const traces = dispatch.series.map(s => ({
        x: dispatch.hours,
        y: s.values,
        type: "scatter",
        mode: "none",
        stackgroup: "dispatch",
        name: s.label,
        fillcolor: s.color || undefined,
        hovertemplate: "h%{x}: %{y:,.0f} MW<extra>%{fullData.name}</extra>",
    }));
    const layout = themedLayout({
        margin: { t: 20, r: 30, b: 40, l: 70 },
        xaxis: { title: "Hour of year" },
        yaxis: { title: "Dispatch (MW)" },
        legend: { orientation: "v", x: 1.02, y: 1 },
        showlegend: true,
    });
    Plotly.react("year-detail-dispatch", traces, layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

function closeYearDetail() {
    const panel = document.getElementById("year-detail");
    panel.style.display = "none";
    // Purge the embedded plot so a stale figure doesn't flash on the
    // next open before fresh data arrives.
    const div = document.getElementById("year-detail-dispatch");
    if (div) Plotly.purge(div);
}

function onTrajectoryRelayout(evt) {
    // Plotly emits relayout for many things (autorange, dragmode, etc).
    // Only react when the user actually changed the X range — either a
    // manual brush ("xaxis.range[0]" / "xaxis.range[1]") or a reset
    // back to autorange.
    const r0 = evt["xaxis.range[0]"];
    const r1 = evt["xaxis.range[1]"];
    if (r0 === undefined && r1 === undefined) {
        // autorange or unrelated event — only matter when explicitly reset
        if (evt["xaxis.autorange"] === true && state.yearRange !== null) {
            state.yearRange = null;
            updateRangeDisplay();
            refresh();
        }
        return;
    }

    // Clamp the floating-point range plotly gives us (the user can
    // brush mid-year) to integer years and to the available domain.
    const ymin = Math.max(meta.years[0], Math.ceil(Number(r0)));
    const ymax = Math.min(meta.years[meta.years.length - 1], Math.floor(Number(r1)));
    if (ymin >= ymax) return;  // degenerate brush, ignore
    state.yearRange = [ymin, ymax];
    updateRangeDisplay();
    refresh();
}

// ─────────────────────────────────────────────────────────────────
// Generation Mix (stacked area)
// ─────────────────────────────────────────────────────────────────

function renderMix(data) {
    const div = document.getElementById("mix");
    if (!data || !data.years || data.years.length === 0 || !data.series || data.series.length === 0) {
        div.innerHTML = '<div class="empty">No generation data</div>';
        return;
    }

    const traces = data.series.map(s => ({
        x: data.years,
        y: s.values,
        type: "scatter",
        mode: "none",
        stackgroup: "gen",
        name: s.label,
        fillcolor: s.color || undefined,
        hovertemplate: "%{y:,.0f} GWh<extra>%{fullData.name}</extra>",
    }));

    const layout = themedLayout({
        margin: { t: 30, r: 30, b: 40, l: 70 },
        xaxis: { title: "Year" },
        yaxis: { title: "Generation (GWh)" },
        legend: { orientation: "v", x: 1.02, y: 1 },
        showlegend: true,
    });

    Plotly.react("mix", traces, layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

// ─────────────────────────────────────────────────────────────────
// Cost Composition (waterfall)
// ─────────────────────────────────────────────────────────────────

function renderCost(data) {
    const div = document.getElementById("cost");
    const hint = document.getElementById("cost-hint");
    if (!data || !data.steps || data.steps.length === 0) {
        div.innerHTML = '<div class="empty">No cost breakdown for this run</div>';
        if (hint) hint.textContent = "Breakdown of the total system cost";
        return;
    }
    if (hint) {
        const ys = data.years || [];
        const span = ys.length === 0 ? ""
            : (ys.length === 1 ? "Year " + ys[0]
               : "Years " + ys[0] + "–" + ys[ys.length - 1]);
        hint.textContent = (span ? span + " — " : "") +
            "components stack up to the total";
    }

    // Build the waterfall: one "relative" step per cost component,
    // then one "total" bar that plotly draws as an absolute sum.
    // The solver stores benefit-type components (e.g. flexible-demand
    // benefit, V2G compensation) as positive numbers that *reduce*
    // the objective — we keep the raw sign here because the backend
    // already returns them with the correct sign for stacking.
    const labels   = data.steps.map(s => s.label);
    const values   = data.steps.map(s => s.value);
    const measures = data.steps.map(() => "relative");

    labels.push("Total");
    values.push(data.total);
    measures.push("total");

    const trace = {
        type: "waterfall",
        orientation: "v",
        measure: measures,
        x: labels,
        y: values,
        // Compact $ text on each bar.
        text: values.map(fmtUsdShort),
        textposition: "outside",
        connector: { line: { color: (theme && theme.text_muted) || "#888" } },
        increasing: { marker: { color: (theme && theme.err)   || "#e74c3c" } },
        decreasing: { marker: { color: (theme && theme.ok)    || "#2ecc71" } },
        totals:     { marker: { color: (theme && theme.accent)|| "#2980b9" } },
        hovertemplate: "%{x}: %{y:$,.0f}<extra></extra>",
    };

    const layout = themedLayout({
        margin: { t: 20, r: 20, b: 110, l: 80 },
        xaxis: { title: "", tickangle: -40, automargin: true },
        yaxis: { title: "Cost (USD)" },
        showlegend: false,
    });

    Plotly.react("cost", [trace], layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

// Short USD formatter shared by the waterfall bar labels.
function fmtUsdShort(v) {
    const a = Math.abs(v);
    if (a >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
    if (a >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (a >= 1e3) return "$" + (v / 1e3).toFixed(1) + "K";
    return "$" + v.toFixed(0);
}

// ─────────────────────────────────────────────────────────────────
// Utilities
// ─────────────────────────────────────────────────────────────────

function applyTheme(t) {
    // Map the colour dict onto the CSS custom properties the
    // stylesheet expects. Setting on documentElement means the
    // variables propagate to every element via `var(--theme-*)`.
    const root = document.documentElement.style;
    root.setProperty("--theme-bg",         t.bg);
    root.setProperty("--theme-bg-2",       t.bg_secondary);
    root.setProperty("--theme-bg-el",      t.bg_elevated);
    root.setProperty("--theme-text",       t.text);
    root.setProperty("--theme-text-muted", t.text_muted);
    root.setProperty("--theme-border",     t.border);
    root.setProperty("--theme-accent",     t.accent);
    root.setProperty("--theme-accent2",    t.accent2);
    root.setProperty("--theme-ok",         t.ok);
    root.setProperty("--theme-warn",       t.warn);
    root.setProperty("--theme-err",        t.err);
    root.setProperty("--theme-selection",  t.selection);
}

function themedLayout(overrides) {
    // Plotly doesn't read CSS vars, so we pass the theme colours in
    // directly. Falls back to the hardcoded light defaults if the
    // bridge handshake hasn't filled `theme` yet.
    const t = theme || {
        bg: "#FFFFFF", bg_elevated: "#FFFFFF",
        text: "#2C3E50", text_muted: "#7F8C8D",
        border: "#DEE2E6",
    };
    return Object.assign({
        paper_bgcolor: t.bg_elevated,
        plot_bgcolor:  t.bg_elevated,
        font: { color: t.text, size: 11 },
        xaxis: { gridcolor: t.border, zerolinecolor: t.border, color: t.text_muted },
        yaxis: { gridcolor: t.border, zerolinecolor: t.border, color: t.text_muted },
        autosize: true,
    }, overrides);
}

function setStatus(text) {
    const el = document.getElementById("status");
    if (el) el.textContent = text;
}

function updateRangeDisplay() {
    const el = document.getElementById("year-range");
    if (!el) return;
    if (state.yearRange) {
        el.textContent = `${state.yearRange[0]} – ${state.yearRange[1]}`;
    } else if (meta && meta.years && meta.years.length) {
        const first = meta.years[0], last = meta.years[meta.years.length - 1];
        el.textContent = `${first} – ${last} (all)`;
    } else {
        el.textContent = "—";
    }
}

// Promise-ify a QWebChannel slot call. The QWebChannel API passes
// results to a callback (variadic args); we wrap it in a Promise so
// the rest of the file reads like normal async code.
function call(fn, ...args) {
    return new Promise((resolve, reject) => {
        try {
            fn(...args, result => resolve(result));
        } catch (err) {
            reject(err);
        }
    });
}

// ─────────────────────────────────────────────────────────────────
// UC renderers — replace the planning trajectory + mix with hourly
// operational views when the run is unit_commitment. The DOM divs
// (#trajectory, #mix) are reused so a system switch between UC and
// planning swaps content cleanly.
// ─────────────────────────────────────────────────────────────────

function renderHourlyLmp(data) {
    const div = document.getElementById("trajectory");
    try { Plotly.purge("trajectory"); } catch (e) {}
    if (!data || !data.hours || data.hours.length === 0) {
        div.innerHTML = '<div class="empty">No LMP data</div>';
        return;
    }
    const x = data.hours;
    const traces = [
        {
            x: x, y: data.lmp, type: "scatter", mode: "lines",
            name: "LMP",
            line: { color: theme && theme.accent ? theme.accent : "#2C3E50",
                    width: 2 },
            xaxis: "x", yaxis: "y",
            hovertemplate: "Hour %{x}: %{y:,.2f} USD/MWh<extra></extra>",
        },
        {
            x: x, y: data.load_shed_mw, type: "bar",
            name: "Load shed (MW)",
            marker: { color: "rgba(231, 76, 60, 0.80)" },
            xaxis: "x", yaxis: "y2",
            hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Load shed</extra>",
        },
    ];
    const layout = {
        margin: { t: 30, r: 60, b: 50, l: 70 },
        showlegend: true,
        legend: { orientation: "h", x: 0.5, xanchor: "center",
                  y: 1.10, yanchor: "bottom", font: { size: 10 } },
        xaxis: { title: "<b>Hour</b>" },
        yaxis: { title: "<b>LMP (USD/MWh)</b>", rangemode: "tozero" },
        yaxis2: { title: "<b>Load shed (MW)</b>",
                  overlaying: "y", side: "right",
                  rangemode: "tozero", showgrid: false },
    };
    Plotly.newPlot("trajectory", traces, layout, {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

function renderHourlyDispatch(data) {
    const div = document.getElementById("mix");
    try { Plotly.purge("mix"); } catch (e) {}
    if (!data || !data.hours || data.hours.length === 0) {
        div.innerHTML = '<div class="empty">No dispatch data</div>';
        return;
    }
    const x = data.hours;
    const traces = [];

    // Positive stack: generation by tech (+ battery discharge +
    // load shed).
    for (const s of (data.series_pos || [])) {
        if (!s.values || !s.values.length) continue;
        traces.push({
            x: x, y: s.values, type: "scatter", mode: "none",
            stackgroup: "pos", name: s.label,
            fillcolor: s.color,
            hovertemplate:
                "Hour %{x}: %{y:,.1f} MW<extra>" + s.label + "</extra>",
        });
    }
    // Negative stack: battery charge + curtailment (drawn below 0).
    for (const s of (data.series_neg || [])) {
        if (!s.values || !s.values.length) continue;
        const neg = s.values.map(v => -Math.abs(v || 0));
        traces.push({
            x: x, y: neg, type: "scatter", mode: "none",
            stackgroup: "neg", name: s.label,
            fillcolor: s.color,
            customdata: s.values,
            hovertemplate:
                "Hour %{x}: %{customdata:,.1f} MW<extra>" + s.label + "</extra>",
        });
    }
    // Demand line on top.
    if (data.demand_mw && data.demand_mw.length) {
        traces.push({
            x: x, y: data.demand_mw, type: "scatter", mode: "lines",
            name: "Demand", line: { color: "#000000", width: 2 },
            hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Demand</extra>",
        });
    }
    const layout = {
        margin: { t: 30, r: 30, b: 50, l: 70 },
        showlegend: true,
        legend: { orientation: "h", x: 0.5, xanchor: "center",
                  y: 1.10, yanchor: "bottom", font: { size: 10 } },
        shapes: [
            { type: "line", xref: "x domain", yref: "y",
              x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "#000000", width: 1 } },
        ],
        xaxis: { title: "<b>Hour</b>" },
        yaxis: { title: "<b>Power (MW)</b>" },
    };
    Plotly.newPlot("mix", traces, layout, {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}
