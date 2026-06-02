/* Hourly Dispatch Stack (UC) — what serves load every hour.
 *
 * Stacked area: each renewable / thermal / battery-discharge tech is a
 * positive band; battery charge and curtailment are stacked downward
 * (negative); the demand curve overlays as a black line so the user
 * can see at a glance whether dispatch matches load.
 *
 * Payload contract:
 *   { year, hours: [0..N-1],
 *     gen_by_tech: { "Solar": [..], "Wind": [..], "Diesel": [..], ... },  // MW per hour, positive
 *     bat_discharge: [...],  // MW per hour, positive
 *     bat_charge: [...],     // MW per hour, positive
 *     curtailment: [...],    // MW per hour, positive
 *     load_shed: [...],      // MW per hour, positive
 *     demand: [...],         // MW per hour, positive
 *     tech_colors: { "Solar": "#F39C12", ... }
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
            _ucClearPlot(); showStatus("No dispatch data", false);
            return;
        }
        try { renderDispatch(data); hideStatus(); }
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

const RE_LIKE = new Set(["Solar", "Wind", "Hydro", "Biomass", "Geothermal", "OTEC", "Nuclear", "Hydrogen"]);
function _sortKey(name) {
    // Renewables first (cheaper marginal cost → bottom of stack
    // visually), thermal in the middle, batteries last on top.
    if (RE_LIKE.has(name)) return 0;
    if (name === "Battery") return 2;
    return 1;
}

function renderDispatch(data) {
    const x = data.hours;
    const traces = [];

    // Generation by tech, stacked. Sort so renewables sit at the
    // bottom of the stack.
    const techs = Object.keys(data.gen_by_tech || {}).sort(
        (a, b) => _sortKey(a) - _sortKey(b) || a.localeCompare(b)
    );
    for (const tech of techs) {
        const series = data.gen_by_tech[tech];
        if (!series || !series.length || series.every(v => !v)) continue;
        traces.push({
            x: x, y: series,
            type: "scatter", mode: "none",
            stackgroup: "pos", groupnorm: "",
            name: tech,
            fillcolor: (data.tech_colors || {})[tech] || "#95A5A6",
            hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>" + tech + "</extra>",
        });
    }

    if (data.bat_discharge && data.bat_discharge.some(v => v > 0)) {
        traces.push({
            x: x, y: data.bat_discharge,
            type: "scatter", mode: "none",
            stackgroup: "pos", name: "Battery discharge",
            fillcolor: "rgba(155, 89, 182, 0.8)",
            hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Battery discharge</extra>",
        });
    }
    if (data.load_shed && data.load_shed.some(v => v > 0)) {
        traces.push({
            x: x, y: data.load_shed,
            type: "scatter", mode: "none",
            stackgroup: "pos", name: "Load shedding",
            fillcolor: "rgba(231, 76, 60, 0.85)",
            hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Load shed</extra>",
        });
    }

    // Battery charge and curtailment go DOWN (negative stack).
    if (data.bat_charge && data.bat_charge.some(v => v > 0)) {
        traces.push({
            x: x, y: data.bat_charge.map(v => -Math.abs(v || 0)),
            type: "scatter", mode: "none",
            stackgroup: "neg", name: "Battery charge",
            fillcolor: "rgba(155, 89, 182, 0.45)",
            customdata: data.bat_charge,
            hovertemplate: "Hour %{x}: %{customdata:,.1f} MW<extra>Battery charge</extra>",
        });
    }
    if (data.curtailment && data.curtailment.some(v => v > 0)) {
        traces.push({
            x: x, y: data.curtailment.map(v => -Math.abs(v || 0)),
            type: "scatter", mode: "none",
            stackgroup: "neg", name: "Curtailment",
            fillcolor: "rgba(241, 196, 15, 0.7)",
            customdata: data.curtailment,
            hovertemplate: "Hour %{x}: %{customdata:,.1f} MW<extra>Curtailment</extra>",
        });
    }

    // Demand line on top of the stack — the reference the dispatch
    // must meet (positive sum must equal demand).
    if (data.demand && data.demand.length === x.length) {
        traces.push({
            x: x, y: data.demand,
            type: "scatter", mode: "lines", name: "Demand",
            line: { color: "#000000", width: 2, dash: "solid" },
            hovertemplate: "Hour %{x}: %{y:,.1f} MW<extra>Demand</extra>",
        });
    }

    const title = `Hourly Dispatch — Year ${data.year}`;
    const layout = {
        margin: { t: 90, r: 30, b: 60, l: 70 },
        showlegend: true,
        annotations: [
            { text: "<b>" + title + "</b>",
              x: 0.5, xref: "paper", xanchor: "center",
              y: 1.14, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
        ],
        legend: {
            orientation: "h",
            x: 0.5, xanchor: "center",
            y: 1.06, yanchor: "bottom",
            font: { size: 10 },
            tracegroupgap: 8,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        shapes: [
            { type: "line", xref: "x domain", yref: "y",
              x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "#000000", width: 1 } },
        ],
        xaxis: { title: "<b>Hour</b>" },
        yaxis: { title: "<b>Power (MW)</b>" },
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
