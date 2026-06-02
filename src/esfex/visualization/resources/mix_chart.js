/* Generation Mix interactive Plotly chart.
 *
 * Mirrors the matplotlib GenerationMixChart in results_charts.py:
 *   Subplot a) Monthly stacked area — positive (renewable + rooftop +
 *      thermal + storage_discharge), negative (storage_charge,
 *      curtailment, spillage, reserve), plus a demand line and a
 *      secondary y-axis with the operational RE % line and the
 *      RE-target step line.
 *   Subplot b) Yearly stacked bars — investments (positive) and
 *      retirements (negative, hatched). Secondary y axis carries the
 *      investment cost line.
 *
 * Wire format: Python builds the dict in GenerationMixChart._build_payload,
 * exposes it through a QWebChannel bridge as get_data(). On every refresh
 * the JS re-pulls the payload and calls Plotly.react so panning / zoom
 * state is preserved when only the data changes.
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
        catch (e) {
            showStatus("Bad payload: " + e.message, true);
            return;
        }
        if (!data || data.error) {
            showStatus(data && data.error ? data.error : "No data", false);
            return;
        }
        if (!data.year_list || data.year_list.length === 0
            || !data.total_months) {
            showStatus("No generation data", false);
            return;
        }
        try {
            renderMix(data);
            hideStatus();
        } catch (e) {
            showStatus("Render error: " + e.message, true);
            console.error(e);
        }
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

function renderMix(data) {
    const traces = [];
    const annotations = [];
    const shapes = [];
    const totalMonths = data.total_months;
    const yearList = data.year_list;
    // Monthly x-axis: integer month index 0 … totalMonths-1
    const monthX = Array.from({ length: totalMonths }, (_, i) => i);

    // ── Subplot (a) — Positive stacked area ──
    // legendgroup is per-label (not per-category) so clicking a
    // technology in the legend toggles its subplot-a *and* its
    // subplot-b (investment / retirement) traces in lockstep.
    for (const s of (data.positive_series || [])) {
        traces.push({
            x: monthX, y: s.values_gwh,
            type: "scatter", mode: "lines",
            line: { width: 0, color: s.color },
            stackgroup: "pos",
            fillcolor: s.color,
            name: s.label,
            xaxis: "x", yaxis: "y",
            hovertemplate: "%{y:,.1f} GWh<extra>%{fullData.name}</extra>",
            legendgroup: s.label,
        });
    }

    // ── Subplot (a) — Negative stacked area (storage_charge, curtailment, spillage, reserve) ──
    for (const s of (data.negative_series || [])) {
        const neg = (s.values_gwh || []).map(v => -Math.abs(v || 0));
        traces.push({
            x: monthX, y: neg,
            type: "scatter", mode: "lines",
            line: { width: 0, color: s.color },
            stackgroup: "neg",
            fillcolor: s.color,
            name: s.label,
            xaxis: "x", yaxis: "y",
            customdata: s.values_gwh,
            hovertemplate: "%{customdata:,.1f} GWh<extra>%{fullData.name}</extra>",
            legendgroup: s.label,
        });
    }

    // ── Demand line on subplot a ──
    if (data.demand_gwh && data.demand_gwh.length) {
        traces.push({
            x: monthX, y: data.demand_gwh,
            type: "scatter", mode: "lines",
            line: { color: "#000000", width: 2.5, dash: "dash" },
            name: "Total Demand",
            xaxis: "x", yaxis: "y",
            hovertemplate: "%{y:,.1f} GWh<extra>Demand</extra>",
        });
    }

    // ── Operational RE % (subplot a, secondary y) ──
    if (data.operational_re_pct && data.operational_re_pct.length) {
        traces.push({
            x: monthX, y: data.operational_re_pct,
            type: "scatter", mode: "lines",
            line: { color: "#E74C3C", width: 2.5, dash: "dash" },
            name: "Operational RE (%)",
            xaxis: "x", yaxis: "y2",
            hovertemplate: "%{y:.1f}%<extra>Op. RE</extra>",
        });
    }

    // ── RE target step line (subplot a, secondary y) ──
    if (data.re_target_pct && data.re_target_pct.length
        && data.re_target_pct.some(v => v > 0)) {
        traces.push({
            x: monthX, y: data.re_target_pct,
            type: "scatter", mode: "lines",
            line: { color: "#1F3A93", width: 2, shape: "hv" },
            name: "RE Target (%)",
            xaxis: "x", yaxis: "y2",
            hovertemplate: "%{y:.1f}%<extra>RE Target</extra>",
        });
    }

    // (RE threshold markers — 34.7% / 50% / 100% — removed per user request.)

    // Year-axis values for subplot b. Force string conversion so the
    // categorical x2 axis treats them as discrete labels — and so the
    // bar traces (categorical) and the cost-line trace (also categorical)
    // share the same x coordinates instead of being placed on a
    // parallel numeric axis. Without this, Plotly can scatter the bars
    // across positions that don't line up with the cost line.
    const yearLabels = yearList.map(String);

    // ── Subplot (b) — yearly investments stacked bars ──
    // showlegend:false + legendgroup matching the subplot-a series
    // suppresses the duplicate "Solar PV" / "Wind Turbine" / …
    // entries the legend would otherwise grow per investment.
    // The user still toggles them via the subplot-a legend entry.
    for (const s of (data.investments || [])) {
        traces.push({
            x: yearLabels, y: s.values_gw,
            type: "bar",
            name: s.label,
            marker: { color: s.color, opacity: 0.85 },
            xaxis: "x2", yaxis: "y3",
            offsetgroup: "invret",     // keep inv+ret bars at same x slot
            hovertemplate: "%{x}: %{y:,.2f} GW<extra>%{fullData.name}</extra>",
            legendgroup: s.label,
            showlegend: false,
        });
    }

    // ── Subplot (b) — retirements (negative, hatched) ──
    // Same dedup story as investments: hatched bars share the
    // tech's legendgroup, no entry of their own.
    for (const s of (data.retirements || [])) {
        const neg = (s.values_gw || []).map(v => -Math.abs(v || 0));
        // Retirement labels arrive with a " (retired)" suffix from
        // the payload builder; group with the parent tech entry so
        // a click on "Solar PV" toggles the retirements too.
        const parentLabel = s.label.replace(/\s*\(retired\)\s*$/, "");
        traces.push({
            x: yearLabels, y: neg,
            type: "bar",
            name: s.label,
            // Same tech colour as the subplot-a area and the
            // investment bar, but with lower opacity (0.45 vs 0.85)
            // so retirements read as "translucent / faded" — the
            // visual cue for "capacity going away" without altering
            // the technology hue.
            marker: {
                color: s.color, opacity: 0.45,
                line: { color: s.color, width: 0.5 },
            },
            xaxis: "x2", yaxis: "y3",
            offsetgroup: "invret",
            customdata: s.values_gw,
            hovertemplate: "%{x}: %{customdata:,.2f} GW retired<extra>%{fullData.name}</extra>",
            legendgroup: parentLabel,
            showlegend: false,
        });
    }

    // ── Investment cost line (subplot b, secondary y) ──
    if (data.cost_musd_by_year && data.cost_musd_by_year.length) {
        traces.push({
            x: yearLabels, y: data.cost_musd_by_year,
            type: "scatter", mode: "lines+markers",
            line: { color: "#27AE60", width: 2.5 },
            marker: { color: "#27AE60", size: 6 },
            name: "Investment Cost",
            xaxis: "x2", yaxis: "y4",
            hovertemplate: "%{x}: %{y:,.1f} M$<extra>Cost</extra>",
        });
    }

    // Tick positions for the monthly x axis: middle of each year
    const yearTickVals = yearList.map((_, i) => i * 12 + 5.5);
    const yearTickText = yearList.map(y => String(y));
    const step = Math.max(1, Math.ceil(yearList.length / 15));
    const tickValsSparse = yearTickVals.filter((_, i) => i % step === 0);
    const tickTextSparse = yearTickText.filter((_, i) => i % step === 0);

    const yearRange = yearList.length > 0
        ? [yearList[0] - 0.5, yearList[yearList.length - 1] + 0.5]
        : [0, 1];

    const subTitleA = yearList.length
        ? `a) Generation Mix Evolution [${yearList[0]}–${yearList[yearList.length - 1]}]`
        : "a) Generation Mix Evolution";

    const layout = {
        // Wide right margin to host the vertical legend outside both
        // subplots. Plot domain ends at x≈0.78 (yaxis2/yaxis4 sit on
        // the right edge of that domain), legend starts at x=1.02 of
        // the plot area, which corresponds to the freed-up gutter.
        margin: { t: 50, r: 30, b: 50, l: 70 },
        showlegend: true,
        barmode: "relative",
        annotations: annotations.concat([
            // Subplot titles: centred over the plot area (domain x∈[0,0.83]
            // → centre at 0.415) so they sit visually above each panel,
            // independent of the legend gutter on the right.
            { text: "<b>" + subTitleA + "</b>",
              x: 0.415, xref: "paper", xanchor: "center",
              y: 1.02, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
            { text: "<b>b) Annual Capacity Investments &amp; Retirements</b>",
              x: 0.415, xref: "paper", xanchor: "center",
              y: 0.42, yref: "paper", yanchor: "bottom",
              showarrow: false, font: { size: 15 } },
        ]),
        shapes: shapes.concat([
            // Zero baselines
            { type: "line", xref: "x",  yref: "y",
              x0: 0, x1: totalMonths - 1, y0: 0, y1: 0,
              line: { color: "#000000", width: 1.2 } },
            { type: "line", xref: "x2 domain", yref: "y3",
              x0: 0, x1: 1, y0: 0, y1: 0,
              line: { color: "#000000", width: 1.2 } },
        ]),
        // Vertical legend hugging the right edge of both subplots.
        // Plot domain runs x ∈ [0, 0.83]; legend starts at x=0.88 in
        // paper coords — that 0.05 gap is ~20% of the previous 0.24
        // gap (when domain ended at 0.78 and legend lived at x=1.02
        // outside the paper).
        legend: {
            orientation: "v",
            x: 0.88, xanchor: "left",
            y: 0.5, yanchor: "middle",
            font: { size: 9 },
            tracegroupgap: 6,
            bgcolor: "rgba(255,255,255,0.85)",
            bordercolor: "rgba(0,0,0,0.1)",
            borderwidth: 1,
        },
        xaxis: {
            domain: [0, 0.83], anchor: "y",
            range: [0, totalMonths - 1],
            tickvals: tickValsSparse, ticktext: tickTextSparse,
            tickangle: -45, showgrid: false,
        },
        yaxis: {
            domain: [0.50, 1.0], anchor: "x",
            title: "<b>Energy (GWh)</b>",
        },
        yaxis2: {
            domain: [0.50, 1.0], anchor: "x",
            overlaying: "y", side: "right",
            title: { text: "<b>RE (%)</b>",
                     font: { color: "#E74C3C" } },
            tickfont: { color: "#E74C3C" },
            range: [0, 105], tickvals: [0, 20, 40, 60, 80, 100],
            showgrid: false,
        },
        xaxis2: {
            domain: [0, 0.83], anchor: "y3",
            type: "category",
            title: "<b>Year</b>",
            tickangle: -45,
        },
        yaxis3: {
            domain: [0.0, 0.40], anchor: "x2",
            title: "<b>Capacity (GW)</b>",
        },
        yaxis4: {
            domain: [0.0, 0.40], anchor: "x2",
            overlaying: "y3", side: "right",
            title: { text: "<b>Inv. Cost (M$)</b>",
                     font: { color: "#27AE60" } },
            tickfont: { color: "#27AE60" },
            showgrid: false,
            rangemode: "tozero",
        },
    };

    Plotly.react("plot", traces, layout, {
        responsive: true,
        displaylogo: false,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
    });
}

window.addEventListener("resize", () => {
    const plot = document.getElementById("plot");
    if (plot && plot.data) Plotly.Plots.resize(plot);
});
