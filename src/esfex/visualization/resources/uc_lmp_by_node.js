/* LMP by Node Map (UC) — locational price spread reveals congestion.
 *
 * Three coordinated panels:
 *   Top:    Heatmap node × hour of LMPs.
 *   Mid:    Per-node mean LMP bar (ranked) — quick "which node is
 *           pricey on average?".
 *   Bottom: Coincident system-average line — context for the heatmap.
 *
 * Wide vertical bands of color mean transmission limits are pinned
 * (LMPs diverge between regions). Hot rows = constrained nodes.
 *
 * Payload:
 *   { year, hours: [...], nodes: ["Node 0", ...],
 *     z: [[hour, ...], per node],
 *     mean_by_node: [...],
 *     system_avg: [...] }
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
        if (!data.nodes || !data.nodes.length) { _ucClearPlot(); showStatus("No nodal data", false); return; }
        try { renderLMP(data); hideStatus(); }
        catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
    });
}
function showStatus(t, e) { const s = document.getElementById("status"); s.textContent = t; s.style.display = "block"; s.style.color = e ? "#c0392b" : "#7F8C8D"; }
function hideStatus() { document.getElementById("status").style.display = "none"; }

function renderLMP(data) {
    const heatmap = {
        z: data.z, x: data.hours, y: data.nodes,
        type: "heatmap", colorscale: "Plasma",
        colorbar: {
            title: { text: "USD/MWh", side: "right" },
            len: 0.55, y: 0.72, yanchor: "middle", thickness: 12,
        },
        hovertemplate: "Node %{y}<br>Hour %{x}<br>LMP: %{z:,.1f} USD/MWh<extra></extra>",
        xaxis: "x", yaxis: "y",
    };

    // Mid panel: per-node mean ranked desc.
    const idx = data.mean_by_node.map((v, i) => [v, i]).sort((a, b) => b[0] - a[0]);
    const rankedNodes = idx.map(([_, i]) => data.nodes[i]);
    const rankedMean  = idx.map(([v, _]) => v);
    const bar = {
        x: rankedNodes, y: rankedMean, type: "bar",
        marker: { color: "#9B59B6", opacity: 0.85 },
        hovertemplate: "%{x}: mean %{y:,.1f} USD/MWh<extra></extra>",
        xaxis: "x2", yaxis: "y2", showlegend: false,
    };

    const sysavg = {
        x: data.hours, y: data.system_avg,
        type: "scatter", mode: "lines",
        line: { color: "#2C3E50", width: 2 },
        hovertemplate: "Hour %{x}: %{y:,.1f} USD/MWh<extra>System avg</extra>",
        xaxis: "x3", yaxis: "y3", showlegend: false,
    };

    const spread = Math.max(...data.mean_by_node) - Math.min(...data.mean_by_node);
    const subTitle =
        `Per-node spread (mean max − min): ${spread.toFixed(1)} USD/MWh` +
        (spread > 1 ? " — transmission constraints active" : " — uniform pricing");

    const layout = {
        margin: { t: 110, r: 70, b: 60, l: 100 },
        grid: { rows: 3, columns: 1, pattern: "independent", roworder: "top to bottom" },
        annotations: [
            { text: `<b>Locational Marginal Prices — Year ${data.year}</b>`,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.13, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: subTitle,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.07, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11, color: "#5D6D7E" } },
        ],
        xaxis:  { domain: [0, 1], anchor: "y", showticklabels: false },
        yaxis:  { domain: [0.55, 1.0], title: "<b>Node</b>", autorange: "reversed" },
        xaxis2: { domain: [0, 1], anchor: "y2", tickangle: -25 },
        yaxis2: { domain: [0.28, 0.48], title: "<b>Mean LMP</b>", rangemode: "tozero" },
        xaxis3: { domain: [0, 1], anchor: "y3", title: "<b>Hour</b>" },
        yaxis3: { domain: [0.0, 0.20], title: "<b>System avg</b>", rangemode: "tozero" },
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", [heatmap, bar, sysavg], layout, {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
