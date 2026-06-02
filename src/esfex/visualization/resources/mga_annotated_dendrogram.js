/* MGA — Annotated Circular Dendrogram.
 *
 * Inside-out:
 *   1. The average-linkage tree drawn radially (root in centre, leaves
 *      on the rim). Branches are concentric arc segments connected by
 *      radial spokes so the geometry stays planar.
 *   2. A composition ring: each leaf carries a stacked categorical bar
 *      (Solar / Wind / Storage / …) whose radial extent ∝ MW share of
 *      that category in that alternative.
 *   3. A peak-RE ring (green ramp) and a cost-premium ring (red ramp).
 *   4. Outermost: ★Alt N labels, rotated so they read outward; the
 *      labels are also coloured by cluster id.
 *
 * Python pre-computes every (x, y) so this file does no trigonometry —
 * it just shovels traces into Plotly. */
"use strict";

let bridge = null;

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
        try { render(data); hideStatus(); }
        catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
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

const CLUSTER_COLORS = [
    "#2980B9", "#27AE60", "#E67E22", "#9B59B6",
    "#16A085", "#C0392B", "#34495E", "#7F8C8D",
];

function render(data) {
    const d = data.annotated_dendrogram;
    if (!d || !d.tree_links || d.tree_links.length === 0) {
        showStatus("Need at least two alternatives for a dendrogram.", false);
        return;
    }
    const traces = [];

    // ── Tree branches ──
    for (const link of d.tree_links) {
        traces.push({
            type: "scatter", mode: "lines",
            x: link.x, y: link.y,
            line: { color: "#34495E", width: 1.3, shape: "linear" },
            hoverinfo: "skip", showlegend: false,
        });
    }

    // ── Composition + scalar ring polygons ──
    // One trace per polygon so each carries its own fill colour and
    // hover text. The volume is small (n_alts × (n_categories + 2))
    // so the JS stays cheap.
    for (const seg of d.ring_segments) {
        traces.push({
            type: "scatter", mode: "lines",
            x: seg.x, y: seg.y,
            fill: "toself",
            fillcolor: seg.color,
            line: { color: "rgba(0,0,0,0.08)", width: 0.5 },
            hoverinfo: "text", hovertext: seg.hover,
            showlegend: false,
        });
    }

    // ── Leaf labels ──
    for (const lab of d.leaf_labels) {
        // Plotly text rotation is in degrees, positive = counter-clockwise.
        // To keep the text reading outward, flip it on the left half
        // of the circle so it never appears upside-down.
        let ang = lab.angle_deg;
        let textPos = "middle right";
        if (ang > 90 || ang < -90) { ang += 180; textPos = "middle left"; }
        const cluster = lab.cluster || 1;
        const color = CLUSTER_COLORS[(cluster - 1) % CLUSTER_COLORS.length];
        traces.push({
            type: "scatter", mode: "text",
            x: [lab.x], y: [lab.y],
            text: [(lab.is_optimal ? "★ " : "") + lab.text],
            textposition: textPos,
            textangle: ang,
            textfont: {
                size: 11,
                color: lab.is_optimal ? "#E74C3C" : color,
                family: "system-ui, sans-serif",
            },
            hoverinfo: "skip",
            showlegend: false,
        });
    }

    // ── Legend stubs ──
    // Category colours, plus entries describing the scalar / categorical
    // rings. Stub traces have null coordinates so they contribute only
    // to the legend, not to the plot area.
    for (const cat of d.categories) {
        traces.push({
            type: "scatter", mode: "markers",
            x: [null], y: [null],
            marker: { size: 12, color: d.category_colors[cat],
                      symbol: "square" },
            name: cat,
            showlegend: true, hoverinfo: "skip",
        });
    }
    // Ring 2 swaps semantics by method (mirrors the Python build).
    // For SPORES we emit one legend entry per declared objective so the
    // viewer can match colour → objective at a glance; for MGA we keep
    // the single "peak RE" ramp.
    const h = data.header || {};
    const isSpores = (h.method || "mga").toLowerCase() === "spores"
                    && d.track1_kind === "objective";
    if (isSpores) {
        const colorMap = h.objective_colors || {};
        const labelMap = h.objective_labels || {};
        for (const obj of (h.objectives || [])) {
            traces.push({
                type: "scatter", mode: "markers",
                x: [null], y: [null],
                marker: { size: 12, color: colorMap[obj] || "#7f8c8d",
                          symbol: "square" },
                name: (labelMap[obj] || obj) + " (ring 2)",
                showlegend: true, hoverinfo: "skip",
            });
        }
    } else {
        traces.push({
            type: "scatter", mode: "markers",
            x: [null], y: [null],
            marker: { size: 12, color: "rgba(39,174,96,0.85)", symbol: "square" },
            name: "Peak RE share (ring 2)", showlegend: true, hoverinfo: "skip",
        });
    }
    traces.push({
        type: "scatter", mode: "markers",
        x: [null], y: [null],
        marker: { size: 12, color: "rgba(192,57,43,0.85)", symbol: "square" },
        name: "Cost premium (ring 3)", showlegend: true, hoverinfo: "skip",
    });

    // Track guide rings (very faint) so the user perceives the three
    // concentric tracks even when an alternative is empty in some
    // categories.
    const R = d.ring_radii || [];
    const guideRadii = [R[0], R[1], R[2], R[3], R[4], R[5]].filter(
        r => typeof r === "number");
    const NSTEP = 220;
    for (const r of guideRadii) {
        const xs = [], ys = [];
        for (let i = 0; i <= NSTEP; i++) {
            const t = 2 * Math.PI * i / NSTEP;
            xs.push(r * Math.cos(t)); ys.push(r * Math.sin(t));
        }
        traces.push({
            type: "scatter", mode: "lines",
            x: xs, y: ys,
            line: { color: "rgba(0,0,0,0.07)", width: 0.5 },
            hoverinfo: "skip", showlegend: false,
        });
    }

    // The plot is locked to a 1:1 aspect ratio centred on the origin
    // so the polar geometry never warps when the user resizes the
    // window.
    const lim = (R[R.length - 1] || 1.8) + 0.2;
    const layout = {
        margin: { l: 8, r: 8, t: 96, b: 24 },
        title: { text: "<b>Annotated Circular Dendrogram</b><br>" +
                       "<span style='font-size:11px;color:#7f8c8d'>tree = average-linkage clustering · rings = composition · peak RE · cost premium</span>",
                 x: 0.5, xanchor: "center", y: 0.98, yanchor: "top",
                 font: { size: 13 } },
        xaxis: {
            visible: false, range: [-lim, lim], fixedrange: false,
            scaleanchor: "y", scaleratio: 1,
        },
        yaxis: { visible: false, range: [-lim, lim], fixedrange: false },
        showlegend: true,
        legend: {
            orientation: "h",
            y: -0.04, x: 0.5, xanchor: "center", yanchor: "top",
            font: { size: 10 }, itemwidth: 70,
        },
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout,
        { responsive: true, displaylogo: false,
          modeBarButtonsToRemove: ["lasso2d", "select2d"] });
}

window.addEventListener("resize", () => {
    const el = document.getElementById("plot");
    if (el && el.data) Plotly.Plots.resize(el);
});
