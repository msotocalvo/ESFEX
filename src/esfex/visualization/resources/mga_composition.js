/* MGA — Composition Treemaps small multiples.
 *
 * One treemap per alternative: investment partitioned by category and
 * then technology, sizes proportional to MW. Compact 3×4 grid lets you
 * compare compositions of all alternatives at a glance. */
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

// Same colour palette the pathway chart uses for category leaves.
const CAT_COLOR = {
    "Solar":    "#F4D03F",
    "Wind":     "#5DADE2",
    "Other RE": "#27AE60",
    "Storage":  "#8E44AD",
    "Thermal":  "#7F8C8D",
    "Other":    "#BDC3C7",
};

function render(data) {
    const alts = data.composition;
    if (!alts || alts.length === 0) {
        showStatus("No composition data.", false); return;
    }
    const cols = 4, rows = Math.ceil(alts.length / cols);
    const xGap = 0.018, yGap = 0.10;
    const cellW = (1 - xGap * (cols - 1)) / cols;
    const topPad = 0.06;
    const cellH = (1 - topPad - yGap * (rows - 1)) / rows;
    const traces = [];
    const annotations = [];
    alts.forEach((a, idx) => {
        const r = Math.floor(idx / cols), c = idx % cols;
        const xS = c * (cellW + xGap), xE = xS + cellW;
        const yE = 1 - topPad - r * (cellH + yGap), yS = yE - cellH;
        // Color each leaf by its category. Plotly treemap takes a marker
        // colors array aligned with labels; "All" + categories get the
        // category color, tech leaves inherit from their parent category.
        const colors = a.labels.map((lab, i) => {
            const par = a.parents[i];
            if (lab === "All") return "#ECF0F1";
            if (par === "All") return CAT_COLOR[lab] || "#bdc3c7";
            return CAT_COLOR[par] || "#bdc3c7";
        });
        traces.push({
            type: "treemap",
            labels: a.labels, parents: a.parents, values: a.values,
            branchvalues: "total",
            textinfo: "label",
            textfont: { size: 9 },
            marker: { colors: colors, line: { color: "#FFF", width: 1 } },
            hovertemplate: "<b>%{label}</b><br>%{value:,.0f} MW<extra></extra>",
            domain: { x: [xS, xE], y: [yS, yE] },
        });
        annotations.push({
            text: a.is_optimal ? `<b>Alt ${a.id} ★</b>` : `Alt ${a.id}`,
            x: (xS + xE) / 2, y: yE + 0.012,
            xref: "paper", yref: "paper",
            xanchor: "center", yanchor: "bottom",
            showarrow: false,
            font: { size: 10, color: a.is_optimal ? "#E74C3C" : "#2C3E50" },
        });
    });
    const layout = {
        margin: { l: 8, r: 8, t: 96, b: 28 },
        title: { text: "<b>Composition of Investment per Alternative</b><br>" +
                       "<span style='font-size:11px;color:#7f8c8d'>tile area = MW added · grouped by category then technology</span>",
                 x: 0.5, xanchor: "center", y: 0.98, yanchor: "top",
                 font: { size: 13 } },
        annotations: annotations,
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", traces, layout,
        { responsive: true, displaylogo: false });
}

window.addEventListener("resize", () => {
    const el = document.getElementById("plot");
    if (el && el.data) Plotly.Plots.resize(el);
});
