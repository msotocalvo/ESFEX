/* Energy Flow (Sankey) — interactive Plotly version.
 *
 * Python builds the full Plotly figure (data + layout) with all four
 * Sankey columns (primary energy → tech → bus → end-uses) and ships
 * it through QWebChannel as a Plotly JSON string. The JS side just
 * parses + Plotly.newPlot — no transformation here, the heavy lifting
 * lives in SankeyEnergyFlowChart._build_payload.
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
        try {
            renderSankey(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[sankey.js] render threw:", e);
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

function renderSankey(data) {
    // The payload IS a Plotly figure dict ({data, layout}), so we can
    // hand it straight to newPlot.
    const traces = data.data || [];
    const layout = data.layout || {};
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
