/* Custom Chart — user-built multi-axis Plotly chart.
 *
 * Payload contract (CustomChart._build_payload):
 *   { x_is_time, x_title, axes:{"1":{title},"2":{title},...},
 *     series:[ {name,type,axis(1-4),x,y,var_index,year_index,n_years} ] }
 *
 * Up to four Y axes are placed dynamically (1=left, 2=right, 3=far left,
 * 4=far right). Each series picks its representation: line / bar /
 * scatter / area. Colour is per variable; multiple overlaid years are
 * distinguished by progressively lighter shades of the variable colour.
 */
"use strict";

const TAB20 = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
    "#aec7e8", "#ffbb78", "#98df8a", "#ff9896", "#c5b0d5",
];

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
        if (!data.series || data.series.length === 0) {
            showStatus("Add at least one variable", false);
            return;
        }
        try {
            render(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error("[custom_chart.js]", e);
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

function hexToRgb(hex) {
    const m = hex.replace("#", "");
    return [parseInt(m.slice(0, 2), 16), parseInt(m.slice(2, 4), 16),
            parseInt(m.slice(4, 6), 16)];
}
// Blend toward white by `amt` (0..1) to distinguish overlaid years.
function shade(hex, amt) {
    const [r, g, b] = hexToRgb(hex);
    const f = v => Math.round(v + (255 - v) * amt);
    return `rgb(${f(r)},${f(g)},${f(b)})`;
}
function seriesColor(s) {
    const base = (s.color && s.color.length) ? s.color
                 : TAB20[s.var_index % TAB20.length];
    if (!s.n_years || s.n_years <= 1) return base;
    const amt = 0.55 * (s.year_index / (s.n_years - 1));
    return shade(base, amt);
}

const AXIS_NAME = { 1: "y", 2: "y2", 3: "y3", 4: "y4" };

function buildTrace(s) {
    const color = seriesColor(s);
    const yaxis = AXIS_NAME[s.axis] || "y";
    const width = (s.line_width != null) ? s.line_width : 1.8;
    const dash = s.line_dash || "solid";
    const common = { x: s.x, y: s.y, name: s.name, xaxis: "x", yaxis: yaxis,
                     legendgroup: s.name };
    if (s.visible === false) common.visible = "legendonly";
    if (s.type === "bar") {
        return Object.assign(common, {
            type: "bar",
            marker: { color: color, line: { color: "#FFFFFF", width: 0.4 } },
        });
    }
    if (s.type === "scatter") {
        return Object.assign(common, {
            type: "scatter", mode: "markers",
            marker: { color: color, size: 4 + width, opacity: 0.7,
                      line: { color: "#FFFFFF", width: 0.3 } },
        });
    }
    if (s.type === "area") {
        return Object.assign(common, {
            type: "scatter", mode: "lines", fill: "tozeroy",
            line: { color: color, width: width, dash: dash },
            fillcolor: shade(color, 0.6),
        });
    }
    // line (default)
    return Object.assign(common, {
        type: "scatter", mode: "lines",
        line: { color: color, width: width, dash: dash },
    });
}

function render(data) {
    const traces = data.series.map(buildTrace);
    const axes = data.axes || {};
    const used = Object.keys(axes).map(Number);

    const useLeft2 = used.includes(3);   // far-left (4th) axis
    const useRight2 = used.includes(4);  // far-right (4th) axis
    // Contract the plot domain so the stacked secondary axes (and their
    // tick labels/titles) stay inside the panel instead of overflowing.
    const GAP = 0.12;
    const domStart = useLeft2 ? GAP : 0.0;
    const domEnd = useRight2 ? 1.0 - GAP : 1.0;

    const layout = {
        margin: {
            t: 30, b: 80,
            l: 70 + (useLeft2 ? 60 : 0),
            r: 30 + (useRight2 ? 60 : 0),
        },
        showlegend: true,
        barmode: data.barmode || "group",
        legend: { orientation: "h", x: 0.5, xanchor: "center",
                  y: -0.06, yanchor: "top", font: { size: 10 } },
        hovermode: "closest",
        xaxis: {
            domain: [domStart, domEnd], anchor: "y",
            title: "<b>" + (data.x_title || "") + "</b>",
            gridcolor: "rgba(0,0,0,0.08)", automargin: true,
        },
        // Base Y axis always exists so the others can overlay it.
        yaxis: {
            anchor: "x", side: "left", automargin: true,
            title: "<b>" + (axes["1"] ? axes["1"].title : "") + "</b>",
            gridcolor: "rgba(0,0,0,0.08)",
        },
    };
    if (axes["2"]) {
        layout.yaxis2 = {
            overlaying: "y", side: "right", anchor: "x", automargin: true,
            title: "<b>" + axes["2"].title + "</b>", showgrid: false,
        };
    }
    if (axes["3"]) {
        layout.yaxis3 = {
            overlaying: "y", side: "left", anchor: "free", position: 0.0,
            title: "<b>" + axes["3"].title + "</b>", showgrid: false,
        };
    }
    if (axes["4"]) {
        layout.yaxis4 = {
            overlaying: "y", side: "right", anchor: "free", position: 1.0,
            title: "<b>" + axes["4"].title + "</b>", showgrid: false,
        };
    }

    // Apply per-axis log scale / manual range.
    const axLayoutKey = { 1: "yaxis", 2: "yaxis2", 3: "yaxis3", 4: "yaxis4" };
    [1, 2, 3, 4].forEach(n => {
        const meta = axes[String(n)];
        const lk = axLayoutKey[n];
        if (!meta || !layout[lk]) return;
        if (meta.log) layout[lk].type = "log";
        if (Array.isArray(meta.range)) {
            layout[lk].range = meta.log
                ? [Math.log10(Math.max(meta.range[0], 1e-9)),
                   Math.log10(Math.max(meta.range[1], 1e-9))]
                : meta.range;
        }
    });

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
