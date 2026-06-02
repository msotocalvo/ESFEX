"""Python object exposed to JavaScript via QWebChannel for the SLD view.

This is a simplified bridge compared to MapBridge since the SLD is
view-only (no drawing, dragging, or polyline tracing).
"""

from PySide6.QtCore import QObject, Signal, Slot


class SldBridge(QObject):
    """Bridge between D3.js SLD view and Python GUI model.

    Exposed to JavaScript as ``window.sldBridge`` via QWebChannel.
    JS calls ``@Slot`` methods; Python emits ``Signal``s to propagate changes.
    """

    # Signals emitted toward the Python GUI
    sldReady = Signal()
    elementSelected = Signal(str, str)       # element_type, element_id
    elementHovered = Signal(str, str)        # element_type, element_id
    elementDeselected = Signal()
    svgExported = Signal(str)                # SVG markup string

    # ------------------------------------------------------------------
    # Slots callable from JavaScript
    # ------------------------------------------------------------------

    @Slot()
    def on_sld_ready(self):
        """Called once the SLD has finished layout and rendering."""
        self.sldReady.emit()

    @Slot(str, str)
    def on_element_selected(self, element_type: str, element_id: str):
        """Called when user clicks an element in the SLD."""
        self.elementSelected.emit(element_type, element_id)

    @Slot(str, str)
    def on_element_hovered(self, element_type: str, element_id: str):
        """Called when user hovers over an element in the SLD."""
        self.elementHovered.emit(element_type, element_id)

    @Slot()
    def on_element_deselected(self):
        """Called when user clicks the SLD background (deselect)."""
        self.elementDeselected.emit()

    @Slot(str)
    def on_svg_exported(self, svg_markup: str):
        """Called by JS with serialized SVG for export."""
        self.svgExported.emit(svg_markup)
