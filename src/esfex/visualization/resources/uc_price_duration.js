/* Price Duration Curve (UC) — LMPs sorted descending vs. cumulative
 * hours. The classic market-design diagnostic: it reveals the price
 * regime mix (baseload band, mid-merit, peaker hours, scarcity tail)
 * at a glance.
 *
 * Three zones shaded as background bands:
 *   • Peak    — top 10% of hours
 *   • Mid     — 10–60% of hours
 *   • Baseload— bottom 40% of hours
 *
 * Payload:
 *   { year, sorted_prices: [...],
 *     pct_hours: [0..100],  # 0% to 100% in 1pp steps
 *     scarcity_threshold: float, scarcity_hours: int,
 *     mean: float, median: float, p95: float, voll_estimate: float }
 */
"use strict";


function _ucClearPlot() {
    // Wipe the previous render so a stale chart from another
    // system selection does not leak through when the current
    // payload has no data.
    try { Plotly.purge("plot"); } catch (e) {}
}
let bridge = null;
document.addEventListener("DOMContentLoaded", () => {
    new QWebChannel(qt.webChannelTransport, channel => {
        bridge = channel.objects.loader; refresh();
    });
});
function refresh() {
    if (!bridge) return;
    bridge.get_data(rawJson => {
        let data = null;
        try { data = JSON.parse(rawJson); }
        catch (e) { _ucClearPlot(); showStatus("Bad payload: " + e.message, true); return; }
        if (!data || data.error) { _ucClearPlot(); showStatus(data && data.error ? data.error : "No data", false); return; }
        if (!data.sorted_prices || !data.sorted_prices.length) { _ucClearPlot(); showStatus("No prices", false); return; }
        try { renderDuration(data); hideStatus(); }
        catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
    });
}
function showStatus(t, e) { const s = document.getElementById("status"); s.textContent = t; s.style.display = "block"; s.style.color = e ? "#c0392b" : "#7F8C8D"; }
function hideStatus() { document.getElementById("status").style.display = "none"; }

function renderDuration(data) {
    const x = data.pct_hours;
    const y = data.sorted_prices;
    const trace = {
        x: x, y: y,
        type: "scatter", mode: "lines",
        name: "LMP", line: { color: "#2C3E50", width: 2 },
        fill: "tozeroy", fillcolor: "rgba(44, 62, 80, 0.08)",
        hovertemplate: "Top %{x:.1f}% of hours: %{y:,.1f} USD/MWh<extra></extra>",
    };

    const yMax = Math.max(...y) * 1.05;
    const yMin = Math.min(0, Math.min(...y));
    const shapes = [
        // Peak zone (top 10%)
        { type: "rect", xref: "x", yref: "y",
          x0: 0, x1: 10, y0: yMin, y1: yMax,
          fillcolor: "rgba(231, 76, 60, 0.08)", line: { width: 0 }, layer: "below" },
        // Mid-merit zone (10-60%)
        { type: "rect", xref: "x", yref: "y",
          x0: 10, x1: 60, y0: yMin, y1: yMax,
          fillcolor: "rgba(243, 156, 18, 0.06)", line: { width: 0 }, layer: "below" },
        // Baseload zone (60-100%)
        { type: "rect", xref: "x", yref: "y",
          x0: 60, x1: 100, y0: yMin, y1: yMax,
          fillcolor: "rgba(39, 174, 96, 0.07)", line: { width: 0 }, layer: "below" },
    ];

    const subTitle =
        `Mean ${data.mean.toFixed(1)} &nbsp;|&nbsp; ` +
        `Median ${data.median.toFixed(1)} &nbsp;|&nbsp; ` +
        `P95 ${data.p95.toFixed(1)} USD/MWh &nbsp;|&nbsp; ` +
        `${data.scarcity_hours} h above ${data.scarcity_threshold.toFixed(0)} (scarcity)`;

    const layout = {
        margin: { t: 110, r: 30, b: 60, l: 70 },
        showlegend: false,
        annotations: [
            { text: `<b>Price Duration Curve — Year ${data.year}</b>`,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.16, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: subTitle,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.08, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11, color: "#5D6D7E" } },
            { text: "<b>Peak</b>", x: 5, xref: "x", y: yMax * 0.95, yref: "y",
              showarrow: false, font: { size: 10, color: "#922B21" } },
            { text: "<b>Mid-merit</b>", x: 35, xref: "x", y: yMax * 0.95, yref: "y",
              showarrow: false, font: { size: 10, color: "#9A6700" } },
            { text: "<b>Baseload</b>", x: 80, xref: "x", y: yMax * 0.95, yref: "y",
              showarrow: false, font: { size: 10, color: "#1E8449" } },
        ],
        shapes: shapes,
        xaxis: { title: "<b>Top X% of hours</b>", range: [0, 100] },
        yaxis: { title: "<b>LMP (USD/MWh)</b>", rangemode: "tozero" },
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", [trace], layout, {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
