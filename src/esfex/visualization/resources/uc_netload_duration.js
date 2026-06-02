/* Net Load Duration Curve (UC) — the classic capacity-sizing diagnostic.
 *
 * Net load = demand − renewable generation. Sorted descending gives a
 * "duration curve": at the left, the few hours where everything must
 * fire; at the right, hours with surplus RE (net load may be negative).
 *
 * Side-by-side: gross demand duration curve vs. net load. The shrink
 * from one to the other is the contribution of RE to peak shaving.
 *
 * Payload:
 *   { year, pct_hours: [0..100],
 *     demand_sorted: [...], net_load_sorted: [...],
 *     peak_demand_mw, peak_netload_mw,
 *     min_netload_mw, hours_negative_netload }
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
        if (!data.demand_sorted || !data.demand_sorted.length) { _ucClearPlot(); showStatus("No demand", false); return; }
        try { renderDuration(data); hideStatus(); }
        catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
    });
}
function showStatus(t, e) { const s = document.getElementById("status"); s.textContent = t; s.style.display = "block"; s.style.color = e ? "#c0392b" : "#7F8C8D"; }
function hideStatus() { document.getElementById("status").style.display = "none"; }

function renderDuration(data) {
    const x = data.pct_hours;
    const traces = [];
    traces.push({
        x: x, y: data.demand_sorted,
        type: "scatter", mode: "lines",
        name: "Gross demand",
        line: { color: "#7F8C8D", width: 2, dash: "dash" },
        hovertemplate: "Top %{x:.1f}% of hours: %{y:,.0f} MW<extra>Gross</extra>",
    });
    traces.push({
        x: x, y: data.net_load_sorted,
        type: "scatter", mode: "lines",
        name: "Net load (demand − RE)",
        line: { color: "#2C3E50", width: 2 },
        fill: "tozeroy", fillcolor: "rgba(44, 62, 80, 0.10)",
        hovertemplate: "Top %{x:.1f}% of hours: %{y:,.0f} MW<extra>Net</extra>",
    });

    const negFrac = data.hours_negative_netload > 0
        ? `${(100 * data.hours_negative_netload / x.length).toFixed(1)}% of hours have surplus RE (net load &lt; 0)`
        : "Net load stays positive — system never has RE surplus";
    const subTitle =
        `Peak gross ${data.peak_demand_mw.toFixed(0)} MW &nbsp;|&nbsp; ` +
        `Peak net ${data.peak_netload_mw.toFixed(0)} MW &nbsp;|&nbsp; ` +
        `Min net ${data.min_netload_mw.toFixed(0)} MW &nbsp;|&nbsp; ` + negFrac;

    const layout = {
        margin: { t: 110, r: 30, b: 60, l: 70 },
        showlegend: true,
        annotations: [
            { text: `<b>Net Load Duration Curve — Year ${data.year}</b>`,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.16, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: subTitle,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.08, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11, color: "#5D6D7E" } },
        ],
        legend: {
            orientation: "h", x: 0.5, xanchor: "center",
            y: 1.02, yanchor: "bottom", font: { size: 10 },
            bgcolor: "rgba(255,255,255,0.85)", bordercolor: "rgba(0,0,0,0.1)", borderwidth: 1,
        },
        shapes: [
            { type: "line", xref: "x domain", yref: "y",
              x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "#000000", width: 1 } },
        ],
        xaxis: { title: "<b>Top X% of hours</b>", range: [0, 100] },
        yaxis: { title: "<b>Power (MW)</b>" },
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
