"""Embedded Plotly dashboard for results visualization.

Drops in as a new tab inside :class:`ResultsDialog` — the existing
matplotlib charts are not modified. The tab is a ``QWebEngineView``
hosting ``resources/dashboard.html``, which renders an interactive
dashboard (filter bar, sticky KPI cards, brushable trajectory,
stacked generation mix) using Plotly.js.

Bytes flow:

* Python builds a :class:`DashboardLoader` knowing the available HDF5
  result files and their per-system base prefixes — the same maps
  ``ResultsDialog._scan_results`` already populates.
* :class:`_DashboardBridge` (QObject) registers as ``"loader"`` on a
  ``QWebChannel`` so the JS can ``await bridge.get_meta()`` and
  ``await bridge.get_overview(state_json)``.
* Each slot returns a JSON string. We use JSON (not native dict
  marshalling) because QWebChannel's primitive coverage for nested
  dicts with mixed types is patchy across Qt versions — JSON is
  the safest contract.

Failure modes:

* No HDF5 files yet → JS receives ``{systems: [], ...}`` and renders
  a "No results loaded" placeholder.
* HDF5 read errors are caught in :class:`DashboardLoader` so the
  whole page never sees an exception; the JS just gets an empty
  payload and shows the appropriate empty-state messages.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QUrl, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from esfex.visualization.panels.dashboard_loader import DashboardLoader

_log = logging.getLogger(__name__)
_RESOURCES_DIR = Path(__file__).parent.parent / "resources"


class _DashboardBridge(QObject):
    """JS-facing object. Slots are thin shells over DashboardLoader.

    Every public slot returns a JSON string; the JS side calls
    ``JSON.parse`` on receipt. Errors are caught here so the bridge
    contract is "always returns a parseable JSON string" — JS code
    can therefore use simple ``await`` without try/catch noise.
    """

    def __init__(self, loader: DashboardLoader, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._loader = loader

    @Slot(result=str)
    def get_meta(self) -> str:
        try:
            return json.dumps(self._loader.get_meta())
        except Exception:
            _log.exception("get_meta failed")
            return json.dumps({"systems": [], "years": [], "system_default": None})

    @Slot(result=str)
    def get_theme(self) -> str:
        """Return the current GUI theme as a colour dict.

        The dashboard mirrors the colors of the rest of the GUI (light
        on light themes, dark on dark themes). We export only the
        subset of ColorPalette the JS needs as CSS variables.
        """
        try:
            from esfex.visualization.theme import current_theme
            c = current_theme().colors
            payload = {
                "bg":           c.surface_primary,
                "bg_secondary": c.surface_secondary,
                "bg_elevated":  c.surface_elevated,
                "text":         c.text_primary,
                "text_muted":   c.text_secondary,
                "border":       c.border_light,
                "accent":       c.accent_primary,
                "accent2":      c.accent_secondary,
                "ok":           c.status_success,
                "warn":         c.status_warning,
                "err":          c.status_error,
                "selection":    c.selection_bg,
            }
        except Exception:
            _log.exception("get_theme failed; falling back to neutral palette")
            payload = {
                "bg": "#FFFFFF", "bg_secondary": "#F5F7FA",
                "bg_elevated": "#FFFFFF", "text": "#2C3E50",
                "text_muted": "#7F8C8D", "border": "#DEE2E6",
                "accent": "#2980B9", "accent2": "#27AE60",
                "ok": "#27AE60", "warn": "#F39C12",
                "err": "#E74C3C", "selection": "#D6EAF8",
            }
        return json.dumps(payload)

    @Slot(str, int, result=str)
    def get_year_detail(self, system: str, year: int) -> str:
        """Drill-down payload triggered by clicking a year on the trajectory."""
        try:
            return json.dumps(self._loader.get_year_detail(system, int(year)))
        except Exception:
            _log.exception("get_year_detail failed (system=%r, year=%s)", system, year)
            return json.dumps({"year": year, "kpis": {}, "dispatch": None})

    @Slot(str, result=str)
    def get_overview(self, state_json: str) -> str:
        """``state_json`` is ``{system, yearRange}`` from the JS state."""
        try:
            state = json.loads(state_json or "{}")
        except json.JSONDecodeError:
            state = {}
        system = state.get("system")
        year_range = state.get("yearRange")  # [min, max] or None
        if year_range is not None:
            try:
                year_range = (int(year_range[0]), int(year_range[1]))
            except (TypeError, ValueError, IndexError):
                year_range = None
        try:
            return json.dumps(self._loader.get_overview(system, year_range))
        except Exception:
            _log.exception("get_overview failed (state=%r)", state)
            return json.dumps({"kpis": {}, "trajectory": None, "mix": None})


class DashboardView(QWebEngineView):
    """Top-level widget — drop into a QTabWidget tab.

    The view is reusable: call :meth:`set_sources` whenever the host
    dialog rescans its results directory (e.g. a new run finished) so
    the dashboard reflects the latest files without recreating the
    widget.
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        # WebChannel + bridge wiring. The bridge starts pointing at an
        # empty loader; set_sources installs the real one.
        self._loader = DashboardLoader({}, {})
        self._bridge = _DashboardBridge(self._loader, self)
        self._channel = QWebChannel(self.page())
        self._channel.registerObject("loader", self._bridge)
        self.page().setWebChannel(self._channel)

        # Local resources side-loaded next to dashboard.html (plotly.js,
        # dashboard.js, etc.) need these flags to be loadable.
        settings = self.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True,
        )
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True,
        )

        # Load static page; subsequent updates flow through the bridge.
        self.load(QUrl.fromLocalFile(str(_RESOURCES_DIR / "dashboard.html")))

    # ── Public API ────────────────────────────────────────────────

    def set_sources(
        self,
        h5_files: dict[str, Path],
        base_prefix_by_system: dict[str, str],
    ) -> None:
        """Point the dashboard at a new set of result files.

        The bridge keeps a stable reference to the loader instance, so
        we mutate that loader in place rather than swapping it out
        (swapping would require re-registering on the QWebChannel and
        the JS holds a captured reference to the original bridge
        object).
        """
        self._loader._h5_files = dict(h5_files)
        self._loader._base_prefix = dict(base_prefix_by_system)
        # Ask the JS to re-bootstrap from the new meta. We do it via a
        # tiny JS snippet rather than a bridge signal because slots
        # are easier to instrument and don't require Signal wiring.
        self.page().runJavaScript(
            "if (typeof bootstrap === 'function') { bootstrap(); }"
        )

    def set_year_range(self, year_min: int | None, year_max: int | None) -> None:
        """Drive the dashboard's year filter from outside.

        Pass ``None, None`` to clear the filter (show every year).
        Mirrors :meth:`set_system` — the host's year slider is the
        canonical year control, so this propagates its value to the
        dashboard's internal state.
        """
        import json
        payload = (
            "null"
            if year_min is None or year_max is None
            else json.dumps([int(year_min), int(year_max)])
        )
        self.page().runJavaScript(
            "if (typeof setYearRange === 'function') "
            f"setYearRange({payload});"
        )

    def set_system(self, system_name: str) -> None:
        """Drive the dashboard's system selector from outside.

        The host dialog's sidebar carries the canonical system combo;
        without this hook the dashboard's own ``<select>`` would keep
        the previous selection and silently render the wrong system
        when the user navigates via the sidebar."""
        import json
        self.page().runJavaScript(
            "if (typeof setActiveSystem === 'function') "
            f"setActiveSystem({json.dumps(system_name)});"
        )
