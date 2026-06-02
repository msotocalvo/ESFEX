/* Price Duration & Composition — Plotly interactive chart.
 *
 * (a) Price Duration Curve: hourly electricity prices sorted descending
 *     per year (one line per year, coloured by year). Reveals peak
 *     prices, base load levels and the spread/volatility of each year.
 * (b) Energy vs. Congestion split: stacked area showing the share of
 *     the monthly average price that comes from the "energy" component
 *     (system-wide marginal cost) versus the "congestion" component
 *     (nodal premium) — a high congestion share signals binding network
 *     constraints.
 *
 * Payload contract (see PriceDurationChart._build_payload):
 *   { years: ["2030", …],
 *     duration_curves: [{year, color, prices_sorted_desc, x_pct}],
 *     monthly: { labels:["2030-01",…], energy:[…], congestion:[…] } }
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
            showStatus(data && data.error ? data.error : "No price data", false);
            return;
        }
        try {
            renderPrice(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[price_duration.js] render threw:", e);
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

function renderPrice(data) {
    const traces = [];

    // ── Subplot (a) — Price Duration Curve (one line per year) ──
    for (const dc of (data.duration_curves || [])) {
        traces.push({
            type: "scatter", mode: "lines",
            x: dc.x_pct, y: dc.prices_sorted_desc,
            name: String(dc.year),
            legendgroup: String(dc.year),
            line: { color: dc.color, width: 1.5 },
            xaxis: "x", yaxis: "y",
            hovertemplate:
                "Top %{x:.1f}%<br>Price: %{y:,.1f} $/MWh<extra>" +
                dc.year + "</extra>",
        });
    }

    // ── Subplot (b) — Energy vs Congestion (stacked area) ──
    const m = data.monthly || {};
    const hasMonthly = m.labels && m.labels.length
        && (m.energy || m.congestion);
    if (hasMonthly) {
        if (m.energy && m.energy.length) {
            traces.push({
                type: "scatter", mode: "lines",
                x: m.labels, y: m.energy,
                name: "Energy component",
                line: { width: 0, color: "#3498DB" },
                fillcolor: "rgba(52,152,219,0.55)",
                stackgroup: "comp",
                xaxis: "x2", yaxis: "y2",
                hovertemplate: "%{x}: %{y:,.2f} $/MWh<extra>Energy</extra>",
            });
        }
        if (m.congestion && m.congestion.length) {
            traces.push({
                type: "scatter", mode: "lines",
                x: m.labels, y: m.congestion,
                name: "Congestion premium",
                line: { width: 0, color: "#E67E22" },
                fillcolor: "rgba(230,126,34,0.55)",
                stackgroup: "comp",
                xaxis: "x2", yaxis: "y2",
                hovertemplate: "%{x}: %{y:,.2f} $/MWh<extra>Congestion</extra>",
            });
        }
    }

    const layout = {
        margin: { t: 70, r: 8, b: 90, l: 70 },
        showlegend: true,
        annotations: [
            { text: "<b>a) Price Duration Curve</b>",
              x: 0.435, xref: "paper", xanchor: "center",
              y: 1.00, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) Energy vs Congestion (monthly avg)</b>",
              x: 0.435, xref: "paper", xanchor: "center",
              y: 0.46, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ],
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
            title: "<b>Cumulative % of hours</b>",
            ticksuffix: "%",
            range: [0, 100],
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis: {
            domain: [0.54, 0.98], anchor: "x",
            title: "<b>Price ($/MWh)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        xaxis2: {
            domain: [0, 0.87], anchor: "y2",
            type: "category", tickangle: -45,
            title: "<b>Month</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis2: {
            domain: [0.0, 0.44], anchor: "x2",
            title: "<b>Price ($/MWh)</b>",
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
