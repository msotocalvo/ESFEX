/* Fuel Supply — interactive Plotly replica of the matplotlib
 * FuelSupplyChart. Two stacked subplots:
 *   (a) Stacked bars of yearly generation by fuel type (GWh).
 *   (b) Side-by-side bars: Total Generation vs Total Demand, with
 *       Loss of Load stacked on top of demand when present.
 *
 * Payload contract (see FuelSupplyChart._build_payload):
 *   { years, fuels: [{label, color, values_gwh}],
 *     gen_total_gwh, demand_gwh, loss_gwh }
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
            renderFuelSupply(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[fuel_supply.js] render threw:", e);
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

function renderFuelSupply(data) {
    const traces = [];
    const years = data.years;

    // ── Subplot (a): stacked bar — generation by fuel ──
    for (const fuel of (data.fuels || [])) {
        traces.push({
            type: "bar",
            x: years, y: fuel.values_gwh,
            name: fuel.label,
            legendgroup: fuel.label,
            marker: {
                color: fuel.color,
                line: { color: "#FFFFFF", width: 0.5 },
            },
            xaxis: "x", yaxis: "y",
            offsetgroup: "stack_a",
            hovertemplate: "%{x}: %{y:,.1f} GWh<extra>%{fullData.name}</extra>",
        });
    }

    // ── Subplot (b): stacked bar — fuel cost by fuel type (M$) ──
    // Same colour per fuel as subplot a; legendgroup shares the entry
    // so toggling a fuel hides both its generation bar and cost bar.
    for (const fuel of (data.fuel_costs || [])) {
        traces.push({
            type: "bar",
            x: years, y: fuel.values_musd,
            name: fuel.label,
            legendgroup: fuel.label,
            showlegend: false,    // already in legend from subplot a
            marker: {
                color: fuel.color,
                line: { color: "#FFFFFF", width: 0.5 },
            },
            xaxis: "x2", yaxis: "y2",
            offsetgroup: "stack_b",
            hovertemplate: "%{x}: %{y:,.2f} M$<extra>%{fullData.name}</extra>",
        });
    }

    // ── Trend overlays: total per year drawn as a dark line so the
    //    year-over-year direction is unmistakable even with many fuels.
    if (data.total_gen_gwh && data.total_gen_gwh.length) {
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: years, y: data.total_gen_gwh,
            name: "Total Generation",
            line: { color: "#2C3E50", width: 2.5 },
            marker: { color: "#2C3E50", size: 7,
                      line: { color: "#FFFFFF", width: 1 } },
            xaxis: "x", yaxis: "y",
            hovertemplate: "%{x}: %{y:,.1f} GWh<extra>Total</extra>",
        });
    }
    if (data.total_cost_musd && data.total_cost_musd.length) {
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: years, y: data.total_cost_musd,
            name: "Total Fuel Cost",
            line: { color: "#2C3E50", width: 2.5 },
            marker: { color: "#2C3E50", size: 7,
                      line: { color: "#FFFFFF", width: 1 } },
            xaxis: "x2", yaxis: "y2",
            hovertemplate: "%{x}: %{y:,.2f} M$<extra>Total</extra>",
        });
    }

    const layout = {
        // Legend left-anchored at x=0.88 so the gap from the plot
        // edge (0.87) is a deterministic 0.01 — ~20% of the previous
        // ~0.03 gap (which depended on the auto legend width when
        // it was right-anchored at x=1.0).
        margin: { t: 70, r: 2, b: 70, l: 80 },
        showlegend: true,
        barmode: "relative",
        annotations: [
            { text: "<b>a) Generation by Fuel Type</b>",
              x: 0.435, xref: "paper", xanchor: "center",
              y: 1.00, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) Fuel Cost by Type</b>",
              x: 0.435, xref: "paper", xanchor: "center",
              y: 0.46, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ],
        legend: {
            orientation: "v",
            x: 0.88, xanchor: "left",
            y: 0.5,  yanchor: "middle",
            font: { size: 10 },
            tracegroupgap: 6,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        xaxis: {
            domain: [0, 0.87], anchor: "y",
            type: "category",
            showticklabels: false,        // ticks shown on subplot b
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis: {
            domain: [0.54, 0.98], anchor: "x",
            title: "<b>Generation (GWh)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        xaxis2: {
            domain: [0, 0.87], anchor: "y2",
            type: "category",
            tickangle: -45,
            title: "<b>Year</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis2: {
            domain: [0.0, 0.44], anchor: "x2",
            title: "<b>Fuel Cost (M$)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
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
