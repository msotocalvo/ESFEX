/* MGA — Decision Factors (Alt × Tech bi-clustered heatmap).
 *
 *  ┌──────────────┐
 *  │ top dendro   │   ← technology clustering (correlation distance)
 *  ├─┬────────────┤
 *  │l│            │
 *  │e│  heatmap   │   ← MW invested per (alt, tech)
 *  │f│            │
 *  │t│            │
 *  └─┴────────────┘
 *  ↑ alt clustering (Euclidean distance in decision space)
 *
 * Python pre-computes every U-shape so the JS only hands them to
 * scatter line traces. Colormap is hot-swappable via setColormap()
 * with the same Plotly.js shim Spatial / Similarity use for the
 * Turbo / Plasma / Magma / Inferno names that aren't bundled with
 * the bare plotly.min.js. */
"use strict";

let bridge = null;
let currentData = null;
let currentCmap = "Viridis";
let currentGranularity = "tech";

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
            currentGranularity = data.granularity || "tech";
            render(currentData, currentCmap, currentGranularity);
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
    Plotly.restyle("plot", {
        colorscale: [resolveColorscale(name)], reversescale: [false],
    }, [findHeatmapTraceIdx()]);
}

// Granularity swaps which view of the column space the heatmap shows.
// We re-render rather than restyle: the column count, dendrogram
// segments, axis ticks and category strip all change together, and
// rebuilding the layout is faster than reconciling restyle deltas
// across that many traces.
function setGranularity(name) {
    if (!currentData) return;
    currentGranularity = name;
    render(currentData, currentCmap, currentGranularity);
}

function findHeatmapTraceIdx() {
    const plot = document.getElementById("plot");
    if (!plot || !plot.data) return 0;
    for (let i = 0; i < plot.data.length; i++) {
        if (plot.data[i].type === "heatmap") return i;
    }
    return 0;
}

