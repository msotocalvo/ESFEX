/* Load Shedding + Curtailment Timeline (UC) — adequacy diagnostic.
 *
 * Two stacked subplots sharing the x axis:
 *   Top:    Load shedding (MW) by hour. Red bars; cumulative line
 *           overlay (MWh) on a secondary axis.
 *   Bottom: Curtailment (MW) by hour. Amber bars; cumulative MWh line.
 *
 * Headline annotations show totals + max single-hour values so a
 * glance is enough to answer "was the run adequate?".
 *
 * Payload:
 *   { year, hours: [0..N-1],
 *     load_shed_mw: [...],
 *     curtailment_mw: [...],
 *     load_shed_cum_mwh: [...],
 *     curtailment_cum_mwh: [...],
 *     demand_total_mwh: float,
 *     load_shed_total_mwh: float, load_shed_max_mw: float,
 *     curtailment_total_mwh: float, curtailment_max_mw: float }
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
            _ucClearPlot(); showStatus("No timeline data", false);
            return;
        }
        try { renderTimeline(data); hideStatus(); }
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

function _fmtMWh(v) {
    const a = Math.abs(v);
    if (a >= 1e6) return (v / 1e6).toFixed(2) + " TWh";
    if (a >= 1e3) return (v / 1e3).toFixed(1) + " GWh";
    return v.toFixed(0) + " MWh";
}

function renderTimeline(data) {
    const x = data.hours;
    const traces = [];

    // Load shedding hourly (top subplot, primary axis = MW)
    traces.push({
        x: x, y: data.load_shed_mw,
        type: "bar", name: "Load shed (MW)",
        marker: { color: "#E74C3C", opacity: 0.85 },
        xaxis: "x", yaxis: "y",
        hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Load shed</extra>",
    });
    // Load shedding cumulative (top subplot, secondary axis = MWh)
    traces.push({
        x: x, y: data.load_shed_cum_mwh,
        type: "scatter", mode: "lines",
        name: "Cum. load shed (MWh)",
        line: { color: "#922B21", width: 2, dash: "dot" },
        xaxis: "x", yaxis: "y2",
        hovertemplate: "Hour %{x}: %{y:,.0f} MWh<extra>Cumulative</extra>",
    });

    // Curtailment hourly (bottom subplot)
    traces.push({
        x: x, y: data.curtailment_mw,
        type: "bar", name: "Curtailment (MW)",
        marker: { color: "#F39C12", opacity: 0.85 },
        xaxis: "x2", yaxis: "y3",
        hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Curtailment</extra>",
    });
    traces.push({
        x: x, y: data.curtailment_cum_mwh,
        type: "scatter", mode: "lines",
        name: "Cum. curtailment (MWh)",
        line: { color: "#9A6700", width: 2, dash: "dot" },
        xaxis: "x2", yaxis: "y4",
        hovertemplate: "Hour %{x}: %{y:,.0f} MWh<extra>Cumulative</extra>",
    });

    const title = `Load Shedding &amp; Curtailment — Year ${data.year}`;
    const sharePct = (data.demand_total_mwh > 0)
        ? (100 * data.load_shed_total_mwh / data.demand_total_mwh).toFixed(1)
        : "0";
    const subTitle =
        `Load shed: total ${_fmtMWh(data.load_shed_total_mwh)} ` +
        `(${sharePct}% of demand) — peak ${data.load_shed_max_mw.toFixed(1)} MW &nbsp;|&nbsp; ` +
        `Curtailment: total ${_fmtMWh(data.curtailment_total_mwh)} — ` +
        `peak ${data.curtailment_max_mw.toFixed(1)} MW`;

    const layout = {
        margin: { t: 110, r: 70, b: 60, l: 70 },
        grid: { rows: 2, columns: 1, pattern: "independent", roworder: "top to bottom" },
        showlegend: true,
        annotations: [
            { text: "<b>" + title + "</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.16, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: subTitle,
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.08, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 11, color: "#5D6D7E" } },
        ],
        legend: {
            orientation: "h",
            x: 0.5, xanchor: "center",
            y: 1.02, yanchor: "bottom",
            font: { size: 10 },
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        xaxis:  { domain: [0, 1], anchor: "y", showticklabels: false },
        yaxis:  { domain: [0.55, 1.0], title: "<b>Load shed (MW)</b>",
                  rangemode: "tozero" },
        yaxis2: { domain: [0.55, 1.0], overlaying: "y", side: "right",
                  title: "<b>Cum. (MWh)</b>", rangemode: "tozero", showgrid: false },
        xaxis2: { domain: [0, 1], anchor: "y3", title: "<b>Hour</b>" },
        yaxis3: { domain: [0.0, 0.45], title: "<b>Curtailment (MW)</b>",
                  rangemode: "tozero" },
        yaxis4: { domain: [0.0, 0.45], overlaying: "y3", side: "right",
                  title: "<b>Cum. (MWh)</b>", rangemode: "tozero", showgrid: false },
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
