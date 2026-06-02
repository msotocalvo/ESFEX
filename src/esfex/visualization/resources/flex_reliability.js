/* Flexibility & Reliability — interactive Plotly, two subplots.
 *
 * (a) Net-load ramp-duration curves (MW/h, sorted descending), one
 *     line per year.
 * (b) Failure mode by year: stacked unserved-energy + dynamic/static
 *     reserve shortfalls (MWh, left axis) and a line for hours under an
 *     inertia deficit (right axis).
 *
 * Payload contract (FlexReliabilityChart._build_payload):
 *   { years, x_pct, ramp_curves_mw_h[year][L], ens_year_mwh,
 *     reserve_dynamic_mwh, reserve_static_mwh, inertia_deficit_hours,
 *     any_event }
 */
"use strict";


function _ucClearPlot() {
    try { Plotly.purge("plot"); } catch (e) {}
}
let bridge = null;

document.addEventListener("DOMContentLoaded", () => {
    new QWebChannel(qt.webChannelTransport, channel => {
        bridge = channel.objects.loader;
        refresh();
    });
});

function refresh() {
    if (!bridge) return;
    bridge.get_data(rawJson => {
        let data = null;
        try { data = JSON.parse(rawJson); }
        catch (e) { _ucClearPlot(); showStatus("Bad payload: " + e.message, true); return; }
        if (!data || data.error) {
            _ucClearPlot(); showStatus(data && data.error ? data.error : "No data", false);
            return;
        }
        if (data.mode === "uc") {
            try { renderUC(data); hideStatus(); }
            catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
            return;
        }
        if (!data.years || data.years.length === 0) {
            _ucClearPlot(); showStatus("No data", false);
            return;
        }
        try {
            render(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[flex_reliability.js]", e);
        }
    });
}

function showStatus(text, isError) {
    const s = document.getElementById("status");
    if (!s) return;
    s.textContent = text;
    s.style.display = "block";
    s.style.color = isError ? "#c0392b" : "#7F8C8D";
}
function hideStatus() {
    const s = document.getElementById("status");
    if (s) s.style.display = "none";
}

// High-contrast categorical palette (matplotlib tab20, reordered so the
// 10 saturated members come first — adjacent years stay easy to tell
// apart instead of the dark/light pairing tab20 uses by default).
const TAB20 = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
    "#c49c94", "#f7b6d2", "#c7c7c7", "#dbdb8d", "#9edae5",
];
function yearColor(i, _n) {
    return TAB20[i % TAB20.length];
}