function render(data, cmap, granularity) {
    const df = data.decision_factors;
    if (!df || !df.views) {
        showStatus("No decision factors to display.", false);
        return;
    }
    const view = df.views[granularity] || df.views.tech;
    if (!view || !view.matrix || view.matrix.length === 0
        || view.tech_labels.length === 0) {
        showStatus("This view has no factor columns above the 1 MW threshold.", false);
        return;
    }
    const nRows = df.alt_labels.length;
    const nCols = view.tech_labels.length;
    const rowIdx = df.alt_labels.map((_, i) => i);
    const colIdx = view.tech_labels.map((_, i) => i);
    // Tech × Node packs ~3× more columns than the Tech view, so make
    // the column tick labels smaller and the bottom margin taller so
    // they all fit without overlap.
    const dense = nCols > 25;
    const colTickFont = dense ? 9 : 11;
    const bottomMargin = dense ? 170 : 130;

    const traces = [];

    // ── Top dendrogram (factors) ──
    // Lives in the (x = col-index, y = linkage-distance) space and is
    // anchored on the heatmap's shared X axis (xaxis="x"). yaxis="y2".
    for (const link of (view.tech_dendro_links || [])) {
        traces.push({
            type: "scatter", mode: "lines",
            x: link.x, y: link.y,
            xaxis: "x", yaxis: "y2",
            line: { color: "#34495E", width: 1.2 },
            hoverinfo: "skip", showlegend: false,
        });
    }
    // ── Left dendrogram (alts) ──
    // (x = linkage-distance, y = row-index) → anchored on xaxis="x2",
    // shared yaxis="y" with the heatmap. The xaxis2 range is reversed
    // so distance grows leftwards from the heatmap.
    for (const link of (df.alt_dendro_links || [])) {
        traces.push({
            type: "scatter", mode: "lines",
            x: link.x, y: link.y,
            xaxis: "x2", yaxis: "y",
            line: { color: "#34495E", width: 1.2 },
            hoverinfo: "skip", showlegend: false,
        });
    }

    // ── Heatmap (alt × factor) ──
    const heatmap = {
        type: "heatmap",
        z: view.matrix,            // rows already in display order
        x: colIdx, y: rowIdx,
        xaxis: "x", yaxis: "y",
        colorscale: resolveColorscale(cmap), reversescale: false,
        colorbar: { title: { text: "MW", side: "right" },
                    thickness: 12, len: 0.6, y: 0.40, yanchor: "middle" },
        customdata: rowIdx.map(i => colIdx.map(j =>
            [df.alt_labels[i], view.tech_labels[j], view.tech_categories[j]])),
        hovertemplate:
            "<b>%{customdata[0]} · %{customdata[1]}</b><br>" +
            "category: %{customdata[2]}<br>" +
            "%{z:,.0f} MW<extra></extra>",
    };
    traces.push(heatmap);

    // ── Category colour strip (above the heatmap, below the dendro) ──
    // Each factor column gets a coloured rectangle showing its
    // category, drawn as layout.shape rectangles on yaxis="y3".
    const stripShapes = [];
    for (let j = 0; j < nCols; j++) {
        stripShapes.push({
            type: "rect", xref: "x", yref: "y3",
            x0: j - 0.5, x1: j + 0.5, y0: 0, y1: 1,
            fillcolor: view.tech_colors[j],
            line: { color: "rgba(255,255,255,0.6)", width: 0.5 },
        });
    }

    const granLabel = granularity === "tech_node"
        ? "technology × node placement"
        : "technology (aggregated across nodes)";
    const layout = {
        margin: { l: 20, r: 30, t: 96, b: bottomMargin },
        title: { text: "<b>Decision Factors — Alternatives × " +
                       (granularity === "tech_node" ? "Tech × Node"
                                                    : "Technologies") +
                       "</b><br>" +
                       "<span style='font-size:11px;color:#7f8c8d'>rows clustered by Euclidean distance · columns clustered by co-investment correlation · cell colour = MW invested · " +
                       granLabel + "</span>",
                 x: 0.5, xanchor: "center", y: 0.98, yanchor: "top",
                 font: { size: 13 } },
        // X axes ─────────────────────────────────────────────────────
        // x  → factor-index space (heatmap columns + top dendrogram)
        // x2 → linkage-distance space for the left dendrogram, with
        //      the range reversed so 0 sits adjacent to the heatmap.
        xaxis: {
            domain: [0.16, 1.0],
            tickmode: "array", tickvals: colIdx, ticktext: view.tech_labels,
            tickangle: -45, automargin: true,
            tickfont: { size: colTickFont },
            showgrid: false, zeroline: false,
            range: [-0.5, nCols - 0.5],
        },
        xaxis2: {
            domain: [0.0, 0.14], anchor: "y",
            range: [df.alt_max_height || 1, 0],
            visible: false, showgrid: false, zeroline: false,
            showticklabels: false, fixedrange: true,
        },
        // Y axes ─────────────────────────────────────────────────────
        // y  → alt-index space (heatmap rows + left dendrogram)
        // y2 → top dendrogram (linkage distance, anchored on x)
        // y3 → category colour strip just above the heatmap
        yaxis: {
            domain: [0.0, 0.65], anchor: "x",
            tickmode: "array", tickvals: rowIdx, ticktext: df.alt_labels,
            automargin: true, autorange: "reversed",
            range: [nRows - 0.5, -0.5],
        },
        yaxis2: {
            domain: [0.71, 0.95], anchor: "x",
            range: [0, view.tech_max_height || 1],
            visible: false, showgrid: false, zeroline: false,
            showticklabels: false, fixedrange: true,
        },
        yaxis3: {
            domain: [0.66, 0.70], anchor: "x",
            range: [0, 1], showgrid: false, zeroline: false,
            showticklabels: false, fixedrange: true,
        },
        shapes: stripShapes,
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
