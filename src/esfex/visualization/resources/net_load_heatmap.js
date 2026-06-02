/* Net Load Heatmap — interactive Plotly replica of the matplotlib
 * NetLoadHeatmapChart. Two side-by-side 12-month × hour-of-day
 * heatmaps:
 *   (a) Avg Net Load (MW)        — default colormap: Jet
 *   (b) Avg Net Load Ramp (MW/h) — default: RdBu (reversed so red=ramp up)
 *
 * Payload contract (see NetLoadHeatmapChart._build_payload):
 *   { year, months, hours, avg_nl_mw, avg_ramp_mw_h, sigma,
 *     colormap_a, colormap_b, reverse_b }
 */
"use strict";


function _ucClearPlot() {
    try { Plotly.purge("plot"); } catch (e) {}
}
// Same name → palette map as battery_heatmap.js. Plotly heatmap accepts
// classic named colorscales as strings, but the modern ones (Turbo,
// Plasma, Magma, Inferno) live elsewhere in the JS module and silently
// fall back to the default unless we pass the explicit pair array.
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
        catch (e) { _ucClearPlot(); showStatus("Bad payload: " + e.message, true); return; }
        if (!data || data.error) {
            _ucClearPlot(); showStatus(data && data.error ? data.error : "No data", false);
            return;
        }
        if (!data.months || !data.months.length
            || !data.avg_nl_mw || !data.avg_nl_mw.length) {
            _ucClearPlot(); showStatus("No data", false);
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

function renderHeatmap(data) {
    const traceA = {
        type: "heatmap",
        z: data.avg_nl_mw,
        x: data.hours, y: data.months,
        xaxis: "x", yaxis: "y",
        colorscale: resolveColorscale(data.colormap_a || "Jet"),
        colorbar: {
            title: { text: "MW", side: "right" },
            thickness: 12, len: 0.85,
            x: 0.42, xanchor: "left",
        },
        hovertemplate:
            "<b>%{y} %{x}</b><br>Net Load: %{z:,.1f} MW<extra></extra>",
    };
    const traceB = {
        type: "heatmap",
        z: data.avg_ramp_mw_h,
        x: data.hours, y: data.months,
        xaxis: "x2", yaxis: "y2",
        colorscale: resolveColorscale(data.colormap_b || "RdBu"),
        reversescale: !!data.reverse_b,
        colorbar: {
            title: { text: "MW/h", side: "right" },
            thickness: 12, len: 0.85,
            x: 1.0, xanchor: "left",
        },
        hovertemplate:
            "<b>%{y} %{x}</b><br>Ramp: %{z:,.1f} MW/h<extra></extra>",
    };

    // Y axis label switches with mode: UC packs rows by day-of-horizon,
    // planning packs rows by month-of-year.
    const yTitle = (data.mode === "uc") ? "<b>Day</b>" : "<b>Month</b>";
    const title = (data.mode === "uc")
        ? "Net Load Heatmap — UC Horizon (Year " + data.year + ")"
        : "Net Load Heatmap — Year " + data.year;
    const layout = {
        margin: { t: 80, r: 30, b: 70, l: 70 },
        showlegend: false,
        annotations: [
            { text: "<b>" + title + "</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.12, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 16 } },
            { text: "<b>a) Avg Net Load (MW)</b>",
              x: 0.20, xref: "paper", xanchor: "center",
              y: 1.02, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) Avg Net Load Ramp (MW/h)</b>",
              x: 0.75, xref: "paper", xanchor: "center",
              y: 1.02, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ],
        xaxis: {
            domain: [0, 0.40], anchor: "y", type: "category",
            tickangle: -45, title: "<b>Hour of Day</b>",
        },
        yaxis: {
            anchor: "x", type: "category",
            title: yTitle,
        },
        xaxis2: {
            domain: [0.55, 0.95], anchor: "y2", type: "category",
            tickangle: -45, title: "<b>Hour of Day</b>",
        },
        yaxis2: {
            anchor: "x2", type: "category",
            title: yTitle,
        },
    };

    Plotly.purge("plot");
    Plotly.newPlot("plot", [traceA, traceB], layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

// Cheap colormap swap, one trace at a time. ``which`` is 0 (Net Load,
// subplot a) or 1 (Ramp, subplot b). When the user picks a new
// colormap we also drop the default reversescale so the choice is
// honoured literally.
function setColormap(which, name) {
    const plotEl = document.getElementById("plot");
    if (plotEl && plotEl.data && plotEl.data.length > which) {
        Plotly.restyle("plot",
            { colorscale: [resolveColorscale(name)], reversescale: [false] },
            [which]);
    }
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
