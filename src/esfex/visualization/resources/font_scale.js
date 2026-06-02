/* Global font-size scaler shared by every Plotly chart in the
 * results dialog. The results dialog drives the slider; on each
 * change it runs `setFontScale(N)` in the page via QWebEngine, and
 * this script rescales every size-bearing layout attribute by N
 * relative to the values captured at the chart's first render.
 *
 * Plotly's `font.size` is the global baseline, but most chart-
 * specific text (titles, axis labels, tick fonts, legend, per-
 * annotation fonts) overrides it with an explicit size — so we walk
 * the full layout, capture those sizes once, and reapply them
 * multiplied by `scale` on every change.
 */
"use strict";

window.setFontScale = function (scale) {
    const plot = document.getElementById("plot");
    if (!plot || !plot.layout) return;
    if (typeof scale !== "number" || !isFinite(scale) || scale <= 0) return;

    // First call: snapshot every font.size in the rendered layout so
    // subsequent scales are always computed against the original base
    // (otherwise repeated calls would compound rounding).
    if (!plot._fontBaseline) {
        plot._fontBaseline = _snapshotFontSizes(plot.layout);
    }
    const base = plot._fontBaseline;
    const update = {};

    if (base.font != null) update["font.size"] = base.font * scale;
    if (base.title != null) update["title.font.size"] = base.title * scale;
    if (base.legend != null) update["legend.font.size"] = base.legend * scale;

    // Every xaxis / yaxis (and their numbered counterparts x2, y2, …).
    for (const axKey in base.axes) {
        const ax = base.axes[axKey];
        if (ax.title != null)    update[`${axKey}.title.font.size`] = ax.title * scale;
        if (ax.tickfont != null) update[`${axKey}.tickfont.size`]   = ax.tickfont * scale;
        if (ax.colorbar_title != null) {
            update[`${axKey}.colorbar.title.font.size`] = ax.colorbar_title * scale;
        }
    }

    // Annotations are an indexed array; each one may have its own font.
    (base.annotations || []).forEach((sz, i) => {
        if (sz != null) update[`annotations[${i}].font.size`] = sz * scale;
    });

    // Trace-level colorbar fonts (heatmap, contour, scatter w/ markers).
    if (plot.data) {
        plot.data.forEach((tr, i) => {
            const baseTr = (base.traces && base.traces[i]) || {};
            if (baseTr.colorbar_title != null) {
                update[`data[${i}].colorbar.title.font.size`] = baseTr.colorbar_title * scale;
            }
            if (baseTr.colorbar_tick != null) {
                update[`data[${i}].colorbar.tickfont.size`] = baseTr.colorbar_tick * scale;
            }
            // For traces that carry a marker colorbar:
            if (baseTr.marker_colorbar_title != null) {
                update[`data[${i}].marker.colorbar.title.font.size`] = baseTr.marker_colorbar_title * scale;
            }
            if (baseTr.marker_colorbar_tick != null) {
                update[`data[${i}].marker.colorbar.tickfont.size`] = baseTr.marker_colorbar_tick * scale;
            }
        });
    }

    Plotly.relayout(plot, update);
    // Marker sizes on scatter traces also benefit from a (smaller)
    // bump — points get harder to see when fonts grow. Keep this
    // restyle separate so layout doesn't trigger a full redraw.
    if (plot.data && plot._markerBaseline) {
        const markerUpdate = { "marker.size": [] };
        const indices = [];
        plot.data.forEach((tr, i) => {
            const baseSize = plot._markerBaseline[i];
            if (baseSize != null) {
                // Half-scale the marker bump so big fonts don't make
                // the whole chart explode visually.
                markerUpdate["marker.size"].push(baseSize * (1 + (scale - 1) * 0.5));
                indices.push(i);
            }
        });
        if (indices.length) {
            Plotly.restyle(plot, markerUpdate, indices);
        }
    }
};

function _snapshotFontSizes(layout) {
    const out = { axes: {}, annotations: [], traces: [] };
    out.font = (layout.font && layout.font.size) || null;
    out.title = (layout.title && layout.title.font && layout.title.font.size) || null;
    out.legend = (layout.legend && layout.legend.font && layout.legend.font.size) || null;

    // Discover every axis (xaxis, xaxis2, …, yaxis, yaxis2, …).
    Object.keys(layout).forEach(k => {
        if (!/^[xy]axis\d*$/.test(k)) return;
        const ax = layout[k] || {};
        out.axes[k] = {
            title:    (ax.title && ax.title.font && ax.title.font.size) || null,
            tickfont: (ax.tickfont && ax.tickfont.size) || null,
        };
    });

    (layout.annotations || []).forEach(a => {
        out.annotations.push((a.font && a.font.size) || null);
    });

    // Capture trace-level marker.size baseline so we can scale points
    // alongside the text. The actual data array is on plot.data.
    const plot = document.getElementById("plot");
    if (plot && plot.data) {
        plot._markerBaseline = plot.data.map(tr =>
            (tr.marker && typeof tr.marker.size === "number") ? tr.marker.size : null
        );
        plot.data.forEach((tr, i) => {
            out.traces[i] = {
                colorbar_title: (tr.colorbar && tr.colorbar.title && tr.colorbar.title.font && tr.colorbar.title.font.size) || null,
                colorbar_tick:  (tr.colorbar && tr.colorbar.tickfont && tr.colorbar.tickfont.size) || null,
                marker_colorbar_title: (tr.marker && tr.marker.colorbar && tr.marker.colorbar.title && tr.marker.colorbar.title.font && tr.marker.colorbar.title.font.size) || null,
                marker_colorbar_tick:  (tr.marker && tr.marker.colorbar && tr.marker.colorbar.tickfont && tr.marker.colorbar.tickfont.size) || null,
            };
        });
    }
    return out;
}

// Re-snapshot on every full render so the baseline tracks layout
// changes triggered by params updates (e.g. sigma change rebuilds
// the payload and the layout is reborn).
document.addEventListener("DOMContentLoaded", () => {
    const plot = document.getElementById("plot");
    if (!plot) return;
    plot.on && plot.on("plotly_afterplot", () => {
        // Drop the previous baseline; next setFontScale call captures fresh.
        plot._fontBaseline = null;
    });
});
