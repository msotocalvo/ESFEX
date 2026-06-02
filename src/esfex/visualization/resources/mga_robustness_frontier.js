/* MGA — Cost Frontier (top) + Decision Robustness (bottom).
 *
 * Top subplot answers "how much extra cost is being spent to escape
 * the optimum?" — every alternative as a point in (diversity, cost)
 * space, with the cost slack envelope shaded behind them.
 *
 * Bottom subplot answers "what decisions did that slack actually
 * shake?" — per-technology min↔max bars across alternatives, sorted
 * by CV. Tight bars at the bottom = must-build; wide bars at the top
 * = swappable.
 *
 * They share the same alternative pool — read top to bottom for the
 * full "what we paid → what we got" story. */
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

function render(data) {
    const alts = data.alternatives;
    const tr   = data.tech_range;
    const h    = data.header || {};
    if (!alts || alts.length === 0) {
        showStatus("No alternatives to display.", false); return;
    }
    if (!tr || !tr.labels || tr.labels.length === 0) {
        showStatus("No technology investments to compare.", false); return;
    }

    // ── Top: cost ↔ diversity scatter ──
    // When method === "spores" we group alternatives by their objective
    // tag and emit one trace per objective so the legend exposes the
    // SPORES menu directly. For method === "mga" everything collapses
    // to a single blue "Alternative" trace (the historical render).
    const isSpores = (h.method || "mga").toLowerCase() === "spores";
    const colorMap = (h.objective_colors || {});
    const labelMap = (h.objective_labels || {});
    const frontierTraces = [];

    // Group non-optimal alts by objective so we can emit categorical
    // legend entries. ``perObj`` preserves insertion order which mirrors
    // header.objectives (the display order Python chose).
    const perObj = new Map();
    for (const o of (h.objectives || [])) {
        if (!perObj.has(o)) perObj.set(o, []);
    }
    // X axis: average normalised L1 distance from the cost-optimal
    // plan, computed in Python for every alternative regardless of
    // method. Replaces the per-method ``diversity_objective`` value,
    // which is not comparable across SPORES objectives (HSJ score vs
    // Gini-min vs min total build live in different unit spaces).
    // Cost-optimal sits at x = 0 by construction.
    const xOf = a => (a.cost_optimal_distance == null
                      ? 0 : a.cost_optimal_distance);

    let xOpt = [], yOpt = [];
    for (const a of alts) {
        if (a.is_optimal) {
            xOpt.push(xOf(a));
            yOpt.push(a.cost_busd);
            continue;
        }
        const obj = a.objective || "hsj_diversity";
        if (!perObj.has(obj)) perObj.set(obj, []);
        perObj.get(obj).push(a);
    }
    for (const [obj, group] of perObj.entries()) {
        if (group.length === 0) continue;
        const xs = group.map(xOf);
        const ys = group.map(a => a.cost_busd);
        const texts = group.map(a =>
            `<b>Alt ${a.id}</b><br>` +
            `Cost: $${a.cost_busd.toFixed(2)}B ` +
            `(+${a.cost_pct_above_optimal.toFixed(2)}% vs opt)<br>` +
            `Distance from optimum: ${xOf(a).toFixed(3)}<br>` +
            (isSpores
                ? `Objective: ${labelMap[obj] || obj}<br>`
                : "") +
            `Peak RE: ${a.re_peak_pct.toFixed(1)}%`);
        frontierTraces.push({
            type: "scatter", mode: "markers+text",
            x: xs, y: ys,
            xaxis: "x", yaxis: "y",
            text: group.map(a => String(a.id)),
            textposition: "top center",
            textfont: { size: 10, color: "#7f8c8d" },
            marker: {
                size: 12,
                color: isSpores ? (colorMap[obj] || "#7f8c8d") : "#3498db",
                line: { color: "#FFF", width: 1 },
            },
            // Single combined trace when MGA so the legend stays clean;
            // one trace per objective when SPORES so each objective gets
            // its own legend entry.
            name: isSpores ? (labelMap[obj] || obj) : "Alternative",
            showlegend: true,
            hovertext: texts, hoverinfo: "text",
        });
    }
    // Optimal star — same colour regardless of method.
    if (xOpt.length > 0) {
        frontierTraces.push({
            type: "scatter", mode: "markers",
            x: xOpt, y: yOpt,
            xaxis: "x", yaxis: "y",
            marker: { symbol: "star", size: 20,
                      color: colorMap["cost_optimal"] || "#E74C3C",
                      line: { color: "#FFF", width: 1 } },
            name: "Optimal",
            hovertemplate: "<b>Optimal</b><br>$%{y:,.2f}B<extra></extra>",
        });
    }

    // ── Bottom: per-tech range bars ──
    const n = tr.labels.length;
    const rowY = tr.labels.map((_, i) => i);
    const rangeX = [], rangeY = [];
    for (let i = 0; i < n; i++) {
        rangeX.push(tr.min_mw[i], tr.max_mw[i], null);
        rangeY.push(i, i, null);
    }
    const robustnessTraces = [
        { type: "scatter", mode: "lines",
          x: rangeX, y: rangeY,
          xaxis: "x2", yaxis: "y2",
          line: { color: "#bdc3c7", width: 6 },
          hoverinfo: "skip", showlegend: false },
        { type: "scatter", mode: "markers",
          x: tr.median_mw, y: rowY,
          xaxis: "x2", yaxis: "y2",
          marker: { symbol: "diamond", color: "#34495e", size: 9,
                    line: { color: "#fff", width: 1 } },
          name: "Median (tech)", text: tr.labels, legendgroup: "tech",
          hovertemplate: "%{text}<br>Median: %{x:,.1f} MW<extra></extra>" },
        { type: "scatter", mode: "markers",
          x: tr.optimal_mw, y: rowY,
          xaxis: "x2", yaxis: "y2",
          marker: { symbol: "star", size: 13, color: "#E74C3C",
                    line: { color: "#fff", width: 1 } },
          name: "Optimal (tech)", text: tr.labels, legendgroup: "tech",
          hovertemplate: "%{text}<br>Optimal: %{x:,.1f} MW<extra></extra>" },
    ];

    // CV annotations sit at the right end of each Robustness bar so the
    // reader sees the dispersion metric at the same eye-line as the
    // bar itself. The xref is "x2" because that's the Robustness axis.
    const cvAnnotations = tr.labels.map((_, i) => ({
        xref: "x2", yref: "y2",
        x: tr.max_mw[i], y: i,
        xanchor: "left", yanchor: "middle",
        text: ` CV ${Math.round((tr.cv[i] || 0) * 100)}%`,
        showarrow: false,
        font: { size: 9, color: "#7f8c8d" }, xshift: 6,
    }));

    // Per-subplot headings sit just above each subplot's top edge in
    // paper coordinates. Each is numbered a), b) … in the figure-caption
    // convention so the reader (and any external commentary) can refer
    // to them unambiguously. The muted-grey subtitle below the heading
    // lets the panel be read without a chart-level intro.
    const subplotTitles = [
        { text: "<b>a)  Cost ↔ Distance Frontier</b><br>" +
                "<span style='font-size:10px;color:#7f8c8d'>each dot = one alternative · x = normalised L1 distance from the cost-optimal · green band = cost slack envelope · ★ = optimal</span>",
          xref: "paper", yref: "paper",
          x: 0.5, y: 0.93, xanchor: "center", yanchor: "bottom",
          showarrow: false, font: { size: 12, color: "#2C3E50" } },
        { text: "<b>b)  Decision Robustness</b><br>" +
                "<span style='font-size:10px;color:#7f8c8d'>min↔max invested MW per technology across the MGA set · sorted by CV (must-build at bottom, swappable at top)</span>",
          xref: "paper", yref: "paper",
          x: 0.5, y: 0.50, xanchor: "center", yanchor: "bottom",
          showarrow: false, font: { size: 12, color: "#2C3E50" } },
    ];

    // Cost-slack envelope behind the Frontier scatter. Uses xref="paper"
    // so it spans the full Frontier x-domain regardless of the data
    // range, and yref="y" anchors it to the Frontier's own y-axis.
    const slackShapes = [
        { type: "rect", xref: "x domain", x0: 0, x1: 1,
          yref: "y", y0: h.optimal_cost_busd, y1: h.cost_limit_busd,
          fillcolor: "rgba(39,174,96,0.12)", line: { width: 0 },
          layer: "below" },
        { type: "line", xref: "x domain", x0: 0, x1: 1,
          yref: "y", y0: h.cost_limit_busd, y1: h.cost_limit_busd,
          line: { color: "#27AE60", width: 1, dash: "dash" } },
        { type: "line", xref: "x domain", x0: 0, x1: 1,
          yref: "y", y0: h.optimal_cost_busd, y1: h.optimal_cost_busd,
          line: { color: "rgba(0,0,0,0.18)", width: 1 } },
    ];

    const traces = [...frontierTraces, ...robustnessTraces];

    // Vertical stack. The chart has no super-title (the panel chrome
    // already labels it "Robust Frontier"); each subplot is captioned
    // in place. The top margin is tight — just enough room for the a)
    // heading to breathe.
    const layout = {
        margin: { l: 220, r: 100, t: 24, b: 80 },
        // ── Top subplot: Frontier ──
        xaxis: {
            domain: [0.0, 1.0], anchor: "y",
            title: { text: "Distance from cost-optimal (norm.)",
                     font: { size: 11 } },
            gridcolor: "rgba(0,0,0,0.08)", rangemode: "tozero",
        },
        yaxis: {
            domain: [0.58, 0.90], anchor: "x",
            title: { text: "System cost ($B)", font: { size: 11 } },
            gridcolor: "rgba(0,0,0,0.08)",
        },
        // ── Bottom subplot: Robustness ──
        xaxis2: {
            domain: [0.0, 1.0], anchor: "y2",
            title: { text: "MW", font: { size: 11 } },
            gridcolor: "rgba(0,0,0,0.08)",
            zerolinecolor: "rgba(0,0,0,0.15)", automargin: true,
        },
        yaxis2: {
            domain: [0.0, 0.46], anchor: "x2",
            tickvals: rowY, ticktext: tr.labels,
            autorange: "reversed", automargin: true,
            gridcolor: "rgba(0,0,0,0.04)",
        },
        shapes: slackShapes,
        annotations: [...subplotTitles, ...cvAnnotations],
        showlegend: true,
        legend: {
            orientation: "h",
            y: -0.06, x: 0.5, xanchor: "center", yanchor: "top",
            font: { size: 10 },
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
