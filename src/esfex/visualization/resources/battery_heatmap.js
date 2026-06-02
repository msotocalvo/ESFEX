/* Battery Heatmap — interactive Plotly replica of the matplotlib
 * BatteryHeatmapChart. Renders a 12-month × N-year heatmap of the
 * net battery flow (charge − discharge), with optional Gaussian
 * smoothing already applied by Python (sigma is a UI param).
 *
 * Payload contract (see BatteryHeatmapChart._build_payload):
 *   { years: ["2030", …], month_labels: ["Jan", …, "Dec"],
 *     values_mwh: [[12 rows × N cols matrix]], sigma: 1.0 }
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
        if (!data.years || !data.years.length
            || !data.values_mwh || !data.values_mwh.length) {
            showStatus("No battery data", false);
            return;
        }
        try {
            renderHeatmap(data);
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

// Plotly's heatmap ``colorscale`` prop accepts either an array of
// [stop, color] pairs or a *named* string. The named list shipped
// with Plotly.js's heatmap is fixed and small — modern matplotlib
// scales (Turbo, Plasma, Magma, Inferno) are bundled under
// Plotly.colors.sequential but are NOT recognised when passed as a
// name string to colorscale, so they silently fall back to the
// default Viridis-like scale (which is what the user was seeing).
// We resolve every name through this map so the label and the actual
// rendered palette always match.
const NAMED_COLORSCALES = {
    Turbo: [
        [0.000, "#30123b"], [0.125, "#4453d6"], [0.250, "#3ea6f8"],
        [0.375, "#23dde0"], [0.500, "#46f08c"], [0.625, "#aafa3f"],
        [0.750, "#f6c12f"], [0.875, "#ee5b1a"], [1.000, "#7a0402"],
    ],
    Plasma: [
        [0.000, "#0d0887"], [0.125, "#41049d"], [0.250, "#6a00a8"],
        [0.375, "#8f0da4"], [0.500, "#b12a90"], [0.625, "#cc4778"],
        [0.750, "#e16462"], [0.875, "#f1834b"], [1.000, "#f0f921"],
    ],
    Magma: [
        [0.000, "#000004"], [0.125, "#180f3d"], [0.250, "#440f76"],
        [0.375, "#721f81"], [0.500, "#9e2f7f"], [0.625, "#cd4071"],
        [0.750, "#f1605d"], [0.875, "#fd9668"], [1.000, "#fcfdbf"],
    ],
    Inferno: [
        [0.000, "#000004"], [0.125, "#1b0c41"], [0.250, "#4a0c6b"],
        [0.375, "#781c6d"], [0.500, "#a52c60"], [0.625, "#cf4446"],
        [0.750, "#ed6925"], [0.875, "#fb9b06"], [1.000, "#fcffa4"],
    ],
    // The rest are recognised by Plotly.js as named strings.
    Viridis: "Viridis", Cividis: "Cividis", Jet: "Jet", Hot: "Hot",
    Greys: "Greys", Electric: "Electric",
    RdBu: "RdBu", Bluered: "Bluered", Portland: "Portland",
    Earth: "Earth", Picnic: "Picnic", Rainbow: "Rainbow",
};

function resolveColorscale(name) {
    return NAMED_COLORSCALES[name] || name;
}

function renderHeatmap(data) {
    const colormap = data.colormap || "Turbo";
    const traces = [];

    // ── Subplot a) — Monthly net battery flow (heatmap) ──
    traces.push({
        type: "heatmap",
        z: data.values_mwh,
        x: data.years,
        y: data.month_labels,
        colorscale: resolveColorscale(colormap),
        colorbar: {
            title: { text: "MWh", side: "right" },
            thickness: 12, len: 0.40,
            // Pinned right after the heatmap and aligned with subplot
            // a's y-domain centre (~0.78). Legend lives at the same x
            // but at y=0.22 (subplot b), so they don't overlap.
            x: 0.92, xanchor: "left",
            y: 0.78, yanchor: "middle",
        },
        xaxis: "x", yaxis: "y",
        hovertemplate:
            "<b>%{y} %{x}</b><br>" +
            "Discharge: %{z:,.1f} MWh<extra></extra>",
    });

    // ── Subplot b) — Annual Arbitrage P&L ──
    const years = data.years || [];
    if (data.discharge_revenue_musd && data.discharge_revenue_musd.length) {
        traces.push({
            type: "bar",
            x: years, y: data.discharge_revenue_musd,
            name: "Discharge revenue",
            marker: { color: "#1F8FBF",
                      line: { color: "#FFFFFF", width: 0.5 } },
            xaxis: "x2", yaxis: "y2",
            offsetgroup: "arb",
            hovertemplate: "%{x}: %{y:,.2f} M$<extra>Revenue</extra>",
        });
    }
    if (data.charge_cost_musd && data.charge_cost_musd.length) {
        const neg = data.charge_cost_musd.map(v => -Math.abs(v || 0));
        traces.push({
            type: "bar",
            x: years, y: neg,
            name: "Charge cost",
            marker: { color: "#7D3C98",
                      line: { color: "#FFFFFF", width: 0.5 } },
            xaxis: "x2", yaxis: "y2",
            offsetgroup: "arb",
            customdata: data.charge_cost_musd,
            hovertemplate: "%{x}: %{customdata:,.2f} M$<extra>Cost</extra>",
        });
    }
    if (data.margin_dollar_per_mwh && data.margin_dollar_per_mwh.length) {
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: years, y: data.margin_dollar_per_mwh,
            name: "Net margin",
            line: { color: "#2C3E50", width: 2.5 },
            marker: { color: "#2C3E50", size: 7,
                      line: { color: "#FFFFFF", width: 1 } },
            xaxis: "x2", yaxis: "y3",
            hovertemplate: "%{x}: %{y:,.2f} $/MWh<extra>Margin</extra>",
        });
    }

    const layout = {
        // Right margin reserves space for both the heatmap colorbar
        // (aligned to subplot a) and the arbitrage legend (aligned
        // to subplot b). No bottom legend → b can be slim again.
        margin: { t: 50, r: 50, b: 70, l: 60 },
        showlegend: true,
        barmode: "relative",
        annotations: [
            { text: "<b>a) Monthly Battery Discharge</b>",
              x: 0.455, xref: "paper", xanchor: "center",
              y: 1.00, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) Annual Arbitrage P&L</b>",
              x: 0.455, xref: "paper", xanchor: "center",
              y: 0.46, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ],
        // Legend anchored to the top-left corner of subplot b
        // (xaxis2/yaxis2 cover x ∈ [0, 0.91], y ∈ [0, 0.44]), so it
        // sits opposite to the right-hand yaxis3 ($/MWh) title.
        legend: {
            orientation: "v",
            x: 0.005, xanchor: "left",
            y: 0.44,  yanchor: "top",
            font: { size: 10 },
            tracegroupgap: 6,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        // automargin disabled on EVERY axis — the outer `margin`
        // above already reserves enough room for axis titles, and
        // automargin + overlay yaxis3 + legend bbox would otherwise
        // ping-pong until Plotly aborts with the cryptic
        // "Something went wrong with axis scaling".
        // Subplot a: heatmap (top half)
        xaxis: {
            domain: [0, 0.91], anchor: "y",
            type: "category", tickangle: -45,
            title: "<b>Year</b>",
            automargin: false,
        },
        yaxis: {
            domain: [0.54, 0.98], anchor: "x",
            type: "category",
            title: "<b>Month</b>",
            automargin: false,
        },
        // Subplot b: arbitrage bars + margin line (bottom half)
        xaxis2: {
            domain: [0, 0.91], anchor: "y2",
            type: "category", tickangle: -45,
            title: "<b>Year</b>",
            gridcolor: "rgba(0,0,0,0.08)",
            automargin: false,
        },
        yaxis2: {
            domain: [0.0, 0.44], anchor: "x2",
            title: "<b>Annual P&L (M$)</b>",
            gridcolor: "rgba(0,0,0,0.08)",
            automargin: false,
        },
        yaxis3: {
            domain: [0.0, 0.44], anchor: "x2",
            overlaying: "y2", side: "right",
            title: { text: "<b>Margin ($/MWh disch.)</b>",
                     font: { color: "#2C3E50", size: 10 } },
            showgrid: false,
            automargin: false,
        },
    };

    Plotly.react("plot", traces, layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

// Cheap colormap swap — called from Python via runJavaScript when the
// user picks a new entry from the Qt combo, so we don't rebuild the
// payload for a purely visual change.
function setColormap(name) {
    const plotEl = document.getElementById("plot");
    if (plotEl && plotEl.data && plotEl.data.length > 0) {
        Plotly.restyle("plot", { colorscale: [resolveColorscale(name)] }, [0]);
    }
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
