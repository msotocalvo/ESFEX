"""QWebEngineView wrapper that hosts the D3.js + ELK.js single-line diagram."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from esfex.visualization.bridge.channel import setup_sld_channel
from esfex.visualization.bridge.sld_bridge import SldBridge

_RESOURCES_DIR = Path(__file__).parent / "resources"


class SldWidget(QWebEngineView):
    """Interactive single-line diagram based on D3.js + ELK.js."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bridge: SldBridge = setup_sld_channel(self)

        settings = self.page().settings()
        settings.setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
        )

        # Disable HTTP cache so local JS/HTML changes take effect immediately
        profile = self.page().profile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)

        html_path = _RESOURCES_DIR / "sld.html"
        self.load(QUrl.fromLocalFile(str(html_path)))

    # ------------------------------------------------------------------
    # Python -> JavaScript helpers
    # ------------------------------------------------------------------

    def _run_js(self, script: str):
        self.page().runJavaScript(script)

    def render_graph(self, elk_graph_json: str):
        """Send ELK JSON graph to the SLD for layout and rendering.

        Parameters
        ----------
        elk_graph_json : str
            Serialized ELK JSON graph from ``build_elk_graph()``.
        """
        escaped = elk_graph_json.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        self._run_js(f"layoutAndRender('{escaped}')")

    def select_element(self, element_type: str, element_id: str):
        """Highlight an element in the SLD (called from tree selection)."""
        self._run_js(f"highlightElement('{element_type}', '{element_id}')")

    def clear_selection(self):
        """Remove current selection highlight."""
        self._run_js("clearSelection()")

    def fit_view(self):
        """Zoom to fit all diagram content."""
        self._run_js("fitView()")

    def toggle_labels(self, show: bool):
        """Show or hide data labels (MW, kV, etc.)."""
        self._run_js(f"toggleLabels({'true' if show else 'false'})")

    def update_theme(self, colors_json: str):
        """Update SLD theme colors."""
        escaped = colors_json.replace("\\", "\\\\").replace("'", "\\'")
        self._run_js(f"updateTheme('{escaped}')")

    def update_operational_data(self, snapshot_json: str):
        """Send an operational snapshot to the SLD overlay layer.

        Parameters
        ----------
        snapshot_json : str
            JSON string from ``SldResultsLoader.get_timestep()``.
        """
        escaped = snapshot_json.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        self._run_js(f"updateOperationalData('{escaped}')")

    def clear_operational_data(self):
        """Remove the operational overlay from the SLD."""
        self._run_js("clearOperationalData()")

    def update_contingency_data(self, contingency_json: str):
        """Send contingency analysis results to the SLD overlay.

        Parameters
        ----------
        contingency_json : str
            JSON string from ``ContingencyResult`` (via ``dataclasses.asdict``).
        """
        escaped = contingency_json.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
        self._run_js(f"updateContingencyData('{escaped}')")

    def clear_contingency_data(self):
        """Remove the contingency overlay from the SLD."""
        self._run_js("clearContingencyData()")

    def export_svg(self):
        """Request the JS side to serialize the SVG and send it back via bridge."""
        self._run_js("exportSvg()")
