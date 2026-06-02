/* Hourly Price Profile (UC) — system-average LMP per hour, with the
 * full per-node spread shown as a band underneath. Payload:
 *
 *   { year, hours: [0..N-1], system_avg: [...], node_min: [...], node_max: [...] }
 *
 * The band (min..max across nodes) highlights congestion hours: a wide
 * band means LMPs diverge between nodes (transmission limit binding);
 * a narrow band means the system price is uniform.
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
        if (!data.hours || data.hours.length === 0) {
            _ucClearPlot(); showStatus("No price data", false);
            return;
        }
        try { renderProfile(data); hideStatus(); }
        catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
    });
}

function showStatus(text, isError) {
    const s = document.getElementById("status");
    s.textContent = text;
    s.style.display = "block";
    s.style.color = isError ? "#c0392b" : "#7F8C8D";
}
function hideStatus() {
    document.getElementById("status").style.display = "none";
}

function renderProfile(data) {
    const x = data.hours;
    const traces = [];

    // Per-node band (max — lower bound first, then upper bound with
    // fill="tonexty" to draw the area between the two lines).
    if (data.node_min && data.node_max
        && data.node_min.length === x.length
        && data.node_max.length === x.length) {
        traces.push({
            x: x, y: data.node_min,
            type: "scatter", mode: "lines", name: "Nodal min",
            line: { color: "rgba(52, 152, 219, 0)", width: 0 },
            showlegend: false,
            hoverinfo: "skip",
        });
        traces.push({
            x: x, y: data.node_max,
            type: "scatter", mode: "lines", name: "Nodal range",
            line: { color: "rgba(52, 152, 219, 0)", width: 0 },
            fill: "tonexty",
            fillcolor: "rgba(52, 152, 219, 0.18)",
            hovertemplate: "Hour %{x}: max %{y:,.1f} USD/MWh<extra></extra>",
        });
    }

    // System-average line — the headline series.
    traces.push({
        x: x, y: data.system_avg,
        type: "scatter", mode: "lines", name: "System average",
        line: { color: "#2C3E50", width: 2 },
        hovertemplate: "Hour %{x}: %{y:,.2f} USD/MWh<extra></extra>",
    });

    const title = `Hourly Electricity Price — Year ${data.year}`;
    const layout = {
        margin: { t: 90, r: 30, b: 60, l: 70 },
        showlegend: true,
        annotations: [
            { text: "<b>" + title + "</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.14, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
        ],
        legend: {
            orientation: "h",
            x: 0.5, xanchor: "center",
            y: 1.06, yanchor: "bottom",
            font: { size: 10 },
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        xaxis: { title: "<b>Hour</b>" },
        yaxis: { title: "<b>LMP (USD/MWh)</b>", rangemode: "tozero" },
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
