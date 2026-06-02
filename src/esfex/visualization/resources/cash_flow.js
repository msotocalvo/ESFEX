/* Cash Flow — Plotly interactive chart.
 *
 * (a) Annual composition: stacked bars with positive inflows (Revenue,
 *     fuchsia/green) and negative outflows (Fuel cost, Investment
 *     CapEx, CO₂ cost, Loss-of-load penalty). A dark line on the same
 *     axis carries the Net Cash Flow per year for quick reading.
 * (b) Cumulative cash flow over the planning horizon: undiscounted
 *     (grey) and discounted at the rate the user selects in the Qt
 *     params bar (green). A vertical dashed marker pin-points the
 *     payback year (first year when undiscounted cumulative ≥ 0).
 *
 * Payload (see CashFlowChart._build_payload):
 *   { years, components:[{label, color, values_musd, sign}],
 *     net_musd:[per year], cumulative_musd:[], cumulative_npv_musd:[],
 *     discount_rate, payback_year (or null) }
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
            showStatus(data && data.error ? data.error : "No cash flow data", false);
            return;
        }
        if (!data.years || data.years.length === 0) {
            showStatus("No cash flow data", false);
            return;
        }
        try {
            renderCashFlow(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[cash_flow.js] render threw:", e);
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

function renderCashFlow(data) {
    const traces = [];
    const shapes = [];
    const annotations = [];
    // Two parallel year representations:
    //  - `years`  : string array used by subplot a (bars). Plotly's
    //               category axis distributes bars cleanly when their
    //               x is a string.
    //  - `yearsN` : numeric array for subplot b. Plotly's category
    //               axis was collapsing the scatter+shape combo to the
    //               left edge (the payback shape's string x0 was
    //               re-ordering the category list); switching the
    //               bottom subplot to a numeric (linear) axis avoids
    //               that interaction entirely.
    const years = data.years;
    const yearsN = years.map(Number);

    // ── Subplot (a): stacked composition bars ──
    for (const c of (data.components || [])) {
        const yVals = c.sign < 0
            ? (c.values_musd || []).map(v => -Math.abs(v || 0))
            : (c.values_musd || []);
        traces.push({
            type: "bar",
            x: years, y: yVals,
            name: c.label,
            marker: { color: c.color,
                      line: { color: "#FFFFFF", width: 0.5 } },
            xaxis: "x", yaxis: "y",
            offsetgroup: "cf",
            customdata: (c.values_musd || []).map(v => Math.abs(v || 0)),
            hovertemplate: "%{x}: %{customdata:,.2f} M$<extra>" + c.label + "</extra>",
        });
    }
    // Net Cash Flow line on the same axis as the bars
    if (data.net_musd && data.net_musd.length) {
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: years, y: data.net_musd,
            name: "Net Cash Flow",
            line: { color: "#2C3E50", width: 2.5 },
            marker: { color: "#2C3E50", size: 7,
                      line: { color: "#FFFFFF", width: 1 } },
            xaxis: "x", yaxis: "y",
            hovertemplate: "%{x}: %{y:,.2f} M$<extra>Net CF</extra>",
        });
    }

    // ── Subplot (b): cumulative cash flow (numeric x axis) ──
    //
    // Layer order (bottom → top):
    //   1. Green fill for years where cumulative ≥ 0 (recovery zone).
    //   2. Red fill for years where cumulative < 0 (in-the-red zone).
    //   3. Faint lines: cumulative Revenue (green) and cumulative Cost
    //      (red) — show how the two totals diverge or converge.
    //   4. Net cumulative (dark, prominent).
    //   5. NPV cumulative (green dashed).
    //
    // The recovery shading uses Plotly's "fill: 'tozeroy'" trick: two
    // copies of the cumulative series, one masked to positives, one to
    // negatives. NaN gaps break the fill between sign-change segments
    // so the colours don't bleed across the zero crossing.
    const cum = data.cumulative_musd || [];
    if (cum.length) {
        const cumPos = cum.map(v => v >= 0 ? v : null);
        const cumNeg = cum.map(v => v <  0 ? v : null);
        traces.push({
            type: "scatter", mode: "none",
            x: yearsN, y: cumPos,
            fill: "tozeroy", fillcolor: "rgba(39,174,96,0.18)",
            xaxis: "x2", yaxis: "y2",
            name: "Recovery zone",
            legendgroup: "recovery_pos",
            hoverinfo: "skip",
            connectgaps: false,
        });
        traces.push({
            type: "scatter", mode: "none",
            x: yearsN, y: cumNeg,
            fill: "tozeroy", fillcolor: "rgba(231,76,60,0.18)",
            xaxis: "x2", yaxis: "y2",
            name: "In-the-red zone",
            legendgroup: "recovery_neg",
            hoverinfo: "skip",
            connectgaps: false,
        });
    }

    if (data.cumulative_revenue_musd && data.cumulative_revenue_musd.length) {
        traces.push({
            type: "scatter", mode: "lines",
            x: yearsN, y: data.cumulative_revenue_musd,
            name: "Cumulative Revenue",
            line: { color: "#27AE60", width: 1.5, dash: "dot" },
            xaxis: "x2", yaxis: "y2",
            hovertemplate: "%{x}: %{y:,.2f} M$<extra>Σ Revenue</extra>",
        });
    }
    if (data.cumulative_cost_musd && data.cumulative_cost_musd.length) {
        // Cumulative cost is shown as a negative line so it lives in
        // the same axis sign as the outflow bars in subplot a — easier
        // mental link between the two panels.
        const negCost = data.cumulative_cost_musd.map(v => -Math.abs(v || 0));
        traces.push({
            type: "scatter", mode: "lines",
            x: yearsN, y: negCost,
            name: "Cumulative Cost",
            line: { color: "#C0392B", width: 1.5, dash: "dot" },
            xaxis: "x2", yaxis: "y2",
            customdata: data.cumulative_cost_musd,
            hovertemplate: "%{x}: %{customdata:,.2f} M$<extra>Σ Cost</extra>",
        });
    }

    if (cum.length) {
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: yearsN, y: cum,
            name: "Cumulative Net (undisc.)",
            line: { color: "#2C3E50", width: 2.8 },
            marker: { color: "#2C3E50", size: 7,
                      line: { color: "#FFFFFF", width: 1 } },
            xaxis: "x2", yaxis: "y2",
            hovertemplate: "%{x}: %{y:,.2f} M$<extra>Net Σ</extra>",
        });
    }
    if (data.cumulative_npv_musd && data.cumulative_npv_musd.length) {
        const rate = (data.discount_rate || 0) * 100;
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: yearsN, y: data.cumulative_npv_musd,
            name: `Cumulative NPV (${rate.toFixed(1)}%)`,
            line: { color: "#16A085", width: 2.5, dash: "dash" },
            marker: { color: "#16A085", size: 6,
                      line: { color: "#FFFFFF", width: 1 } },
            xaxis: "x2", yaxis: "y2",
            hovertemplate: "%{x}: %{y:,.2f} M$<extra>NPV</extra>",
        });
    }

    // Baseline at zero for both subplots
    shapes.push({
        type: "line", xref: "x domain", yref: "y",
        x0: 0, x1: 1, y0: 0, y1: 0,
        line: { color: "#000000", width: 1 },
    });
    shapes.push({
        type: "line", xref: "x2 domain", yref: "y2",
        x0: 0, x1: 1, y0: 0, y1: 0,
        line: { color: "#000000", width: 1 },
    });

    // Payback marker on subplot b (numeric x — matches the cumulative
    // traces' axis type so the dashed line lands on the right year).
    if (data.payback_year != null) {
        const pyN = Number(data.payback_year);
        shapes.push({
            type: "line", xref: "x2", yref: "y2 domain",
            x0: pyN, x1: pyN, y0: 0, y1: 1,
            line: { color: "#E67E22", width: 1.5, dash: "dot" },
        });
        annotations.push({
            xref: "x2", yref: "y2 domain",
            x: pyN, y: 1.0,
            xanchor: "left", yanchor: "top",
            text: `<b>Payback: ${data.payback_year}</b>`,
            showarrow: false, font: { color: "#E67E22", size: 10 },
            bgcolor: "rgba(255,255,255,0.8)",
        });
    }

    const layout = {
        // Wider bottom margin to host the horizontal legend below
        // subplot b's tick labels.
        margin: { t: 70, r: 30, b: 140, l: 80 },
        showlegend: true,
        barmode: "relative",
        annotations: annotations.concat([
            { text: "<b>a) Annual Cash Flow Composition</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.00, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) Cumulative Cash Flow</b>" +
                    (data.payback_year == null
                        ? "  <i>(not yet recovered)</i>" : ""),
              x: 0.5, xref: "paper", xanchor: "center",
              y: 0.46, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ]),
        shapes: shapes,
        // Horizontal legend pinned below subplot b's tick labels.
        legend: {
            orientation: "h",
            x: 0.5, xanchor: "center",
            y: -0.18, yanchor: "top",
            font: { size: 10 },
            tracegroupgap: 12,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        xaxis: {
            domain: [0, 1], anchor: "y",
            type: "category", tickangle: -45,
            title: "<b>Year</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis: {
            domain: [0.54, 0.98], anchor: "x",
            title: "<b>Annual (M$)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        // Numeric (linear) axis for subplot b — see comment near the
        // `yearsN` definition. tickvals/ticktext pin one tick per year.
        xaxis2: {
            domain: [0, 1], anchor: "y2",
            type: "linear", tickangle: -45,
            tickvals: yearsN, ticktext: years,
            range: yearsN.length
                ? [yearsN[0] - 0.5, yearsN[yearsN.length - 1] + 0.5]
                : undefined,
            title: "<b>Year</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis2: {
            domain: [0.0, 0.44], anchor: "x2",
            title: "<b>Cumulative (M$)</b>",
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
