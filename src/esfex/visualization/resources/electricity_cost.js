/* Electricity Cost — interactive Plotly replica of the matplotlib
 * ElectricityCostChart. Two stacked subplots:
 *   (a) Contour plot of daily electricity price (365 days × N years)
 *       with an annual-average price line over a secondary y-axis.
 *   (b) Generation-weighted price distribution: stacked histogram of
 *       Renewable (green) vs Non-Renewable (red) prices, with each
 *       group's weighted-mean drawn as a vertical dashed line.
 *
 * Payload built by ElectricityCostChart._build_payload Python-side.
 * The Qt params widget owns sigma (data) and the colormap (visual) —
 * sigma triggers a rebuild, colormap is a cheap Plotly.restyle.
 */
"use strict";

// Same Plotly.js named/modern colorscale map as battery_heatmap.js.
// Modern matplotlib scales (Turbo, Plasma, Magma, Inferno) live outside
// the heatmap-accepted name list and need explicit colour stops.
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
    Viridis: "Viridis", Cividis: "Cividis", Jet: "Jet", Hot: "Hot",
    Greys: "Greys", Electric: "Electric",
    RdBu: "RdBu", Bluered: "Bluered", Portland: "Portland",
    Earth: "Earth", Picnic: "Picnic", Rainbow: "Rainbow",
};

