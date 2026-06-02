/* MGA — Clustered Heatmap of Alternative Similarity.
 *
 * Top subplot: average-linkage dendrogram (one U-shape per merge).
 * Bottom subplot: Euclidean distance matrix between alternatives,
 *                 reordered by the dendrogram's leaf order.
 *
 * The two share the X axis ordering so each column of the heatmap
 * lines up under its leaf in the dendrogram. White guide lines on
 * the heatmap mark cluster boundaries. The colormap is hot-swappable
 * via setColormap() (called from Python when the user picks a new
 * one in the params bar). */
"use strict";

let bridge = null;
let currentData = null;
let currentCmap = "Viridis";

document.addEventListener("DOMContentLoaded", () => {
    new QWebChannel(qt.webChannelTransport, channel => {
        bridge = channel.objects.loader; refresh();
    });
});

function refresh() {
    if (!bridge) return;
    bridge.get_data(raw => {
        let data; try { data = JSON.parse(raw); }
        catch (e) { showStatus("Bad payload: " + e.message, true); return; }
        if (!data || data.error) {
            showStatus(data && data.error ? data.error : "No data", false); return;
        }
        try {
            currentData = data;
            currentCmap = data.colormap || "Viridis";
            render(currentData, currentCmap);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true); console.error(e);
        }
    });
}
function showStatus(text, isError) {
    const s = document.getElementById("status");
    s.textContent = text; s.style.display = "block";
    s.style.color = isError ? "#c0392b" : "#7F8C8D";
    const p = document.getElementById("plot"); if (p) p.style.display = "none";
}
function hideStatus() {
    const s = document.getElementById("status"); if (s) s.style.display = "none";
    const p = document.getElementById("plot"); if (p) p.style.display = "";
}

// Plotly.js ships only a subset of named colorscales. Turbo / Magma /
// Inferno / Plasma are matplotlib's perceptually-uniform maps that the
// Python plotly module knows how to expand for the browser, but the
// raw JS library does not. If we pass the bare name, Plotly silently
// falls back to its default (Viridis-ish), so the displayed colours
// don't match the combo box. The shim below mirrors what Spatial
// Divergence does: hand-rolled colour stops for the maps that aren't
// in plotly.js, pass-through for the rest.
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
};
function resolveColorscale(name) {
    return NAMED_COLORSCALES[name] || name;
}

function setColormap(name) {
    if (!currentData) return;
    currentCmap = name;
    // Only the heatmap trace depends on the colormap, so we restyle
    // the existing plot in place instead of rebuilding it (much faster
    // than a full Plotly.newPlot for large matrices).
    Plotly.restyle("plot", {
        colorscale: [resolveColorscale(name)], reversescale: [false],
    }, [findHeatmapTraceIdx()]);
}

function findHeatmapTraceIdx() {
    const plot = document.getElementById("plot");
    if (!plot || !plot.data) return 0;
    for (let i = 0; i < plot.data.length; i++) {
        if (plot.data[i].type === "heatmap") return i;
    }
    return 0;
}

function render(data, cmap) {
    const s = data.similarity;
    if (!s || !s.distance || s.distance.length < 2) {
        showStatus("Need at least two alternatives to compute similarity.", false);
        return;
    }
    const n = s.alt_ids.length;
    const order = (s.order && s.order.length === n)
        ? s.order : s.alt_ids.map((_, i) => i);
    // Reorder the matrix and labels by the dendrogram's leaf order.
    const z = order.map(i => order.map(j => s.distance[i][j]));
    const labels = order.map(i =>
        (s.is_optimal[i] ? "★ " : "") + "Alt " + s.alt_ids[i]);
    const idx = labels.map((_, i) => i);          // numeric col/row index
    const clustersReordered = order.map(i => s.clusters[i]);

    // Cluster boundary guides on the heatmap.
    const shapes = [];
    for (let k = 1; k < n; k++) {
        if (clustersReordered[k] !== clustersReordered[k - 1]) {
            const c = k - 0.5;
            shapes.push({
                type: "line", xref: "x", yref: "y",
                x0: c, x1: c, y0: -0.5, y1: n - 0.5,
                line: { color: "#FFF", width: 2 },
            });
            shapes.push({
                type: "line", xref: "x", yref: "y",
                x0: -0.5, x1: n - 0.5, y0: c, y1: c,
                line: { color: "#FFF", width: 2 },
            });
        }
    }

    // ── Top: dendrogram ──
    // One scatter trace per merge step. The x-coords are already in
    // column-index space (Python computed them from leaves_list), so
    // they line up exactly with the heatmap columns below.
    const dendroTraces = [];
    for (const link of (s.dendro_links || [])) {
        dendroTraces.push({
            type: "scatter", mode: "lines",
            x: link.x, y: link.y,
            xaxis: "x", yaxis: "y2",
            line: { color: "#34495E", width: 1.4, shape: "linear" },
            hoverinfo: "skip", showlegend: false,
        });
    }

    // ── Bottom: heatmap ──
    // Use numeric x/y indices so the axes stay linear (matching the
    // dendrogram's numeric x-coords); the string labels live on the
    // axis ticks. Passing string arrays on a heatmap turns the axes
    // categorical and breaks the shared-x alignment with the dendro.
    const heatmap = {
        type: "heatmap",
        z: z, x: idx, y: idx,
        xaxis: "x", yaxis: "y",
        colorscale: resolveColorscale(cmap), reversescale: false,
        colorbar: { title: { text: "Distance", side: "right" },
                    thickness: 12, len: 0.65, y: 0.32, yanchor: "middle" },
        // 2-D customdata so the hover shows the readable row + column
        // labels instead of the numeric indices the axes now use.
        customdata: idx.map(i => idx.map(j => [labels[i], labels[j]])),
        hovertemplate:
            "<b>%{customdata[0]} ↔ %{customdata[1]}</b><br>" +
            "distance = %{z:,.1f}<extra></extra>",
    };

    const traces = [...dendroTraces, heatmap];

    // Stack the two panels vertically with shared x.
    // y  (heatmap) takes the lower 65%, y2 (dendro) the top ~22%.
    const layout = {
        margin: { l: 90, r: 30, t: 96, b: 90 },
        title: { text: "<b>Clustered Heatmap — Alternative Similarity</b><br>" +
                       "<span style='font-size:11px;color:#7f8c8d'>Euclidean distance in decision space · rows / columns reordered by the dendrogram above</span>",
                 x: 0.5, xanchor: "center", y: 0.98, yanchor: "top",
                 font: { size: 13 } },
        xaxis:  { domain: [0.0, 1.0],
                  tickmode: "array",
                  tickvals: idx, ticktext: labels,
                  tickangle: -40, automargin: true,
                  showgrid: false, zeroline: false,
                  range: [-0.5, n - 0.5] },
        yaxis:  { domain: [0.0, 0.66], anchor: "x",
                  autorange: "reversed", automargin: true,
                  tickmode: "array",
                  tickvals: idx, ticktext: labels,
                  range: [-0.5, n - 0.5] },
        yaxis2: { domain: [0.72, 0.94], anchor: "x",
                  range: [0, s.dendro_max_height || 1],
                  visible: false, showgrid: false, zeroline: false,
                  showticklabels: false, fixedrange: true },
        shapes: shapes,
        showlegend: false,
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout,
        { responsive: true, displaylogo: false });
}

window.addEventListener("resize", () => {
    const el = document.getElementById("plot");
    if (el && el.data) Plotly.Plots.resize(el);
});
