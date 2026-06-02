/* Revenue & Profitability by Technology — Plotly interactive chart.
 *
 * (a) Stacked bars of Revenue (M$) by canonical technology per year,
 *     with a total-revenue trend line overlaid for quick reading.
 * (b) Per-tech grouped bars: average Selling Price ($/MWh, blue) and
 *     LCOE ($/MWh, orange), with the profit margin (% of selling
 *     price) annotated above each pair — green when positive, red
 *     when negative.
 *
 * Payload contract (see RevenueProfitabilityChart._build_payload):
 *   { years, techs: [{label,color,revenue_musd:[],values_by_year:[]}],
 *     totals_musd:[per year],
 *     summary: [{label, color, avg_price, lcoe, margin_pct}] }
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
            showStatus(data && data.error ? data.error : "No revenue data", false);
            return;
        }
        if (!data.years || data.years.length === 0) {
            showStatus("No revenue data", false);
            return;
        }
        try {
            renderRevenue(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[revenue_profitability.js] render threw:", e);
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

function renderRevenue(data) {
    const traces = [];
    const years = data.years;

    // ── Subplot (a): stacked bars Revenue by tech per year ──
    for (const t of (data.techs || [])) {
        traces.push({
            type: "bar",
            x: years, y: t.revenue_musd,
            name: t.label,
            legendgroup: t.label,
            marker: {
                color: t.color,
                line: { color: "#FFFFFF", width: 0.5 },
            },
            xaxis: "x", yaxis: "y",
            offsetgroup: "stack_a",
            hovertemplate: "%{x}: %{y:,.2f} M$<extra>%{fullData.name}</extra>",
        });
    }
    // Total trend line
    if (data.totals_musd && data.totals_musd.length) {
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: years, y: data.totals_musd,
            name: "Total Revenue",
            line: { color: "#2C3E50", width: 2.5 },
            marker: { color: "#2C3E50", size: 7,
                      line: { color: "#FFFFFF", width: 1 } },
            xaxis: "x", yaxis: "y",
            hovertemplate: "%{x}: %{y:,.1f} M$<extra>Total</extra>",
        });
    }

    // ── Subplot (b): per-tech Selling Price vs LCOE, with margin annot.
    const summary = data.summary || [];
    const techLabels = summary.map(s => s.label);
    const prices    = summary.map(s => s.avg_price);
    const lcoes     = summary.map(s => s.lcoe);
    const marginPct = summary.map(s => s.margin_pct);

    if (techLabels.length) {
        traces.push({
            type: "bar",
            x: techLabels, y: prices,
            name: "Avg Selling Price",
            marker: { color: "#3498DB",
                      line: { color: "#FFFFFF", width: 0.5 } },
            xaxis: "x2", yaxis: "y2",
            offsetgroup: "price",
            hovertemplate: "%{x}: %{y:,.2f} $/MWh<extra>Selling Price</extra>",
        });
        traces.push({
            type: "bar",
            x: techLabels, y: lcoes,
            name: "LCOE",
            marker: { color: "#E67E22",
                      line: { color: "#FFFFFF", width: 0.5 } },
            xaxis: "x2", yaxis: "y2",
            offsetgroup: "lcoe",
            hovertemplate: "%{x}: %{y:,.2f} $/MWh<extra>LCOE</extra>",
        });
    }

    // Margin annotations above each tech bar pair (green if positive,
    // red if negative — signals which techs cover their costs at the
    // current market price).
    const annotations = [
        { text: "<b>a) Revenue by Technology</b>",
          x: 0.435, xref: "paper", xanchor: "center",
          y: 1.00, yref: "paper", yanchor: "bottom",
          showarrow: false, font: { size: 13 } },
        { text: "<b>b) Selling Price vs LCOE (Profit Margin %)</b>",
          x: 0.435, xref: "paper", xanchor: "center",
          y: 0.46, yref: "paper", yanchor: "bottom",
          showarrow: false, font: { size: 13 } },
    ];
    for (let i = 0; i < summary.length; i++) {
        const m = summary[i].margin_pct;
        if (m == null || !isFinite(m)) continue;
        const yTop = Math.max(prices[i] || 0, lcoes[i] || 0);
        const mColor = m >= 0 ? "#27AE60" : "#C0392B";
        annotations.push({
            xref: "x2", yref: "y2",
            x: summary[i].label, y: yTop,
            yshift: 8, xanchor: "center", yanchor: "bottom",
            text: `<b>${m >= 0 ? "+" : ""}${m.toFixed(1)}%</b>`,
            showarrow: false,
            font: { color: mColor, size: 10 },
            // White chip with a coloured border so the value stays
            // legible over bars of any colour and on dark themes.
            bgcolor: "rgba(255,255,255,0.9)",
            bordercolor: mColor,
            borderwidth: 1,
            borderpad: 2,
        });
    }

    const layout = {
        // Right margin trimmed to ≈2 px so the legend hugs the widget
        // edge (≈20% of the previous 8 px gutter); plot still extends
        // to 0.87 and legend starts at 0.88.
        margin: { t: 70, r: 2, b: 110, l: 80 },
        showlegend: true,
        barmode: "relative",
        annotations: annotations,
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
            tickangle: -45,
            title: "<b>Year</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis: {
            domain: [0.54, 0.98], anchor: "x",
            title: "<b>Revenue (M$)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        xaxis2: {
            domain: [0, 0.87], anchor: "y2",
            type: "category",
            tickangle: -45,
            title: "<b>Technology</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis2: {
            domain: [0.0, 0.44], anchor: "x2",
            title: "<b>$/MWh</b>",
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
