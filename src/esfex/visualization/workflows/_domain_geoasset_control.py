"""Reusable control: pick an imported GeoAsset polygon as a workflow domain.

Dropped into any workflow domain step. It lists imported GeoAssets that contain
polygons (from a ``geo_assets_provider`` callable) and emits the dissolved
boundary when the user applies one. It hides itself when there are no
polygon-bearing assets, so it costs nothing until a usable asset is imported.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
)

from esfex.visualization.i18n import tr
from esfex.visualization.workflows.geo_domain import (
    geoasset_to_domain_polygon,
    has_polygon,
)


class GeoAssetDomainControl(QGroupBox):
    """Emits ``domainPicked(list[(lat, lng)])`` — the dissolved GeoAsset domain."""

    domainPicked = Signal(list)

    def __init__(self, geo_assets_provider=None, parent=None):
        super().__init__(tr("geo_domain.group"), parent)
        self._provider = geo_assets_provider

        row = QHBoxLayout(self)
        self._combo = QComboBox()
        self._combo.setMinimumWidth(160)
        row.addWidget(self._combo, 1)
        self._btn = QPushButton(tr("geo_domain.apply"))
        self._btn.clicked.connect(self._on_apply)
        row.addWidget(self._btn)

        self.refresh()

    def _assets(self) -> dict:
        if self._provider is None:
            return {}
        try:
            return self._provider() or {}
        except Exception:
            return {}

    def refresh(self):
        """Repopulate from the provider; hide when no polygon assets exist."""
        self._combo.blockSignals(True)
        self._combo.clear()
        count = 0
        for asset_id, info in self._assets().items():
            gj = getattr(info, "geojson_data", None)
            if gj and has_polygon(gj):
                self._combo.addItem(getattr(info, "name", asset_id), asset_id)
                count += 1
        self._combo.blockSignals(False)
        self.setVisible(count > 0)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _on_apply(self):
        asset_id = self._combo.currentData()
        info = self._assets().get(asset_id) if asset_id else None
        if info is None:
            return
        poly = geoasset_to_domain_polygon(info.geojson_data)
        if not poly:
            QMessageBox.warning(
                self, tr("common.warning"), tr("geo_domain.no_polygon"))
            return
        self.domainPicked.emit(poly)
