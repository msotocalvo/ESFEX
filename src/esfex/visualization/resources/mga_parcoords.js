/* MGA — Parallel Coordinates */
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
    const pc = data.parcoords;
    if (!pc || !pc.dim_labels || pc.dim_labels.length === 0) {
        showStatus("No parallel-coordinates data.", false); return;
    }
    const colors = pc.is_optimal.map(v => v ? 1 : 0);
    const dims = pc.dim_labels.map((label, i) => ({
        label: label, values: pc.dim_values[i]
    }));
    const trace = {
        type: "parcoords",
        line: { color: colors, colorscale: [[0, "#3498db"], [1, "#E74C3C"]],
                showscale: false },
        dimensions: dims,
        labelfont: { size: 12 },
        tickfont:  { size: 10 }
    };
    const layout = {
        margin: { l: 60, r: 40, t: 96, b: 40 },
        title: { text: "<b>Trade-off Parallel Coordinates</b><br><span style='font-size:11px;color:#7f8c8d'>each line = one alternative · red = optimal · drag axes to brush</span>",
                 x: 0.5, xanchor: "center", y: 0.98, yanchor: "top", font: { size: 13 } }
    };
    Plotly.purge("plot");
    Plotly.newPlot("plot", [trace], layout, { responsive: true, displaylogo: false });
}

window.addEventListener("resize", () => {
    const el = document.getElementById("plot");
    if (el && el.data) Plotly.Plots.resize(el);
});
