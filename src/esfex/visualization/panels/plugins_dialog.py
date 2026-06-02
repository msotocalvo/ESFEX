"""Plugin Manager dialog for the ESFEX Studio."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr

logger = logging.getLogger(__name__)


class PluginsDialog(QDialog):
    """Modal dialog for managing ESFEX plugins."""

    _COL_ENABLED = 0
    _COL_NAME = 1
    _COL_VERSION = 2
    _COL_CATEGORY = 3
    _COL_AUTHOR = 4
    _COL_DESCRIPTION = 5
    _NUM_COLS = 6

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("plugins_dialog.title"))
        self.setMinimumSize(800, 450)
        self.setModal(True)

        from esfex.plugins import get_plugin_manager
        self._pm = get_plugin_manager()

        self._build_ui()
        self._populate_table()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # Table
        self._table = QTableWidget(0, self._NUM_COLS, self)
        self._table.setHorizontalHeaderLabels([
            tr("plugins_dialog.enabled"),
            tr("plugins_dialog.name"),
            tr("plugins_dialog.version"),
            tr("plugins_dialog.category"),
            tr("plugins_dialog.author"),
            tr("plugins_dialog.description"),
        ])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(self._COL_DESCRIPTION, QHeaderView.ResizeMode.Stretch)
        for col in range(self._COL_ENABLED, self._COL_DESCRIPTION):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table)

        # Buttons row
        btn_layout = QHBoxLayout()

        self._btn_install_zip = QPushButton(tr("plugins_dialog.install_zip"))
        self._btn_install_zip.clicked.connect(self._on_install_zip)
        btn_layout.addWidget(self._btn_install_zip)

        self._btn_install_git = QPushButton(tr("plugins_dialog.install_git"))
        self._btn_install_git.clicked.connect(self._on_install_git)
        btn_layout.addWidget(self._btn_install_git)

        self._btn_uninstall = QPushButton(tr("plugins_dialog.uninstall"))
        self._btn_uninstall.setEnabled(False)
        self._btn_uninstall.clicked.connect(self._on_uninstall)
        btn_layout.addWidget(self._btn_uninstall)

        self._table.itemSelectionChanged.connect(self._on_selection_changed)

        btn_layout.addStretch()

        self._btn_open_folder = QPushButton(tr("plugins_dialog.open_folder"))
        self._btn_open_folder.clicked.connect(self._on_open_folder)
        btn_layout.addWidget(self._btn_open_folder)

        layout.addLayout(btn_layout)

        # OK / Cancel
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    # ------------------------------------------------------------------
    # Table population
    # ------------------------------------------------------------------

    def _populate_table(self) -> None:
        self._table.setRowCount(0)
        self._btn_uninstall.setEnabled(False)
        # Ensure discovery ran
        if not self._pm._discovered:
            self._pm.discover()

        names = sorted(self._pm._discovered.keys())
        if not names:
            self._table.setRowCount(1)
            item = QTableWidgetItem(tr("plugins_dialog.no_plugins"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._table.setItem(0, 0, item)
            self._table.setSpan(0, 0, 1, self._NUM_COLS)
            return

        self._table.setRowCount(len(names))
        for row, name in enumerate(names):
            meta = self._pm._metas.get(name)

            # Enabled checkbox
            chk = QCheckBox()
            chk.setChecked(self._pm.is_enabled(name))
            chk_widget = QWidget()
            chk_layout = QHBoxLayout(chk_widget)
            chk_layout.addWidget(chk)
            chk_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chk_layout.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(row, self._COL_ENABLED, chk_widget)

            # Text columns
            self._table.setItem(row, self._COL_NAME, self._ro_item(name))
            self._table.setItem(row, self._COL_VERSION, self._ro_item(meta.version if meta else ""))
            self._table.setItem(row, self._COL_CATEGORY, self._ro_item(meta.category if meta else ""))
            self._table.setItem(row, self._COL_AUTHOR, self._ro_item(meta.author if meta else ""))
            self._table.setItem(row, self._COL_DESCRIPTION, self._ro_item(meta.description if meta else ""))

    @staticmethod
    def _ro_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        return item

    # ------------------------------------------------------------------
    # Selection tracking
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        has_selection = bool(self._table.selectedItems())
        self._btn_uninstall.setEnabled(has_selection)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_install_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("plugins_dialog.install_zip"),
            "",
            "ZIP Files (*.zip)",
        )
        if not path:
            return
        try:
            installed = self._pm.install_from_zip(Path(path))
        except ValueError as exc:
            if "already exists" in str(exc):
                reply = QMessageBox.question(
                    self, tr("common.warning"), str(exc) + "\n\nOverwrite?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                try:
                    installed = self._pm.install_from_zip(Path(path), force=True)
                except Exception as exc2:
                    QMessageBox.critical(self, tr("common.error"), str(exc2))
                    return
            else:
                QMessageBox.critical(self, tr("common.error"), str(exc))
                return
        except Exception as exc:
            QMessageBox.critical(self, tr("common.error"), str(exc))
            return

        self._pm.discover()
        self._populate_table()
        self._hot_load_plugin(installed)
        QMessageBox.information(self, tr("common.info"), f"Installed: {installed}")

    def _on_install_git(self) -> None:
        url, ok = QInputDialog.getText(
            self,
            tr("plugins_dialog.install_git"),
            "Git URL:",
        )
        if not ok or not url.strip():
            return
        clean_url = url.strip()

        def _do_install(force: bool = False, trust_host: bool = False) -> str:
            return self._pm.install_from_git(
                clean_url, force=force, trust_host=trust_host,
            )

        def _prompt_untrusted_host(host: str) -> bool:
            reply = QMessageBox.question(
                self, tr("common.warning"),
                f"The git host {host!r} is not in the default trusted "
                "list (github, gitlab, bitbucket, codeberg, sourcehut). "
                "Installing from an attacker-controlled host runs the "
                "plugin's __init__.py with full process privileges.\n\n"
                "Proceed only if you trust this URL.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes

        try:
            installed = _do_install()
        except ValueError as exc:
            msg = str(exc)
            if "already exists" in msg:
                reply = QMessageBox.question(
                    self, tr("common.warning"), msg + "\n\nOverwrite?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                try:
                    installed = _do_install(force=True)
                except Exception as exc2:
                    QMessageBox.critical(self, tr("common.error"), str(exc2))
                    return
            elif "not in the default trusted git hosts" in msg:
                # Extract host from message and offer confirm.
                from urllib.parse import urlparse
                host = (urlparse(clean_url).hostname or "").lower()
                if not _prompt_untrusted_host(host):
                    return
                try:
                    installed = _do_install(trust_host=True)
                except Exception as exc2:
                    QMessageBox.critical(self, tr("common.error"), str(exc2))
                    return
            else:
                QMessageBox.critical(self, tr("common.error"), msg)
                return
        except Exception as exc:
            QMessageBox.critical(self, tr("common.error"), str(exc))
            return

        self._pm.discover()
        self._populate_table()
        self._hot_load_plugin(installed)
        QMessageBox.information(self, tr("common.info"), f"Installed: {installed}")

    def _on_uninstall(self) -> None:
        row = self._table.currentRow()
        name_item = self._table.item(row, self._COL_NAME)
        if name_item is None:
            return
        name = name_item.text()
        reply = QMessageBox.question(
            self,
            tr("plugins_dialog.confirm_uninstall_title"),
            tr("plugins_dialog.confirm_uninstall").replace("{name}", name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._pm.uninstall(name)
        self._pm.discover()
        self._populate_table()

    def _hot_load_plugin(self, name: str) -> None:
        """Load a just-installed plugin and register its GUI extensions."""
        window = self.parent()
        plugin = self._pm.load_single(name, config=None, gui_mode=True)
        if plugin is None:
            logger.warning("Could not hot-load plugin %r", name)
            return
        # Register only this plugin's GUI extensions on the main window
        self._pm._register_translations(plugin)
        self._pm._register_tree_categories(plugin, window)
        self._pm._register_forms(plugin, window)
        self._pm._register_toolbar(plugin, window)
        self._pm._register_menu(plugin, window)
        self._pm._register_results(plugin, window)
        self._pm._register_map_layers(plugin, window)

    def _on_open_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from esfex.plugins.manager import _USER_PLUGINS_DIR

        _USER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(_USER_PLUGINS_DIR)))

    def _on_accept(self) -> None:
        # Persist enable/disable changes and detect if a restart is needed
        needs_restart = False
        names = sorted(self._pm._discovered.keys())
        for row, name in enumerate(names):
            widget = self._table.cellWidget(row, self._COL_ENABLED)
            if widget is None:
                continue
            chk = widget.findChild(QCheckBox)
            if chk is None:
                continue
            was_enabled = self._pm.is_enabled(name)
            now_enabled = chk.isChecked()
            if now_enabled and not was_enabled:
                # Enabling: hot-load if possible
                self._pm.enable(name)
                self._hot_load_plugin(name)
            elif not now_enabled and was_enabled:
                # Disabling a loaded plugin requires restart
                self._pm.disable(name)
                needs_restart = True
            # No change: do nothing

        if needs_restart:
            QMessageBox.information(
                self,
                tr("common.info"),
                tr("plugins_dialog.restart_notice"),
            )
        self.accept()
