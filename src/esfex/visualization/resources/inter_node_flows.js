/* Inter-Node Flows — interactive Plotly replica of the matplotlib
 * InterNodeFlowsChart. Per-year stacked bars: imports (positive) above
 * the zero line, exports (negative) below. Each node has its own colour
 * shared between its import and export bars (exports use lower opacity
 * so the matching pair reads clearly).
 *
 * Payload contract (see InterNodeFlowsChart._build_payload):
 *   { years: ["2030", …],
 *     nodes: [{label, color, imports_gwh, exports_gwh}] }
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
            _ucClearPlot(); showStatus(data && data.error ? data.error : "No power flow data", false);
            return;
        }
        if (!data.nodes || data.nodes.length === 0) {
            _ucClearPlot(); showStatus("No power flow data", false);
            return;
        }
        try {
            if (data.mode === "uc") {
                renderFlowsUC(data);
            } else {
                if (!data.years || data.years.length === 0) {
                    _ucClearPlot(); showStatus("No power flow data", false);
                    return;
                }
                renderFlows(data);
            }
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[inter_node_flows.js] render threw:", e);
        }
    });
}

function renderFlowsUC(data) {
    // Hourly net flow per node: positive = importing, negative =
    // exporting. One line per node.
    const traces = data.nodes.map(node => ({
        x: data.hours, y: node.net_mw,
        type: "scatter", mode: "lines",
        name: node.label,
        line: { color: node.color, width: 2 },
        hovertemplate:
            "Hour %{x}: %{y:,.1f} MW<extra>" + node.label + "</extra>",
    }));
    const layout = {
        margin: { t: 100, r: 30, b: 60, l: 80 },
        showlegend: true,
        annotations: [
            { text: "<b>Inter-Node Net Flow — Year " + data.year + "</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.10, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: "Positive = net importing &nbsp;|&nbsp; Negative = net exporting",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.04, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11, color: "#5D6D7E" } },
        ],
        legend: { font: { size: 10 } },
        shapes: [
            { type: "line", xref: "x domain", yref: "y",
              x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "#000000", width: 1 } },
        ],
        xaxis: { title: "<b>Hour</b>" },
        yaxis: { title: "<b>Net flow (MW)</b>" },
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout, {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
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

function renderFlows(data) {
    const traces = [];
    const years = data.years;

    for (const node of data.nodes) {
        // Imports — positive bars
        traces.push({
            type: "bar",
            x: years, y: node.imports_gwh,
            name: node.label,
            legendgroup: node.label,
            marker: { color: node.color, opacity: 0.85 },
            offsetgroup: "flows",
            hovertemplate:
                "%{x}: %{y:,.1f} GWh<extra>" + node.label + " · imports</extra>",
        });
        // Exports — negative bars (same colour, lower opacity)
        const neg = (node.exports_gwh || []).map(v => -Math.abs(v || 0));
        traces.push({
            type: "bar",
            x: years, y: neg,
            name: node.label,
            legendgroup: node.label,
            showlegend: false,
            marker: { color: node.color, opacity: 0.45 },
            offsetgroup: "flows",
            customdata: node.exports_gwh,
            hovertemplate:
                "%{x}: %{customdata:,.1f} GWh<extra>" + node.label + " · exports</extra>",
        });
    }

    const layout = {
        // Tight right margin so the legend hugs the figure edge
        // (≈20% of the previous 30 px gutter).
        margin: { t: 60, r: 6, b: 70, l: 80 },
        barmode: "relative",
        showlegend: true,
        annotations: [
            { text: "<b>Inter-Node Power Flows (Imports ↑ / Exports ↓)</b>",
              x: 0.435, xref: "paper", xanchor: "center",
              y: 1.02, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
        ],
        shapes: [
            // Zero baseline across the plot
            { type: "line", xref: "x domain", yref: "y",
              x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "#000000", width: 1 } },
        ],
        // Plot ends at x=0.87, legend starts at x=0.88 → 0.01 gap
        // (≈20% of the previous 0.05 between plot end and legend start).
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
            domain: [0, 0.87],
            type: "category",
            tickangle: -45,
            title: "<b>Year</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis: {
            title: "<b>Energy (GWh)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
            zeroline: false,
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
