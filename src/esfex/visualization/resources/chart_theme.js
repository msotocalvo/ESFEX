/* Theme bridge for every Plotly chart in the results dialog.
 *
 * `applyTheme(colors)` caches the active GUI palette and immediately
 * rewrites the rendered chart's background / font / gridline / tick
 * colours. Series colours are intentionally left alone (they're
 * semantic — red = cost, green = revenue) so they stay legible on
 * both light and dark surfaces.
 */
"use strict";

// Theme cache discovery order:
//   1) `window._currentThemeColors` — Python sets this BEFORE calling
//      applyTheme() so this script (loading slightly later) still sees it.
//   2) `localStorage.esfex_theme_colors` — survives full page reload.
if (!window._currentThemeColors) {
    try {
        const raw = localStorage.getItem("esfex_theme_colors");
        if (raw) window._currentThemeColors = JSON.parse(raw);
    } catch (_) { /* localStorage may be disabled */ }
}

window.applyTheme = function (colors, retries) {
    if (colors && typeof colors === "object") {
        window._currentThemeColors = colors;
    }
    const palette = window._currentThemeColors;
    if (!palette) return;

    const plot = document.getElementById("plot");
    if (!plot || !plot.layout || typeof Plotly === "undefined") {
        const tries = (retries == null) ? 0 : retries;
        if (tries < 15) {
            setTimeout(() => window.applyTheme(null, tries + 1), 200);
        }
        return;
    }
    _applyThemeNow(palette);
    _patchPlotlyIfNeeded();
};

function _applyThemeNow(colors) {
    // CRITICAL: also paint the page background. The chart HTML's body
    // CSS hardcodes `background: #FFFFFF`; Plotly changes only the
    // SVG's `paper_bgcolor`, so without touching the body the white
    // shell stays visible behind a dark Plotly canvas.
    if (colors.surface_primary) {
        document.documentElement.style.background = colors.surface_primary;
        document.body.style.background = colors.surface_primary;
    }
    if (colors.text_primary) {
        document.body.style.color = colors.text_primary;
    }
    // Also tint the "Loading…" / status overlay if it's still around.
    const statusEl = document.getElementById("status");
    if (statusEl && colors.text_secondary) {
        statusEl.style.color = colors.text_secondary;
    }

    const plot = document.getElementById("plot");
    if (!plot || !plot.layout) return;

    const update = {};
    if (colors.surface_primary) {
        update.paper_bgcolor = colors.surface_primary;
        update.plot_bgcolor  = colors.surface_primary;
    }
    if (colors.text_primary) {
        update["font.color"] = colors.text_primary;
    }

    const gridcolor = _withAlpha(colors.border_light || "#888888", 0.35);
    const linecolor = colors.border_medium || colors.border_light || "#888888";
    Object.keys(plot.layout).forEach(k => {
        if (!/^[xy]axis\d*$/.test(k)) return;
        update[k + ".gridcolor"]      = gridcolor;
        update[k + ".zerolinecolor"]  = linecolor;
        update[k + ".linecolor"]      = linecolor;
        update[k + ".tickcolor"]      = colors.text_secondary || linecolor;
        update[k + ".tickfont.color"] = colors.text_primary || linecolor;
    });

    if (colors.surface_primary) {
        update["legend.bgcolor"]    = _withAlpha(colors.surface_primary, 0.92);
        update["legend.bordercolor"] = linecolor;
        update["legend.font.color"]  = colors.text_primary;
    }

    try {
        Plotly.relayout(plot, update);
    } catch (e) {
        console.warn("[chart_theme] relayout failed:", e);
    }
}

// Monkey-patch Plotly so subsequent renders auto-reapply the theme.
// Idempotent — runs once per page.
function _patchPlotlyIfNeeded() {
    if (typeof Plotly === "undefined") return;
    if (Plotly._themePatched) return;
    const _origNew = Plotly.newPlot;
    const _origReact = Plotly.react;
    if (!_origNew || !_origReact) return;

    function _afterRender() {
        if (!window._currentThemeColors) return;
        requestAnimationFrame(() => _applyThemeNow(window._currentThemeColors));
    }
    Plotly.newPlot = function () {
        const r = _origNew.apply(Plotly, arguments);
        if (r && typeof r.then === "function") {
            r.then(_afterRender, function(){});
        } else {
            setTimeout(_afterRender, 30);
        }
        return r;
    };
    Plotly.react = function () {
        const r = _origReact.apply(Plotly, arguments);
        if (r && typeof r.then === "function") {
            r.then(_afterRender, function(){});
        } else {
            setTimeout(_afterRender, 30);
        }
        return r;
    };
    Plotly._themePatched = true;
}

// On script load: if a palette is already cached (Python set it
// before this script parsed), kick the apply loop now. Plotly may
// not be ready yet — applyTheme() handles that with its own retry.
if (window._currentThemeColors) {
    setTimeout(function () { window.applyTheme(null); }, 50);
}

function _withAlpha(color, alpha) {
    if (typeof color !== "string") return color;
    if (color.startsWith("rgba")) return color;
    if (color.startsWith("rgb(")) {
        return color.replace("rgb(", "rgba(").replace(")", "," + alpha + ")");
    }
    if (color.startsWith("#")) {
        const h = color.replace("#", "");
        const expand = h.length === 3
            ? h.split("").map(c => c + c).join("")
            : h.slice(0, 6);
        const r = parseInt(expand.slice(0, 2), 16);
        const g = parseInt(expand.slice(2, 4), 16);
        const b = parseInt(expand.slice(4, 6), 16);
        return "rgba(" + r + "," + g + "," + b + "," + alpha + ")";
    }
    return color;
}
