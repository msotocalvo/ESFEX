/* Battery Operation — interactive Plotly replica of the matplotlib
 * BatteryOperationChart. Three bar series at the chosen temporal
 * resolution (daily / monthly / yearly):
 *   - Charge (positive, purple)
 *   - Discharge (negative, blue)
 *   - Losses / Spillage (negative, red — stacked below discharge)
 *
 * Payload contract (see BatteryOperationChart._build_payload):
 *   { year, resolution, x_labels: ["Day 1", …] or ["Jan", …],
 *     charge_gwh, discharge_gwh, spillage_gwh }
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
        catch (e) {
            showStatus("Bad payload: " + e.message, true);
            return;
        }
        if (!data || data.error) {
            showStatus(data && data.error ? data.error : "No data", false);
            return;
        }
        if (!data.x_labels || data.x_labels.length === 0) {
            showStatus("No battery data", false);
            return;
        }
        try {
            renderOperation(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error(e);
        }
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

function renderOperation(data) {
    const x = data.x_labels;
    const traces = [];

    // Charge — positive
    if (data.charge_gwh && data.charge_gwh.length) {
        traces.push({
            x: x, y: data.charge_gwh,
            type: "bar", name: "Charge",
            marker: { color: "#9B59B6", opacity: 0.85 },
            offsetgroup: "ops",
            hovertemplate: "%{x}: %{y:,.2f} GWh<extra>Charge</extra>",
        });
    }

    // Discharge — negative (matplotlib flips the sign for the visual)
    if (data.discharge_gwh && data.discharge_gwh.length) {
        const neg = data.discharge_gwh.map(v => -Math.abs(v || 0));
        traces.push({
            x: x, y: neg,
            type: "bar", name: "Discharge",
            marker: { color: "#3498DB", opacity: 0.85 },
            offsetgroup: "ops",
            customdata: data.discharge_gwh,
            hovertemplate: "%{x}: %{customdata:,.2f} GWh<extra>Discharge</extra>",
        });
    }

    // Spillage / losses — negative, stacked under discharge.
    // barmode: "relative" stacks same-sign bars in array order, so
    // adding spillage after discharge piles it below.
    if (data.spillage_gwh && data.spillage_gwh.some(v => v > 0)) {
        const neg = data.spillage_gwh.map(v => -Math.abs(v || 0));
        traces.push({
            x: x, y: neg,
            type: "bar", name: "Losses (Spillage)",
            marker: { color: "#E74C3C", opacity: 0.75 },
            offsetgroup: "ops",
            customdata: data.spillage_gwh,
            hovertemplate: "%{x}: %{customdata:,.2f} GWh<extra>Losses</extra>",
        });
    }

    const resCap = data.resolution.charAt(0).toUpperCase() + data.resolution.slice(1);
    const title = `Battery Operation — Year ${data.year} (${data.resolution})`;

    const layout = {
        // Wider top margin to host title + horizontal legend stack.
        margin: { t: 90, r: 30, b: 60, l: 70 },
        barmode: "relative",
        showlegend: true,
        annotations: [
            { text: "<b>" + title + "</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.14, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
        ],
        shapes: [
            { type: "line", xref: "x domain", yref: "y",
              x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "#000000", width: 1 } },
        ],
        // Horizontal legend just below the title, above the plot.
        legend: {
            orientation: "h",
            x: 0.5, xanchor: "center",
            y: 1.06, yanchor: "bottom",
            font: { size: 10 },
            tracegroupgap: 12,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        xaxis: {
            type: "category",
            title: "<b>" + resCap + "</b>",
            tickangle: -45,
        },
        yaxis: {
            title: "<b>Energy (GWh)</b>",
        },
    };

    Plotly.react("plot", traces, layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
