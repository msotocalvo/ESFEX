/* Commitment Heatmap (UC) — the signature visualisation of a Unit
 * Commitment run. Rows are committable units (gen × node), columns are
 * hours, cells are 0/1 (off/on). Renewable / non-committable units are
 * omitted so the heatmap focuses on what UC actually decides.
 *
 * Payload contract (see UCCommitmentHeatmapChart._build_payload):
 *   { year, hours: [0..N-1], units: ["Diesel @ Havana", …],
 *     status: [[0|1, …], …]   # rows × cols matching units × hours }
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
        if (!data.units || data.units.length === 0) {
            _ucClearPlot(); showStatus("No committable units (renewables aren't shown here)", false);
            return;
        }
        try { renderHeatmap(data); hideStatus(); }
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

function renderHeatmap(data) {
    // Two-tone colorscale: light grey for off, accent green for on.
    // Discrete (not interpolated) — explicit stops at the band edges.
    const colorscale = [
        [0.0, "#ECEFF1"],
        [0.5, "#ECEFF1"],
        [0.5, "#27AE60"],
        [1.0, "#27AE60"],
    ];

    const trace = {
        z: data.status,
        x: data.hours,
        y: data.units,
        type: "heatmap",
        colorscale: colorscale,
        zmin: 0, zmax: 1,
        showscale: false,
        hovertemplate: "Unit: %{y}<br>Hour: %{x}<br>Status: %{z:.0f}<extra></extra>",
        xgap: 1, ygap: 1,
    };

    const title = `Commitment Schedule — Year ${data.year}`;
    // Dynamic height: rows × ~22 px so even large fleets stay readable.
    const rowH = Math.max(18, Math.min(28, 600 / data.units.length));
    const plot = document.getElementById("plot");
    plot.style.height = Math.max(400, data.units.length * rowH + 160) + "px";

    const layout = {
        margin: { t: 90, r: 30, b: 60, l: 220 },
        annotations: [
            { text: "<b>" + title + "</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.07, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: "<span style='color:#27AE60'>■</span> On  &nbsp; <span style='color:#ECEFF1'>■</span> Off",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.03, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11 } },
        ],
        xaxis: { title: "<b>Hour</b>", side: "bottom" },
        yaxis: { autorange: "reversed" },  // first unit at top
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", [trace], layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
