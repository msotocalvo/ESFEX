/* System Metrics Evolution — Plotly interactive chart.
 *
 * Replica of the matplotlib "Temporal Evolution of Power System
 * Metrics" figure: one row per metric, one marker per scenario year
 * normalised 0–100% on the metric's individual min/max range. Marker
 * fill encodes the year via a RdBu reversed colorscale (blue=early,
 * red=late), with min/max values annotated at the row ends.
 *
 * Payload contract (see SystemMetricsEvolutionChart._build_payload):
 *   { years_min, years_max, years: [...],
 *     categories: [{name, color}],   // ordered
 *     metrics: [{label, category, values_str:[...], normalized:[...],
 *                min_str, max_str}] }
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
            showStatus(data && data.error ? data.error : "No data", false);
            return;
        }
        if (!data.metrics || data.metrics.length === 0) {
            showStatus("No metrics available", false);
            return;
        }
        try {
            renderMetrics(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[system_metrics.js] render threw:", e);
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

function renderMetrics(data) {
    const metrics = data.metrics || [];
    const N = metrics.length;
    const years = data.years || [];
    const ymin = data.years_min;
    const ymax = data.years_max;
    const useYearScale = (ymax !== undefined && ymax > ymin);

    // ── Per-metric scatter row (one row per metric) ──
    // We emit a single trace per metric so the colour scale binds to
    // the year via marker.color, and showscale only fires on the first
    // trace (single colorbar for the whole figure).
    const traces = [];
    metrics.forEach((m, i) => {
        const xs = m.normalized || [];
        // y is constant = i (the row index). Plotly's category y axis
        // will draw 0…N-1 with the metric labels via ticktext below.
        const ys = xs.map(() => i);
        const marker = {
            size: 15,
            line: { color: "#FFFFFF", width: 1.2 },
        };
        if (useYearScale) {
            marker.color = years;
            marker.colorscale = "RdBu";
            marker.reversescale = true;
            marker.cmin = ymin;
            marker.cmax = ymax;
            if (i === 0) {
                marker.showscale = true;
                // Colorbar pushed further right so it doesn't crash into
                // the rotated category labels (which sit ~60 px past the
                // plot edge). Layout's margin.r makes room.
                marker.colorbar = {
                    title: { text: "Year", side: "right" },
                    thickness: 14, len: 0.75,
                    x: 1.12, xanchor: "left",
                };
            }
        } else {
            marker.color = "#555555";
        }
        traces.push({
            type: "scatter", mode: "markers",
            x: xs, y: ys,
            name: m.label,
            marker: marker,
            showlegend: false,
            customdata: m.values_str || [],
            hovertemplate:
                "<b>" + m.label + "</b><br>" +
                "Year %{marker.color}<br>" +
                "Value: %{customdata}<extra></extra>",
        });
    });

    // ── Min / Max annotations at the ends of each row ──
    const annotations = [];
    metrics.forEach((m, i) => {
        annotations.push({
            xref: "x", yref: "y",
            x: 0, y: i, xanchor: "right", yanchor: "middle",
            xshift: -8,
            text: m.min_str || "",
            showarrow: false, font: { size: 10, color: "#34495E" },
        });
        annotations.push({
            xref: "x", yref: "y",
            x: 100, y: i, xanchor: "left", yanchor: "middle",
            xshift: 8,
            text: m.max_str || "",
            showarrow: false, font: { size: 10, color: "#34495E" },
        });
    });

    // ── Category bands (subtle horizontal tinted strips) ──
    // A row span per category makes scanning easier when there are
    // many metrics. The colour is intentionally muted so it doesn't
    // compete with the marker colours.
    const shapes = [];
    // Walk the metrics array; whenever the category changes we know
    // the bounds of the previous category's strip.
    const catMap = {};
    (data.categories || []).forEach((c, idx) => { catMap[c.name] = c.color; });
    let runStart = 0;
    let runCat = metrics[0] ? metrics[0].category : "";
    for (let i = 1; i <= N; i++) {
        const cat = i < N ? metrics[i].category : null;
        if (cat !== runCat) {
            shapes.push({
                type: "rect", xref: "paper", yref: "y",
                x0: 0, x1: 1, y0: runStart - 0.5, y1: i - 0.5,
                fillcolor: catMap[runCat] || "rgba(127,140,141,0.06)",
                line: { width: 0 }, layer: "below",
            });
            annotations.push({
                xref: "paper", yref: "y",
                x: 1.0, y: (runStart + i - 1) / 2,
                xanchor: "left", yanchor: "middle",
                xshift: 60,
                text: "<b>" + runCat + "</b>",
                showarrow: false, font: { size: 10, color: "#2C3E50" },
                textangle: 90,
            });
            runStart = i;
            runCat = cat;
        }
    }

    const layout = {
        // Right margin reserves space for the rotated category labels
        // (which double as the band-colour legend) and the year
        // colorbar that lives further right.
        margin: { t: 50, r: 260, b: 60, l: 280 },
        annotations: annotations,
        shapes: shapes,
        title: {
            text: "<b>Temporal Evolution of Power System Metrics</b>",
            x: 0.5, xanchor: "center", y: 0.98, yanchor: "top",
            font: { size: 16 },
        },
        xaxis: {
            range: [-20, 130],
            tickvals: [0, 25, 50, 75, 100],
            ticktext: ["<b>Min</b>", "25%", "50%", "75%", "<b>Max</b>"],
            title: "<b>Normalized Metric Range (individual scaling)</b>",
            gridcolor: "rgba(0,0,0,0.06)",
            zeroline: false,
        },
        yaxis: {
            tickvals: metrics.map((_, i) => i),
            ticktext: metrics.map(m => m.label),
            range: [-0.5, N - 0.5],
            autorange: false,
            tickfont: { size: 11 },
            zeroline: false,
            gridcolor: "rgba(0,0,0,0.04)",
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
