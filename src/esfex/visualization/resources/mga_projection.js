/* MGA — 2D Projection (PCA / t-SNE).
 *
 * Each alternative is a point in a high-dim decision space; we project
 * to 2D so the user can spot clusters/families of similar solutions.
 * The Python "Method" combo calls setMethod(name) to switch projections
 * without a payload re-fetch — the data was already shipped. */
"use strict";

let bridge = null;
let currentData = null;
let currentMethod = "pca";

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
            currentMethod = data.method || "pca";
            render(currentData, currentMethod);
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

// Called from Python when the user changes the method combo.
function setMethod(method) {
    if (!currentData) return;
    currentMethod = method;
    render(currentData, method);
}

function render(data, method) {
    const proj = data.projections;
    if (!proj) { showStatus("No projection data.", false); return; }
    const block = (method === "tsne") ? proj.tsne : proj.pca;
    if (!block) {
        showStatus(method === "tsne"
            ? "t-SNE unavailable (install scikit-learn)."
            : "Projection unavailable.", false);
        return;
    }
    const ids = proj.alt_ids;
    const isOpt = proj.is_optimal;
    const cost = proj.cost_busd;
    const costPct = proj.cost_pct_above_optimal;
    const div = proj.diversity;
    const reP = proj.re_peak_pct;
    const objective = proj.objective || ids.map(() => "hsj_diversity");
    const h = data.header || {};
    const isSpores = (h.method || "mga").toLowerCase() === "spores";
    const colorMap = h.objective_colors || {};
    const labelMap = h.objective_labels || {};

    // Hover text per point.
    const text = ids.map((id, i) => (
        `<b>Alt ${id}${isOpt[i] ? " (Optimal)" : ""}</b><br>` +
        `Cost: $${cost[i].toFixed(2)}B (+${costPct[i].toFixed(2)}% vs opt)<br>` +
        `Diversity: ${div[i].toFixed(1)}<br>` +
        (isSpores
            ? `Objective: ${labelMap[objective[i]] || objective[i]}<br>`
            : "") +
        `Peak RE: ${reP[i].toFixed(1)}%`
    ));

    const idxOpt  = ids.map((_, i) => i).filter(i =>  isOpt[i]);
    const traces = [];

    if (isSpores) {
        // ── SPORES: one trace per distinct objective ──
        // Each objective renders as a categorical group sharing the
        // colour the bundle prepared (header.objective_colors). No
        // colourbar — the legend carries the encoding instead.
        const groups = new Map();
        for (const o of (h.objectives || [])) groups.set(o, []);
        ids.forEach((_, i) => {
            if (isOpt[i]) return;
            const o = objective[i] || "hsj_diversity";
            if (!groups.has(o)) groups.set(o, []);
            groups.get(o).push(i);
        });
        for (const [obj, idxs] of groups.entries()) {
            if (idxs.length === 0) continue;
            traces.push({
                type: "scatter", mode: "markers+text",
                x: idxs.map(i => block.x[i]),
                y: idxs.map(i => block.y[i]),
                text: idxs.map(i => String(ids[i])),
                textposition: "top center",
                textfont: { size: 10, color: "#7f8c8d" },
                name: labelMap[obj] || obj,
                marker: {
                    size: 14, color: colorMap[obj] || "#7f8c8d",
                    line: { color: "#FFF", width: 1 },
                },
                hovertext: idxs.map(i => text[i]), hoverinfo: "text",
            });
        }
    } else {
        // ── MGA: classical cost-coloured scatter (Viridis ramp) ──
        const idxNorm = ids.map((_, i) => i).filter(i => !isOpt[i]);
        traces.push({
            type: "scatter", mode: "markers+text",
            x: idxNorm.map(i => block.x[i]),
            y: idxNorm.map(i => block.y[i]),
            text: idxNorm.map(i => String(ids[i])),
            textposition: "top center",
            textfont: { size: 10, color: "#7f8c8d" },
            name: "Alternative",
            marker: {
                size: 14,
                color: idxNorm.map(i => cost[i]),
                colorscale: "Viridis", showscale: true,
                colorbar: { title: { text: "Cost ($B)", side: "right" },
                            thickness: 12, len: 0.85 },
                line: { color: "#FFF", width: 1 },
            },
            hovertext: idxNorm.map(i => text[i]),
            hoverinfo: "text",
        });
    }

    // Optimal star — coloured the same way regardless of method.
    traces.push({
        type: "scatter", mode: "markers+text",
        x: idxOpt.map(i => block.x[i]),
        y: idxOpt.map(i => block.y[i]),
        text: idxOpt.map(i => "★ " + ids[i]),
        textposition: "top center",
        textfont: { size: 11, color: colorMap["cost_optimal"] || "#E74C3C",
                    weight: 700 },
        name: "Optimal",
        marker: { symbol: "star", size: 22,
                  color: colorMap["cost_optimal"] || "#E74C3C",
                  line: { color: "#FFF", width: 1.5 } },
        hovertext: idxOpt.map(i => text[i]),
        hoverinfo: "text",
    });

    const isP = (method !== "tsne");
    const xTitle = isP ? `PC1  (${(proj.pca.var_pc1*100).toFixed(1)}% var)`
                       : "t-SNE dim 1";
    const yTitle = isP ? `PC2  (${(proj.pca.var_pc2*100).toFixed(1)}% var)`
                       : "t-SNE dim 2";
    const methodLabel = isP ? "Principal Component Analysis"
                            : "t-distributed Stochastic Neighbour Embedding";
    const layout = {
        margin: { l: 70, r: 30, t: 96, b: 80 },
        title: { text: `<b>2D Projection of the decision space</b><br>` +
                       `<span style='font-size:11px;color:#7f8c8d'>${methodLabel} — each alternative as a point in technology × node investment space</span>`,
                 x: 0.5, xanchor: "center", y: 0.98, yanchor: "top",
                 font: { size: 13 } },
        xaxis: { title: xTitle, gridcolor: "rgba(0,0,0,0.08)",
                 zeroline: true, zerolinecolor: "rgba(0,0,0,0.15)" },
        yaxis: { title: yTitle, gridcolor: "rgba(0,0,0,0.08)",
                 zeroline: true, zerolinecolor: "rgba(0,0,0,0.15)" },
        showlegend: true,
        legend: { orientation: "h", y: -0.16, x: 0.5, xanchor: "center",
                  yanchor: "top", font: { size: 10 } },
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout,
        { responsive: true, displaylogo: false });
}

window.addEventListener("resize", () => {
    const el = document.getElementById("plot");
    if (el && el.data) Plotly.Plots.resize(el);
});
