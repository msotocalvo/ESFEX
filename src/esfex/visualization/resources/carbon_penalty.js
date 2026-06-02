/* Carbon & System Penalty Costs — Plotly interactive chart.
 *
 * (a) CO₂ emissions per year (bar, Mt) + CO₂ emission intensity
 *     (g CO₂ / kWh) on the secondary y axis — the line shows how
 *     quickly the system is decarbonising, independent of how demand
 *     itself grows.
 * (b) Reliability / reserve penalty quantities per year (stacked bars,
 *     MWh): loss-of-load + dynamic & static reserve violations. A run
 *     with no penalties shows empty bars — confirming the operational
 *     feasibility from a single glance.
 */
"use strict";

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
        catch (e) { showStatus("Bad payload: " + e.message, true); return; }
        if (!data || data.error) {
            showStatus(data && data.error ? data.error : "No data", false);
            return;
        }
        if (!data.years || data.years.length === 0) {
            showStatus("No data", false);
            return;
        }
        try {
            renderCarbon(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[carbon_penalty.js] render threw:", e);
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

function renderCarbon(data) {
    const traces = [];
    const years = data.years;

    // ── Subplot (a): CO2 emissions bars + intensity line ──
    if (data.co2_mt && data.co2_mt.length) {
        traces.push({
            type: "bar",
            x: years, y: data.co2_mt,
            name: "CO₂ Emissions",
            marker: { color: "#7F8C8D",
                      line: { color: "#FFFFFF", width: 0.5 } },
            xaxis: "x", yaxis: "y",
            hovertemplate: "%{x}: %{y:,.3f} Mt<extra>CO₂</extra>",
        });
    }
    if (data.co2_intensity_g_per_kwh && data.co2_intensity_g_per_kwh.length) {
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: years, y: data.co2_intensity_g_per_kwh,
            name: "CO₂ Intensity",
            line: { color: "#E67E22", width: 2.5 },
            marker: { color: "#E67E22", size: 7,
                      line: { color: "#FFFFFF", width: 1 } },
            xaxis: "x", yaxis: "y2",
            hovertemplate: "%{x}: %{y:,.1f} g/kWh<extra>Intensity</extra>",
        });
    }

    // ── Subplot (b): penalty quantities (stacked bars) ──
    const series = [
        { key: "loss_load_mwh", label: "Loss of Load",
          color: "#E74C3C" },
        { key: "reserve_dynamic_violation_mwh", label: "Reserve viol. (dynamic)",
          color: "#9B59B6" },
        { key: "reserve_static_violation_mwh", label: "Reserve viol. (static)",
          color: "#3498DB" },
    ];
    let anyPenalty = false;
    for (const s of series) {
        const vals = data[s.key];
        if (!vals || !vals.length) continue;
        if (vals.some(v => v > 0)) anyPenalty = true;
        traces.push({
            type: "bar",
            x: years, y: vals,
            name: s.label,
            marker: { color: s.color,
                      line: { color: "#FFFFFF", width: 0.5 } },
            xaxis: "x2", yaxis: "y3",
            offsetgroup: "pen",
            hovertemplate: "%{x}: %{y:,.2f} MWh<extra>" + s.label + "</extra>",
        });
    }

    const layout = {
        margin: { t: 70, r: 8, b: 70, l: 80 },
        showlegend: true,
        barmode: "relative",
        annotations: [
            { text: "<b>a) CO₂ Emissions & Intensity</b>",
              x: 0.435, xref: "paper", xanchor: "center",
              y: 1.00, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) Reliability & Reserve Penalties</b>" +
                    (anyPenalty ? "" : "  <i>(none — operationally feasible)</i>"),
              x: 0.435, xref: "paper", xanchor: "center",
              y: 0.46, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ],
        legend: {
            orientation: "v",
            x: 0.88, xanchor: "left",
            y: 0.5, yanchor: "middle",
            font: { size: 10 },
            tracegroupgap: 6,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        xaxis: {
            domain: [0, 0.87], anchor: "y",
            type: "category",
            showticklabels: false,
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis: {
            domain: [0.54, 0.98], anchor: "x",
            title: "<b>Emissions (Mt CO₂)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
            rangemode: "tozero",
        },
        yaxis2: {
            domain: [0.54, 0.98], anchor: "x",
            overlaying: "y", side: "right",
            title: { text: "<b>Intensity (g CO₂/kWh)</b>",
                     font: { color: "#E67E22" } },
            tickfont: { color: "#E67E22" },
            showgrid: false,
            rangemode: "tozero",
        },
        xaxis2: {
            domain: [0, 0.87], anchor: "y3",
            type: "category",
            tickangle: -45,
            title: "<b>Year</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis3: {
            domain: [0.0, 0.44], anchor: "x2",
            title: "<b>Penalty quantity (MWh)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
            rangemode: "tozero",
        },
    };

    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
