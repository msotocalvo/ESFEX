/* Ramp Distribution (UC) — how aggressively each technology ramps.
 *
 * Box plot per tech of the hour-to-hour ΔP distribution (absolute
 * value, in MW). Wide boxes / tall whiskers flag techs that the run
 * cycles aggressively; narrow boxes suggest the tech runs near
 * baseload. The P95 marker per tech lets you compare ramp severity
 * across techs without being dominated by a few outlier hours.
 *
 * Payload:
 *   { year, techs: ["Wind", "Solar", "Diesel", ...],
 *     ramps: { "Wind": [|ΔP|, ...], ... },     # MW values per hour-pair
 *     p95: { "Wind": float, ... },
 *     tech_colors: { "Wind": "#...", ... } }
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
        if (!data.techs || !data.techs.length) { _ucClearPlot(); showStatus("No ramp data", false); return; }
        try { renderRamps(data); hideStatus(); }
        catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
    });
}
function showStatus(t, e) { const s = document.getElementById("status"); s.textContent = t; s.style.display = "block"; s.style.color = e ? "#c0392b" : "#7F8C8D"; }
function hideStatus() { document.getElementById("status").style.display = "none"; }

function renderRamps(data) {
    const traces = [];
    for (const tech of data.techs) {
        const ramps = data.ramps[tech];
        if (!ramps || !ramps.length) continue;
        traces.push({
            y: ramps, x: ramps.map(() => tech),
            type: "box", name: tech,
            marker: { color: (data.tech_colors || {})[tech] || "#95A5A6" },
            boxpoints: "outliers",
            jitter: 0.3, pointpos: 0,
            hovertemplate: "%{y:,.1f} MW<extra>" + tech + "</extra>",
        });
    }

    // P95 markers overlaid as a scatter trace — quick visual ranking.
    const p95Techs = data.techs.filter(t => data.p95[t] != null);
    if (p95Techs.length) {
        traces.push({
            x: p95Techs,
            y: p95Techs.map(t => data.p95[t]),
            type: "scatter", mode: "markers",
            name: "P95",
            marker: { symbol: "diamond", size: 12, color: "#922B21",
                      line: { color: "#000", width: 1 } },
            hovertemplate: "%{x} P95: %{y:,.1f} MW<extra></extra>",
        });
    }

    const layout = {
        margin: { t: 100, r: 30, b: 70, l: 70 },
        showlegend: true,
        annotations: [
            { text: `<b>Ramp Distribution by Technology — Year ${data.year}</b>`,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.10, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: "Hour-to-hour |ΔP|, MW &nbsp;|&nbsp; red diamond = P95",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.04, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11, color: "#5D6D7E" } },
        ],
        legend: { font: { size: 10 } },
        xaxis: { title: "<b>Technology</b>", tickangle: -25 },
        yaxis: { title: "<b>|ΔP| per hour (MW)</b>", rangemode: "tozero" },
        boxmode: "group",
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