function resolveColorscale(name) {
    return NAMED_COLORSCALES[name] || name;
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
        catch (e) { showStatus("Bad payload: " + e.message, true); return; }
        if (!data || data.error) {
            showStatus(data && data.error ? data.error : "No price data", false);
            return;
        }
        try {
            renderCost(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[electricity_cost.js] render threw:", e);
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

function renderCost(data) {
    const traces = [];
    const shapes = [];
    const annotations = [];

    // ── Subplot (a) — contour plot ──
    if (data.cost_matrix && data.cost_matrix.length) {
        traces.push({
            type: "contour",
            z: data.cost_matrix,
            x: data.year_labels,
            y: data.day_indices,
            colorscale: resolveColorscale(data.colormap || "Turbo"),
            zmin: data.price_min,
            zmax: data.price_max,
            contours: {
                coloring: "fill",
                showlines: true,
                start: data.price_min,
                end: data.price_max,
                size: (data.price_max - data.price_min) / 15,
            },
            line: { color: "rgba(0,0,0,0.35)", width: 0.4 },
            colorbar: {
                title: { text: "$/MWh", side: "right" },
                thickness: 12, len: 0.40,
                x: 0.96, xanchor: "left",
                y: 0.78, yanchor: "middle",
            },
            xaxis: "x", yaxis: "y",
            hovertemplate:
                "%{x} – Day %{y}<br>Price: %{z:.2f} $/MWh<extra></extra>",
            showlegend: false,
        });
    }

    // ── Annual average price line over the contour (secondary y axis) ──
    if (data.annual_avg_prices && data.annual_avg_prices.length) {
        // Halo (wide light grey behind) + crisp white line for contrast.
        const xs = data.year_labels;
        const ys = data.annual_avg_prices;
        traces.push({
            type: "scatter", mode: "lines",
            x: xs, y: ys,
            line: { color: "rgba(0,0,0,0.35)", width: 7 },
            hoverinfo: "skip",
            showlegend: false,
            xaxis: "x", yaxis: "y2",
        });
        traces.push({
            type: "scatter", mode: "lines+markers",
            x: xs, y: ys,
            line: { color: "#FFFFFF", width: 3 },
            marker: {
                color: "#FFFFFF", size: 8,
                line: { color: "#000000", width: 1.5 },
            },
            name: "Annual Avg Price",
            hovertemplate: "%{x}: %{y:.2f} $/MWh<extra>Annual Avg</extra>",
            xaxis: "x", yaxis: "y2",
        });
    }

    // ── Subplot (b) — generation-weighted histogram ──
    const hasNR = data.nonren_prices && data.nonren_prices.length > 5;
    const hasRE = data.ren_prices && data.ren_prices.length > 5;
    if (hasNR) {
        traces.push({
            type: "histogram",
            x: data.nonren_prices,
            y: data.nonren_weights_gwh,
            histfunc: "sum",
            xbins: { start: data.hist_lo, end: data.hist_hi,
                     size: (data.hist_hi - data.hist_lo) / 100 },
            marker: { color: "#D62728" },
            opacity: 0.55,
            name: "Non-Renewable",
            xaxis: "x3", yaxis: "y3",
            hovertemplate: "%{x:.1f} $/MWh<br>%{y:,.1f} GWh<extra>Non-Ren.</extra>",
        });
    }
    if (hasRE) {
        traces.push({
            type: "histogram",
            x: data.ren_prices,
            y: data.ren_weights_gwh,
            histfunc: "sum",
            xbins: { start: data.hist_lo, end: data.hist_hi,
                     size: (data.hist_hi - data.hist_lo) / 100 },
            marker: { color: "#2CA02C" },
            opacity: 0.55,
            name: "Renewable",
            xaxis: "x3", yaxis: "y3",
            hovertemplate: "%{x:.1f} $/MWh<br>%{y:,.1f} GWh<extra>Renewable</extra>",
        });
    }
    // Vertical dashed lines for the weighted means.
    if (data.mean_nonren !== null && data.mean_nonren !== undefined) {
        shapes.push({
            type: "line", xref: "x3", yref: "y3 domain",
            x0: data.mean_nonren, x1: data.mean_nonren, y0: 0, y1: 1,
            line: { color: "#D62728", width: 2, dash: "dash" },
        });
        annotations.push({
            xref: "x3", yref: "y3 domain",
            x: data.mean_nonren, y: 0.95, xanchor: "left", yanchor: "top",
            text: `<b>Avg Non-Ren: $${data.mean_nonren.toFixed(1)}/MWh</b>`,
            showarrow: false, font: { color: "#D62728", size: 10 },
            bgcolor: "rgba(255,255,255,0.75)",
        });
    }
    if (data.mean_ren !== null && data.mean_ren !== undefined) {
        shapes.push({
            type: "line", xref: "x3", yref: "y3 domain",
            x0: data.mean_ren, x1: data.mean_ren, y0: 0, y1: 1,
            line: { color: "#2CA02C", width: 2, dash: "dash" },
        });
        annotations.push({
            xref: "x3", yref: "y3 domain",
            x: data.mean_ren, y: 0.85, xanchor: "left", yanchor: "top",
            text: `<b>Avg Ren: $${data.mean_ren.toFixed(1)}/MWh</b>`,
            showarrow: false, font: { color: "#2CA02C", size: 10 },
            bgcolor: "rgba(255,255,255,0.75)",
        });
    }

    // Month-anchored y ticks for the contour (Jan at bottom, Dec at top
    // — same convention as the matplotlib chart's origin="lower").
    const monthLabels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const monthTickvals = monthLabels.map((_, m) => (m + 0.5) * (365 / 12));

    const layout = {
        margin: { t: 60, r: 100, b: 80, l: 70 },
        showlegend: true,
        annotations: annotations.concat([
            { text: "<b>a) Daily Electricity Price Evolution</b>",
              x: 0.46, xref: "paper", xanchor: "center",
              y: 1.00, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) Generation-Weighted Price Distribution</b>",
              x: 0.46, xref: "paper", xanchor: "center",
              y: 0.46, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ]),
        shapes: shapes,
        legend: {
            orientation: "h",
            x: 0.46, xanchor: "center",
            y: -0.12, yanchor: "top",
            font: { size: 10 },
            tracegroupgap: 12,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        barmode: "overlay",
        // Subplot a — contour
        xaxis: {
            domain: [0, 0.93], anchor: "y",
            type: "category",
            title: "<b>Year</b>",
            tickangle: -45,
        },
        yaxis: {
            domain: [0.55, 0.98], anchor: "x",
            tickvals: monthTickvals, ticktext: monthLabels,
            title: "<b>Month</b>",
            // Pin the range exactly to the data extents so Plotly's
            // default ~5% axis padding doesn't add empty bands above
            // and below the heatmap.
            range: [0, 364],
            autorange: false,
        },
        yaxis2: {
            domain: [0.55, 0.98], anchor: "x",
            overlaying: "y", side: "right",
            title: { text: "<b>Annual Avg ($/MWh)</b>",
                     font: { size: 10 } },
            showgrid: false, tickfont: { size: 9 },
        },
        // Subplot b — histogram
        xaxis3: {
            domain: [0, 0.93], anchor: "y3",
            title: "<b>Price ($/MWh)</b>",
            range: [data.hist_lo, data.hist_hi],
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis3: {
            domain: [0.0, 0.45], anchor: "x3",
            title: "<b>Generation (GWh)</b>",
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

// Cheap colormap swap for the contour (trace 0). Called from Python.
function setColormap(name) {
    const plotEl = document.getElementById("plot");
    if (plotEl && plotEl.data && plotEl.data.length > 0) {
        Plotly.restyle("plot",
            { colorscale: [resolveColorscale(name)] }, [0]);
    }
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
