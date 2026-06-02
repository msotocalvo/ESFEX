/* Technology Performance — interactive Plotly replica of the matplotlib
 * CFLcoeVallcoeChart, made faithful to the original layout:
 *   - Numeric x axis (tickvals: tech indices, ticktext: tech names).
 *   - Each tech "column" carries jittered scatter markers on the LEFT
 *     half and a Gaussian-sum KDE silhouette on the RIGHT half — pre-
 *     computed Python-side as a closed polygon ready to fill.
 *   - Subplot (b) LCOE/VALCOE y-axis is REVERSED (max cost at the
 *     bottom, 0 at the top) to mirror the matplotlib chart.
 *   - Light grey vertical separators between tech columns.
 *   - Marker fill encodes the year via "RdBu" reversed colormap with a
 *     "Year" colorbar on the right.
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
            showStatus(data && data.error ? data.error : "No CF/LCOE data", false);
            return;
        }
        if (!data.techs || data.techs.length === 0) {
            showStatus("No CF/LCOE data", false);
            return;
        }
        try {
            renderTechPerf(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[tech_perf.js] render threw:", e);
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

// One closed polygon trace per tech's KDE silhouette. Plotly fills it
// with `fill: "toself"` so we don't need a separate baseline trace.
function _kdeTrace(curve, opts) {
    return {
        type: "scatter", mode: "lines",
        x: curve.x, y: curve.y,
        fill: "toself",
        fillcolor: opts.fillcolor,
        line: { color: "white", width: 1 },
        name: opts.name,
        legendgroup: opts.name,
        showlegend: opts.showlegend === true,
        xaxis: opts.xaxis, yaxis: opts.yaxis,
        hoverinfo: "skip",
    };
}

function _scatterTrace(points, valueKey, opts, yearScale) {
    if (!points || points.length === 0) return null;
    const marker = {
        size: 11,
        symbol: opts.symbol,
        line: { color: "#2C3E50", width: 0.5 },
    };
    if (yearScale) {
        marker.color = points.map(p => p.year);
        marker.colorscale = "RdBu";
        marker.reversescale = true;
        marker.cmin = yearScale.cmin;
        marker.cmax = yearScale.cmax;
        if (opts.showscale) {
            marker.showscale = true;
            marker.colorbar = {
                title: { text: "Year", side: "right" },
                thickness: 12, len: 0.85,
                x: 0.96, xanchor: "left",
            };
        }
    } else {
        marker.color = opts.flatColor || "#3498db";
    }
    return {
        type: "scatter",
        x: points.map(p => p.x),                  // numeric jittered x
        y: points.map(p => p[valueKey]),
        mode: "markers",
        name: opts.name,
        legendgroup: opts.name,
        marker: marker,
        xaxis: opts.xaxis, yaxis: opts.yaxis,
        customdata: points.map(p => [p.tech, p.year]),
        hovertemplate:
            "<b>%{customdata[0]}</b><br>Year %{customdata[1]}<br>" +
            opts.unit + ": %{y:.2f}<extra></extra>",
    };
}

function renderTechPerf(data) {
    const traces = [];
    const yearScale = (data.years_max > data.years_min)
        ? { cmin: data.years_min, cmax: data.years_max }
        : null;
    const N = data.techs.length;

    // ── Subplot (a) — KDE silhouettes + CF markers ──
    (data.cf_kdes || []).forEach((curve, i) => {
        traces.push(_kdeTrace(curve, {
            name: "CF Distribution",
            fillcolor: "rgba(107,107,107,0.4)",
            xaxis: "x", yaxis: "y",
            showlegend: i === 0,    // only the first carries a legend entry
        }));
    });
    const cfScatter = _scatterTrace(data.cf_points, "cf", {
        name: "Capacity Factor", symbol: "circle",
        unit: "CF (%)", showscale: true,
        flatColor: "#555555",
        xaxis: "x", yaxis: "y",
    }, yearScale);
    if (cfScatter) traces.push(cfScatter);

    // ── Subplot (b) — LCOE KDEs (blue) + VALCOE KDEs (red) + markers ──
    (data.lcoe_kdes || []).forEach((curve, i) => {
        traces.push(_kdeTrace(curve, {
            name: "LCOE Distribution",
            fillcolor: "rgba(74,144,226,0.4)",
            xaxis: "x2", yaxis: "y2",
            showlegend: i === 0,
        }));
    });
    (data.vallcoe_kdes || []).forEach((curve, i) => {
        traces.push(_kdeTrace(curve, {
            name: "VALCOE Distribution",
            fillcolor: "rgba(231,76,60,0.4)",
            xaxis: "x2", yaxis: "y2",
            showlegend: i === 0,
        }));
    });
    const lcoeScatter = _scatterTrace(data.lcoe_points, "lcoe", {
        name: "LCOE", symbol: "triangle-up",
        unit: "LCOE ($/MWh)",
        flatColor: "#4A90E2",
        xaxis: "x2", yaxis: "y2",
    }, yearScale);
    if (lcoeScatter) traces.push(lcoeScatter);
    const vallcoeScatter = _scatterTrace(data.vallcoe_points, "vallcoe", {
        name: "VALCOE", symbol: "square",
        unit: "VALCOE ($/MWh)",
        flatColor: "#E74C3C",
        xaxis: "x2", yaxis: "y2",
    }, yearScale);
    if (vallcoeScatter) traces.push(vallcoeScatter);

    if (traces.length === 0) {
        showStatus("No CF/LCOE data", false);
        return;
    }

    // Vertical separators between tech columns (i + 0.5 lines) for both
    // subplots — same as the matplotlib axvline calls.
    const separators = [];
    for (let i = 0; i < N - 1; i++) {
        separators.push({
            type: "line", xref: "x", yref: "y",
            x0: i + 0.5, x1: i + 0.5, y0: 0, y1: 100,
            line: { color: "#7F8C8D", width: 1, dash: "dash" }, opacity: 0.3,
        });
        separators.push({
            type: "line", xref: "x2", yref: "y2 domain",
            x0: i + 0.5, x1: i + 0.5, y0: 0, y1: 1,
            line: { color: "#7F8C8D", width: 1, dash: "dash" }, opacity: 0.3,
        });
    }

    // Numeric tick array: position 0..N-1, label = tech name.
    const tickvals = Array.from({ length: N }, (_, i) => i);
    const ticktext = data.techs;

    const layout = {
        // Bottom margin sized to host the horizontal legend plus the
        // rotated tech tick labels.
        margin: { t: 40, r: 80, b: 120, l: 80 },
        showlegend: true,
        annotations: [
            // Super-title removed per user request; subtitles only.
            { text: "<b>a) Capacity Factor by Technology</b>",
              x: 0.465, xref: "paper", xanchor: "center",
              y: 1.00, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
            { text: "<b>b) LCOE and VALCOE by Technology</b>",
              x: 0.465, xref: "paper", xanchor: "center",
              y: 0.46, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 13 } },
        ],
        shapes: separators,
        // Horizontal legend below the figure, centred over the plot
        // area. y=-0.125 is half the previous -0.25 gap, pulling the
        // legend up closer to the bottom subplot.
        legend: {
            orientation: "h",
            x: 0.465, xanchor: "center",
            y: -0.125, yanchor: "top",
            font: { size: 10 },
            tracegroupgap: 12,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        xaxis: {
            domain: [0, 0.93], anchor: "y",
            tickvals: tickvals, ticktext: ticktext,
            range: [-0.8, N - 0.2],
            showticklabels: false,
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis: {
            domain: [0.54, 0.98], anchor: "x",
            title: "<b>Capacity Factor (%)</b>",
            range: [0, 100],
            gridcolor: "rgba(0,0,0,0.08)",
        },
        xaxis2: {
            domain: [0, 0.93], anchor: "y2",
            tickvals: tickvals, ticktext: ticktext,
            range: [-0.8, N - 0.2],
            tickangle: -45,
            title: "<b>Technology</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
        yaxis2: {
            domain: [0.0, 0.44], anchor: "x2",
            title: "<b>LCOE / VALCOE ($/MWh)</b>",
            // Matplotlib version put max cost at the bottom; we keep
            // the convention so the two charts read identically.
            range: [data.max_cost || 500, 0],
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
