/* Marginal Technology Heatmap (UC) — which tech sets the price.
 *
 * Two views:
 *   Top:    Time × tech color heatmap. Each cell encodes the
 *           marginal tech bucket as a discrete colour from the
 *           tech palette. Optionally tagged with the LMP via hover.
 *   Bottom: Bar chart of "hours marginal" — how many hours each
 *           tech set the system price.
 *
 * Marginal tech is computed Python-side via merit-order ex-post:
 *   1. For every hour, list ON committed gens (gen_status > 0.5).
 *   2. Among ON gens whose output is between 0 and rated capacity
 *      (i.e. dispatching partially — they have headroom to move),
 *      pick the one with the highest variable cost. That's the
 *      marginal unit; its tech bucket is the cell value.
 *   3. If no partial dispatcher exists (everyone at min or max),
 *      fall back to the highest-cost ON unit. If nothing is on,
 *      we tag the cell as "Load shed" (price = VOLL).
 *
 * Payload:
 *   { year, hours: [0..N-1],
 *     tech_indices: [0,1,2,...],
 *     tech_labels: ["Solar","Wind","Diesel",...],
 *     tech_colors: ["#...","#...",...],
 *     marginal_idx: [...],                      # index into tech_labels per hour
 *     hours_marginal: { "Diesel": 18, ... },    # tech → hour count
 *     hour_lmp: [...]                           # USD/MWh per hour (for hover)
 *   }
 */
"use strict";


function _ucClearPlot() {
    // Wipe the previous render so a stale chart from another
    // system selection does not leak through when the current
    // payload has no data.
    try { Plotly.purge("plot"); } catch (e) {}
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
        if (!data.hours || data.hours.length === 0) {
            _ucClearPlot(); showStatus("No marginal-tech data", false);
            return;
        }
        try { renderMarginal(data); hideStatus(); }
        catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
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

function renderMarginal(data) {
    const x = data.hours;
    const N = data.tech_labels.length;
    // Build a discrete colorscale from the per-tech palette. Plotly's
    // colorscale expects [normalised_pos, color] pairs; for N techs we
    // emit (N) plateaus.
    const colorscale = [];
    for (let i = 0; i < N; i++) {
        const left  = (N === 1) ? 0 : i / N;
        const right = (N === 1) ? 1 : (i + 1) / N;
        colorscale.push([left, data.tech_colors[i]]);
        colorscale.push([right - 1e-6, data.tech_colors[i]]);
    }

    // Single-row heatmap of the marginal tech index over the horizon.
    const z = [data.marginal_idx];
    const customdata = [data.marginal_idx.map((idx, h) => [
        data.tech_labels[idx] || "—",
        (data.hour_lmp && data.hour_lmp[h] != null) ? data.hour_lmp[h] : null,
    ])];
    const heatmap = {
        z: z, x: x, y: ["Marginal tech"],
        type: "heatmap",
        colorscale: colorscale,
        zmin: -0.5, zmax: N - 0.5,
        showscale: false,
        customdata: customdata,
        hovertemplate:
            "Hour: %{x}<br>" +
            "Tech: %{customdata[0]}<br>" +
            "LMP: %{customdata[1]:,.1f} USD/MWh<extra></extra>",
        xaxis: "x", yaxis: "y",
    };

    // Bar chart of hours-marginal per tech, ordered by frequency.
    const techByCount = Object.entries(data.hours_marginal || {})
        .sort((a, b) => b[1] - a[1]);
    const barX = techByCount.map(([t]) => t);
    const barY = techByCount.map(([, c]) => c);
    const barColors = barX.map(t => {
        const idx = data.tech_labels.indexOf(t);
        return idx >= 0 ? data.tech_colors[idx] : "#95A5A6";
    });
    const bar = {
        x: barX, y: barY, type: "bar",
        marker: { color: barColors, opacity: 0.9 },
        hovertemplate: "%{x}: %{y} h<extra></extra>",
        xaxis: "x2", yaxis: "y2",
        showlegend: false,
    };

    const title = `Marginal Technology — Year ${data.year}`;
    const layout = {
        margin: { t: 110, r: 30, b: 80, l: 110 },
        grid: { rows: 2, columns: 1, pattern: "independent", roworder: "top to bottom" },
        annotations: [
            { text: "<b>" + title + "</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.16, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: "Which technology sets the system price each hour",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.08, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11, color: "#5D6D7E" } },
        ],
        xaxis:  { domain: [0, 1], anchor: "y", title: "<b>Hour</b>" },
        yaxis:  { domain: [0.65, 0.95], showticklabels: false },
        xaxis2: { domain: [0, 1], anchor: "y2",
                  title: "<b>Technology</b>", tickangle: -25 },
        yaxis2: { domain: [0.0, 0.55], title: "<b>Hours marginal</b>",
                  rangemode: "tozero" },
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", [heatmap, bar], layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