function render(data) {
    const years = data.years;
    const n = years.length;
    const traces = [];

    // ── (a) ramp-duration curves ──
    (data.ramp_curves_mw_h || []).forEach((curve, i) => {
        if (!curve || !curve.length) return;
        const x = data.x_pct.slice(0, curve.length);
        traces.push({
            type: "scatter", mode: "lines",
            x: x, y: curve, name: years[i], legendgroup: years[i],
            line: { color: yearColor(i, n), width: 1.8 },
            xaxis: "x", yaxis: "y",
            hovertemplate: years[i] +
                "<br>%{x:.1f}% of time<br>Ramp: %{y:,.1f} MW/h<extra></extra>",
        });
    });

    // ── (b) failure-mode bars + inertia-deficit line ──
    const modes = [
        { key: "ens_year_mwh", label: "Unserved energy", color: "#E74C3C" },
        { key: "reserve_dynamic_mwh", label: "Reserve shortfall (dyn)", color: "#9B59B6" },
        { key: "reserve_static_mwh", label: "Reserve shortfall (sta)", color: "#3498DB" },
    ];
    for (const m of modes) {
        const v = data[m.key];
        if (!v || !v.length) continue;
        traces.push({
            type: "bar", x: years, y: v, name: m.label,
            marker: { color: m.color, line: { color: "#FFFFFF", width: 0.5 } },
            xaxis: "x2", yaxis: "y2", offsetgroup: "fm", legendgroup: "fm",
            hovertemplate: "%{x}: %{y:,.2f} MWh<extra>" + m.label + "</extra>",
        });
    }
    if (data.inertia_deficit_hours && data.inertia_deficit_hours.length) {
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: years, y: data.inertia_deficit_hours, name: "Inertia-deficit hours",
            line: { color: "#E67E22", width: 2.5 },
            marker: { color: "#E67E22", size: 7, line: { color: "#FFF", width: 1 } },
            xaxis: "x2", yaxis: "y3",
            hovertemplate: "%{x}: %{y:,.0f} h<extra>Inertia deficit</extra>",
        });
    }

    const feasibleNote = data.any_event ? "" :
        "  <i>(no violations — adequate)</i>";

    const layout = {
        margin: { t: 56, r: 116, b: 64, l: 76 },
        showlegend: true,
        barmode: "relative",
        // Single shared legend, vertical, hugging the right edge of the
        // figure (small gap ≈ 20% of the previous offset).
        legend: { orientation: "v", x: 1.004, xanchor: "left",
                  y: 0.5, yanchor: "middle", font: { size: 10 },
                  tracegroupgap: 8,
                  bgcolor: "rgba(255,255,255,0.85)",
                  bordercolor: "rgba(0,0,0,0.1)", borderwidth: 1 },
        annotations: [
            { text: "<b>a) Net-Load Ramp Duration Curve (MW/h)</b>",
              x: 0.21, xref: "paper", xanchor: "center",
              y: 1.02, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) Failure Mode by Year</b>" + feasibleNote,
              x: 0.78, xref: "paper", xanchor: "center",
              y: 1.02, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ],
        // (a) left
        xaxis: { domain: [0, 0.42], anchor: "y",
                 title: "<b>% of time ramp exceeded</b>",
                 range: [0, 100], gridcolor: "rgba(0,0,0,0.08)" },
        yaxis: { domain: [0, 1], anchor: "x",
                 title: "<b>Net-load ramp (MW/h)</b>",
                 zeroline: true, zerolinecolor: "rgba(0,0,0,0.25)",
                 gridcolor: "rgba(0,0,0,0.08)" },
        // (b) right
        xaxis2: { domain: [0.58, 0.96], anchor: "y2", type: "category",
                  tickangle: -45, title: "<b>Year</b>",
                  gridcolor: "rgba(0,0,0,0.08)" },
        yaxis2: { domain: [0, 1], anchor: "x2",
                  title: "<b>Shortfall (MWh)</b>", rangemode: "tozero",
                  gridcolor: "rgba(0,0,0,0.08)" },
        yaxis3: { domain: [0, 1], anchor: "x2", overlaying: "y2",
                  side: "right", rangemode: "tozero", showgrid: false,
                  title: { text: "<b>Inertia-deficit (h)</b>",
                           font: { color: "#E67E22" } },
                  tickfont: { color: "#E67E22" } },
    };

    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout, {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

function _fmtMWh(v) {
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(2) + " TWh";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + " GWh";
    return v.toFixed(0) + " MWh";
}

function renderUC(data) {
    // UC layout: three stacked subplots sharing nothing — they each
    // answer a separate question about the operational window.
    //   (a) Net-load + hourly ramp (dual axis).
    //   (b) Ramp duration curve (sorted desc) — captures the upper-tail
    //       flexibility requirement at a glance.
    //   (c) Hourly stress events (load shed + reserve shortfalls)
    //       stacked, with inertia-deficit markers.
    const x = data.hours;
    const t = data.totals_mwh || {};
    const traces = [];

    traces.push({
        x: x, y: data.net_load_mw,
        type: "scatter", mode: "lines",
        name: "Net load",
        line: { color: "#2C3E50", width: 2 },
        xaxis: "x", yaxis: "y",
        hovertemplate: "Hour %{x}: %{y:,.0f} MW<extra>Net load</extra>",
    });
    traces.push({
        x: x, y: data.ramp_mw_h,
        type: "scatter", mode: "lines",
        name: "Ramp (ΔP/Δt)",
        line: { color: "#922B21", width: 1.5, dash: "dot" },
        xaxis: "x", yaxis: "y2",
        hovertemplate: "Hour %{x}: %{y:,.1f} MW/h<extra>Ramp</extra>",
    });

    traces.push({
        x: data.x_pct, y: data.ramp_sorted_mw_h,
        type: "scatter", mode: "lines",
        name: "Ramp duration",
        line: { color: "#27AE60", width: 2 },
        fill: "tozeroy", fillcolor: "rgba(39, 174, 96, 0.10)",
        xaxis: "x2", yaxis: "y3",
        hovertemplate: "Top %{x:.1f}%: %{y:,.1f} MW/h<extra></extra>",
        showlegend: false,
    });

    traces.push({
        x: x, y: data.loss_load_mw,
        type: "bar", name: "Load shed",
        marker: { color: "#E74C3C", opacity: 0.85 },
        xaxis: "x3", yaxis: "y4",
        hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Load shed</extra>",
    });
    traces.push({
        x: x, y: data.loss_reserve_static_mw,
        type: "bar", name: "Reserve (static) shortfall",
        marker: { color: "#3498DB", opacity: 0.85 },
        xaxis: "x3", yaxis: "y4",
        hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Static</extra>",
    });
    traces.push({
        x: x, y: data.loss_reserve_dynamic_mw,
        type: "bar", name: "Reserve (dynamic) shortfall",
        marker: { color: "#9B59B6", opacity: 0.85 },
        xaxis: "x3", yaxis: "y4",
        hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Dynamic</extra>",
    });

    const subTitle = data.any_event
        ? `<span style="color:#922B21"><b>Stress events:</b> ` +
          `LS ${_fmtMWh(t.loss_load || 0)} &nbsp; ` +
          `RS-stat ${_fmtMWh(t.loss_reserve_static || 0)} &nbsp; ` +
          `RS-dyn ${_fmtMWh(t.loss_reserve_dynamic || 0)} &nbsp; ` +
          `Inertia ${(t.loss_inertia_hours || 0).toFixed(0)} h</span>`
        : `<span style="color:#1E8449"><b>Adequate</b> — no operational stress events</span>`;

    const layout = {
        margin: { t: 110, r: 70, b: 60, l: 80 },
        grid: { rows: 3, columns: 1, pattern: "independent", roworder: "top to bottom" },
        showlegend: true,
        annotations: [
            { text: `<b>Flexibility &amp; Reliability — Year ${data.year}</b>`,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.16, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: subTitle,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.08, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11 } },
        ],
        legend: {
            orientation: "h", x: 0.5, xanchor: "center",
            y: 1.02, yanchor: "bottom", font: { size: 10 },
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)", borderwidth: 1,
        },
        // Subplot (a): net load + ramp
        xaxis:  { domain: [0, 1], anchor: "y", showticklabels: false },
        yaxis:  { domain: [0.68, 1.0], title: "<b>Net load (MW)</b>",
                  rangemode: "tozero" },
        yaxis2: { domain: [0.68, 1.0], overlaying: "y", side: "right",
                  title: "<b>Ramp (MW/h)</b>", showgrid: false },
        // Subplot (b): ramp duration curve
        xaxis2: { domain: [0, 1], anchor: "y3",
                  title: "<b>Top X% of hours</b>", range: [0, 100] },
        yaxis3: { domain: [0.36, 0.62], title: "<b>Ramp (MW/h)</b>" },
        // Subplot (c): stress events
        xaxis3: { domain: [0, 1], anchor: "y4", title: "<b>Hour</b>" },
        yaxis4: { domain: [0.0, 0.30], title: "<b>Shortfall (MW)</b>",
                  rangemode: "tozero" },
        barmode: "stack",
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout, {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
