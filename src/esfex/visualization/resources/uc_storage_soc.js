/* Storage SOC + Cycles (UC) — battery operation diagnostic.
 *
 * Top:    SOC trajectories (one line per battery) + system-wide
 *         aggregated charge/discharge envelope.
 * Bottom: Daily equivalent full cycles per battery (bar chart) —
 *         total discharge MWh / capacity MWh per day. A typical Li-ion
 *         is rated 1-2 cycles/day; sustained > 3 hints at degradation.
 *
 * Payload:
 *   { year, hours: [...], days: [...],
 *     batteries: ["Bat A", "Bat B", ...],
 *     soc_pct: { "Bat A": [...], ... },           # % of capacity, per hour
 *     daily_cycles: { "Bat A": [...], ... },      # per-day equivalent full cycles
 *     totals: { "Bat A": {charge_mwh, discharge_mwh, capacity_mwh}, ... } }
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
        bridge = channel.objects.loader; refresh();
    });
});
function refresh() {
    if (!bridge) return;
    bridge.get_data(rawJson => {
        let data = null;
        try { data = JSON.parse(rawJson); }
        catch (e) { _ucClearPlot(); showStatus("Bad payload: " + e.message, true); return; }
        if (!data || data.error) { _ucClearPlot(); showStatus(data && data.error ? data.error : "No data", false); return; }
        if (!data.batteries || !data.batteries.length) { _ucClearPlot(); showStatus("No batteries in this run", false); return; }
        try { renderSOC(data); hideStatus(); }
        catch (e) { showStatus("Render error: " + e.message, true); console.error(e); }
    });
}
function showStatus(t, e) { const s = document.getElementById("status"); s.textContent = t; s.style.display = "block"; s.style.color = e ? "#c0392b" : "#7F8C8D"; }
function hideStatus() { document.getElementById("status").style.display = "none"; }

const PALETTE = ["#9B59B6", "#3498DB", "#27AE60", "#F39C12", "#E74C3C", "#16A085", "#2980B9", "#8E44AD"];

function renderSOC(data) {
    const traces = [];
    data.batteries.forEach((bname, bi) => {
        const c = PALETTE[bi % PALETTE.length];
        traces.push({
            x: data.hours, y: data.soc_pct[bname],
            type: "scatter", mode: "lines",
            name: bname, line: { color: c, width: 2 },
            hovertemplate: "Hour %{x}: %{y:.1f}% SOC<extra>" + bname + "</extra>",
            xaxis: "x", yaxis: "y",
        });
        traces.push({
            x: data.days, y: data.daily_cycles[bname],
            type: "bar", name: bname + " (cycles)",
            marker: { color: c, opacity: 0.75 },
            hovertemplate: "Day %{x}: %{y:.2f} cycles<extra>" + bname + "</extra>",
            xaxis: "x2", yaxis: "y2",
            showlegend: false,
        });
    });

    // Header subtitle with key totals.
    const parts = data.batteries.map(bname => {
        const t = data.totals[bname] || {};
        const discharge = t.discharge_mwh || 0;
        const cap = t.capacity_mwh || 0;
        const ratio = (cap > 0) ? (discharge / cap).toFixed(2) : "—";
        return `${bname}: ${ratio} eq. full cycles total`;
    }).join(" &nbsp;|&nbsp; ");

    const layout = {
        margin: { t: 110, r: 30, b: 60, l: 70 },
        grid: { rows: 2, columns: 1, pattern: "independent", roworder: "top to bottom" },
        showlegend: true,
        annotations: [
            { text: `<b>Storage SOC &amp; Cycles — Year ${data.year}</b>`,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.16, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: parts,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.08, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11, color: "#5D6D7E" } },
        ],
        legend: {
            orientation: "h", x: 0.5, xanchor: "center",
            y: 1.02, yanchor: "bottom", font: { size: 10 },
            bgcolor: "rgba(255,255,255,0.85)", bordercolor: "rgba(0,0,0,0.1)", borderwidth: 1,
        },
        xaxis:  { title: "<b>Hour</b>", domain: [0, 1], anchor: "y" },
        yaxis:  { title: "<b>SOC (%)</b>", domain: [0.55, 1.0], range: [0, 100] },
        xaxis2: { title: "<b>Day</b>", domain: [0, 1], anchor: "y2" },
        yaxis2: { title: "<b>Eq. full cycles / day</b>", domain: [0.0, 0.45], rangemode: "tozero" },
        barmode: "group",
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout, {
        responsive: true, displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
