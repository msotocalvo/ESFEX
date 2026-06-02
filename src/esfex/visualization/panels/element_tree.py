"""Element tree panel showing all power system components."""

import logging

from PySide6.QtCore import Qt, QTimer, Signal

logger = logging.getLogger(__name__)
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from esfex.visualization.i18n import tr
from esfex.visualization.theme import get_tree_category_color

# Ordered list of category keys (labels resolved lazily via tr()).
_CATEGORY_KEYS = [
    "nodes", "generators", "batteries", "lines", "transformers",
    "zones", "fuel_entries", "fuel_sources", "fuel_storages",
    "fuel_routes", "fuels", "electrolyzers", "ev_config",
    "rooftop_solar", "buses", "acdc_converters", "freq_converters",
    "technologies", "investment_portfolio", "risk_scenarios",
]


def _category_label(cat_key: str) -> str:
    """Return the translated display label for a category key."""
    return tr(f"tree.{cat_key}")

# element_type (used in signals) -> category key (used in _system_roots)
_ELEMENT_TYPE_TO_CATEGORY = {
    "node": "nodes",
    "generator": "generators",
    "battery": "batteries",
    "line": "lines",
    "transformer": "transformers",
    "zone": "zones",
    "fuel_entry": "fuel_entries",
    "fuel_source": "fuel_sources",
    "fuel_storage": "fuel_storages",
    "fuel_route": "fuel_routes",
    "fuel": "fuels",
    "electrolyzer": "electrolyzers",
    "ev_config": "ev_config",
    "rooftop_solar": "rooftop_solar",
    "bus": "buses",
    "acdc_converter": "acdc_converters",
    "freq_converter": "freq_converters",
    "technology": "technologies",
    "investment_entry": "investment_portfolio",
    "risk_scenario": "risk_scenarios",
    "system_settings": "system_settings",
    "global_settings": "global_settings",
    "stochastic": "stochastic",
}

# Element types whose IDs are list indices (int-as-string).
# When deleting multiple, higher indices must be removed first.
_INDEX_BASED_TYPES = {
    "transformer", "fuel_entry", "node", "zone",
    "acdc_converter", "freq_converter",
}


def _sort_deletes_reverse(
    items: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Sort *items* so index-based element types come last, in descending index.

    This prevents index-shift corruption when deleting multiple elements
    from a list by index.  Non-index-based types are emitted first (order
    preserved), then index-based types in descending order.
    """
    non_idx = [(t, e) for t, e in items if t not in _INDEX_BASED_TYPES]
    idx_items = [(t, e) for t, e in items if t in _INDEX_BASED_TYPES]
    # Sort descending by numeric id so highest index is deleted first
    idx_items.sort(key=lambda x: int(x[1]), reverse=True)
    return non_idx + idx_items


class ElementTreePanel(QWidget):
    """Hierarchical tree view of all system elements."""

    elementSelected = Signal(str, str)  # element_type, element_id
    elementFocused = Signal(str, str)   # element_type, element_id (double-click)
    systemSwitchRequested = Signal(str)  # system_name
    deleteRequested = Signal(str, str)  # element_type, element_id
    batchDeleteRequested = Signal(list)  # [(element_type, element_id), ...]
    duplicateRequested = Signal(str, str)  # element_type, element_id
    copyRequested = Signal(str, str)     # element_type, element_id
    pasteRequested = Signal(str, str)    # element_type, element_id
    addNodeRequested = Signal(str)       # system_name
    addFuelRequested = Signal(str)       # system_name
    addTechnologyRequested = Signal(str)  # system_name
    addInvestmentRequested = Signal(str, str)  # system_name, technology_type
    multiElementSelected = Signal(str, list)  # element_type, [element_ids]
    deleteSystemRequested = Signal(str)  # system_name

    # Element types that support deletion from the tree
    _DELETABLE_TYPES = {
        "node", "generator", "battery", "line", "transformer", "zone",
        "bus", "fuel_entry", "fuel_source", "fuel_storage", "fuel_route", "fuel",
        "electrolyzer", "acdc_converter", "freq_converter",
        "technology", "investment_entry", "inter_system_link", "geo_asset",
    }

    # Element types that support duplication
    _DUPLICABLE_TYPES = {
        "node", "generator", "battery", "line", "transformer",
        "bus", "fuel_entry", "fuel_source", "fuel_storage", "fuel",
        "electrolyzer", "acdc_converter", "freq_converter",
        "technology", "investment_entry",
    }

    # Element types that support copy/paste of attributes
    _COPYABLE_TYPES = {
        "generator", "battery", "line", "transformer",
        "bus", "fuel_entry", "fuel_source", "fuel_storage", "fuel",
        "electrolyzer", "acdc_converter", "freq_converter",
        "technology", "investment_entry",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(200)
        self._clipboard_type: str = ""  # element type in clipboard (for paste enable)
        self._batch_mode: bool = False  # suppress _update_count during bulk loading

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Search bar
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText(tr("tree.search_placeholder"))
        self._search_box.setClearButtonEnabled(True)
        self._search_box.setObjectName("searchBox")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._on_search_debounced)
        self._search_box.textChanged.connect(self._on_search_changed)
        layout.addWidget(self._search_box)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("tree.header_element"), tr("tree.header_info")])
        self.tree.setColumnWidth(0, 180)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        layout.addWidget(self.tree)

        # Delete button
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(4, 2, 4, 2)
        self._delete_btn = QPushButton(tr("common.delete"))
        self._delete_btn.setEnabled(False)
        self._delete_btn.setObjectName("deleteButton")
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        btn_row.addStretch()
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Per-system storage
        self._system_items: dict[str, QTreeWidgetItem] = {}
        self._system_roots: dict[str, dict[str, QTreeWidgetItem]] = {}
        self._current_system: str = ""

        # Global settings (permanent, not per-system)
        self._global_settings_item = QTreeWidgetItem(
            self.tree, [tr("tree.global_settings"), ""],
        )
        self._global_settings_item.setData(0, 100, ("global_settings", "global_settings"))
        font_gs = self._global_settings_item.font(0)
        font_gs.setBold(True)
        self._global_settings_item.setFont(0, font_gs)
        _gs_color = get_tree_category_color("global_settings")
        if _gs_color:
            self._global_settings_item.setForeground(0, QBrush(QColor(_gs_color)))

        self._stochastic_item = QTreeWidgetItem(
            self.tree, [tr("tree.scenarios"), ""],
        )
        self._stochastic_item.setData(0, 100, ("stochastic", "stochastic"))
        font_st = self._stochastic_item.font(0)
        font_st.setBold(True)
        self._stochastic_item.setFont(0, font_st)
        _sc_color = get_tree_category_color("stochastic")
        if _sc_color:
            self._stochastic_item.setForeground(0, QBrush(QColor(_sc_color)))

        # Inter-system links (permanent, not per-system)
        self._islinks_root = QTreeWidgetItem(self.tree, [tr("tree.inter_system_links"), ""])
        self._islinks_root.setData(0, 100, None)  # no click action
        font = self._islinks_root.font(0)
        font.setBold(True)
        self._islinks_root.setFont(0, font)
        _isl_color = get_tree_category_color("inter_system_links")
        if _isl_color:
            self._islinks_root.setForeground(0, QBrush(QColor(_isl_color)))
        self._islink_transmission_root = QTreeWidgetItem(
            self._islinks_root, [f"{tr('tree.transmission_lines')} (0)", ""],
        )
        self._islink_fuel_root = QTreeWidgetItem(
            self._islinks_root, [f"{tr('tree.fuel_routes')} (0)", ""],
        )
        self._islink_transmission_root.setExpanded(True)
        self._islink_fuel_root.setExpanded(True)
        self._islinks_root.setExpanded(True)

        # Geo Assets (permanent, not per-system, reference overlays)
        self._geo_assets_root = QTreeWidgetItem(self.tree, [f"{tr('tree.geo_assets')} (0)", ""])
        self._geo_assets_root.setData(0, 100, None)
        font_ga = self._geo_assets_root.font(0)
        font_ga.setBold(True)
        self._geo_assets_root.setFont(0, font_ga)
        _ga_color = get_tree_category_color("geo_assets")
        if _ga_color:
            self._geo_assets_root.setForeground(0, QBrush(QColor(_ga_color)))
        self._geo_assets_root.setExpanded(True)

        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.itemChanged.connect(self._on_item_changed)

    # ------------------------------------------------------------------
    # System management
    # ------------------------------------------------------------------

    def add_system(self, name: str):
        """Add a new system as a top-level tree node with category children."""
        if name in self._system_items:
            return

        sys_item = QTreeWidgetItem(self.tree, [name, "system"])
        sys_item.setData(0, 100, ("system", name))
        font = sys_item.font(0)
        font.setBold(True)
        sys_item.setFont(0, font)
        sys_color = get_tree_category_color("system")
        if sys_color:
            sys_item.setForeground(0, QBrush(QColor(sys_color)))

        # Categories that are directly clickable at root level
        _CLICKABLE_CATEGORIES = {
            "nodes": ("nodes_cat", "__nodes__"),
            "fuels": ("fuels_cat", "__fuels__"),
            "ev_config": ("ev_config", "ev_config"),
            "rooftop_solar": ("rooftop_solar", "rooftop_solar"),
            "technologies": ("technologies_cat", "__technologies__"),
            "investment_portfolio": ("investment_portfolio_cat", "__inv_portfolio__"),
        }
        # Pure singletons have no children → no count display
        _PURE_SINGLETONS = {"ev_config", "rooftop_solar"}

        roots: dict[str, QTreeWidgetItem] = {}
        for cat_key in _CATEGORY_KEYS:
            cat_label = _category_label(cat_key)
            cat_item = QTreeWidgetItem(sys_item, [f"{cat_label} (0)", ""])
            cat_item.setExpanded(True)
            if cat_key in _CLICKABLE_CATEGORIES:
                cat_item.setData(0, 100, _CLICKABLE_CATEGORIES[cat_key])
            if cat_key in _PURE_SINGLETONS:
                cat_item.setText(0, cat_label)  # No count for singletons
            # Apply per-category color from theme
            cat_color = get_tree_category_color(cat_key)
            if cat_color:
                cat_item.setForeground(0, QBrush(QColor(cat_color)))
                cat_font = cat_item.font(0)
                cat_font.setBold(True)
                cat_item.setFont(0, cat_font)
            roots[cat_key] = cat_item

        self._system_items[name] = sys_item
        self._system_roots[name] = roots
        sys_item.setExpanded(True)

    def remove_system(self, name: str):
        """Remove a system and all its children from the tree."""
        sys_item = self._system_items.pop(name, None)
        if sys_item:
            idx = self.tree.indexOfTopLevelItem(sys_item)
            if idx >= 0:
                self.tree.takeTopLevelItem(idx)
            self._system_roots.pop(name, None)
            if self._current_system == name:
                self._current_system = ""

    def rename_system(self, old_name: str, new_name: str):
        """Rename a system in the tree."""
        sys_item = self._system_items.pop(old_name, None)
        if sys_item is None:
            return
        sys_item.setText(0, new_name)
        font = sys_item.font(0)
        font.setBold(True)
        sys_item.setFont(0, font)
        self._system_items[new_name] = sys_item
        self._system_roots[new_name] = self._system_roots.pop(old_name, {})
        if self._current_system == old_name:
            self._current_system = new_name

    def set_current_system(self, name: str):
        """Set which system is visually active.

        All systems remain expanded — the active system is just tracked
        internally for context menu operations.
        """
        self._current_system = name

    def register_plugin_category(
        self, key: str, label: str, element_type: str
    ) -> None:
        """Register a new category from a plugin.

        Adds the category to the module-level registries and creates tree
        nodes in every existing system.
        """
        global _CATEGORY_KEYS
        if key not in _CATEGORY_KEYS:
            _CATEGORY_KEYS.append(key)
        if element_type not in _ELEMENT_TYPE_TO_CATEGORY:
            _ELEMENT_TYPE_TO_CATEGORY[element_type] = key

        # Create nodes in every existing system
        for sys_name, roots in self._system_roots.items():
            if key in roots:
                continue
            sys_item = self._system_items.get(sys_name)
            if sys_item is None:
                continue
            cat_item = QTreeWidgetItem(sys_item, [f"{label} (0)", ""])
            cat_item.setExpanded(True)
            roots[key] = cat_item

    # ------------------------------------------------------------------
    # Public API — elements
    # ------------------------------------------------------------------

    def add_node(self, node_id: int, label: str, info: str = ""):
        root = self._get_root("nodes")
        if root is None:
            return
        item = QTreeWidgetItem(root, [label, info])
        item.setData(0, 100, ("node", str(node_id)))
        self._update_count(root)

    def remove_node(self, node_id: int):
        root = self._get_root("nodes")
        if root is None:
            return
        self._remove_child(root, "node", str(node_id))
        self._update_count(root)

    # --- Buses ---

    def add_bus(self, bus_id: str, name: str, info: str = ""):
        root = self._get_root("buses")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("bus", bus_id))
        self._update_count(root)

    def remove_bus(self, bus_id: str):
        root = self._get_root("buses")
        if root is None:
            return
        self._remove_child(root, "bus", bus_id)
        self._update_count(root)

    def update_bus(self, bus_id: str, name: str, info: str = ""):
        root = self._get_root("buses")
        if root is None:
            return
        self._update_child(root, "bus", bus_id, name, info)

    def add_generator(self, gen_key: str, name: str, info: str = ""):
        root = self._get_root("generators")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("generator", gen_key))
        self._update_count(root)

    def remove_generator(self, gen_key: str):
        root = self._get_root("generators")
        if root is None:
            return
        self._remove_child(root, "generator", gen_key)
        self._update_count(root)

    def add_battery(self, bat_key: str, name: str, info: str = ""):
        root = self._get_root("batteries")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("battery", bat_key))
        self._update_count(root)

    def remove_battery(self, bat_key: str):
        root = self._get_root("batteries")
        if root is None:
            return
        self._remove_child(root, "battery", bat_key)
        self._update_count(root)

    def add_line(self, line_id: str, label: str, info: str = ""):
        root = self._get_root("lines")
        if root is None:
            return
        item = QTreeWidgetItem(root, [label, info])
        item.setData(0, 100, ("line", line_id))
        self._update_count(root)

    def remove_line(self, line_id: str):
        root = self._get_root("lines")
        if root is None:
            return
        self._remove_child(root, "line", line_id)
        self._update_count(root)

    def add_zone(self, zone_id: str, name: str, info: str = ""):
        root = self._get_root("zones")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("zone", zone_id))
        self._update_count(root)

    def update_zone(self, zone_id: str, name: str, info: str = ""):
        root = self._get_root("zones")
        if root is None:
            return
        self._update_child(root, "zone", zone_id, name, info)

    def remove_zone(self, zone_id: str):
        root = self._get_root("zones")
        if root is None:
            return
        self._remove_child(root, "zone", zone_id)
        self._update_count(root)

    def add_fuel_entry(self, entry_id: str, name: str, info: str = ""):
        root = self._get_root("fuel_entries")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("fuel_entry", entry_id))
        self._update_count(root)

    def remove_fuel_entry(self, entry_id: str):
        root = self._get_root("fuel_entries")
        if root is None:
            return
        self._remove_child(root, "fuel_entry", entry_id)
        self._update_count(root)

    def update_fuel_entry(self, entry_id: str, name: str, info: str = ""):
        root = self._get_root("fuel_entries")
        if root is None:
            return
        self._update_child(root, "fuel_entry", entry_id, name, info)

    def add_transformer(self, tr_id: str, name: str, info: str = ""):
        root = self._get_root("transformers")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("transformer", tr_id))
        self._update_count(root)

    def remove_transformer(self, tr_id: str):
        root = self._get_root("transformers")
        if root is None:
            return
        self._remove_child(root, "transformer", tr_id)
        self._update_count(root)

    def update_transformer(self, tr_id: str, name: str, info: str = ""):
        root = self._get_root("transformers")
        if root is None:
            return
        self._update_child(root, "transformer", tr_id, name, info)

    def add_fuel_source(self, source_id: str, name: str, info: str = ""):
        root = self._get_root("fuel_sources")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("fuel_source", source_id))
        self._update_count(root)

    def remove_fuel_source(self, source_id: str):
        root = self._get_root("fuel_sources")
        if root is None:
            return
        self._remove_child(root, "fuel_source", source_id)
        self._update_count(root)

    def update_fuel_source(self, source_id: str, name: str, info: str = ""):
        root = self._get_root("fuel_sources")
        if root is None:
            return
        self._update_child(root, "fuel_source", source_id, name, info)

    def add_fuel_storage(self, storage_id: str, name: str, info: str = ""):
        root = self._get_root("fuel_storages")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("fuel_storage", storage_id))
        self._update_count(root)

    def remove_fuel_storage(self, storage_id: str):
        root = self._get_root("fuel_storages")
        if root is None:
            return
        self._remove_child(root, "fuel_storage", storage_id)
        self._update_count(root)

    def update_fuel_storage(self, storage_id: str, name: str, info: str = ""):
        root = self._get_root("fuel_storages")
        if root is None:
            return
        self._update_child(root, "fuel_storage", storage_id, name, info)

    def add_fuel_route(self, route_id: str, label: str, info: str = ""):
        root = self._get_root("fuel_routes")
        if root is None:
            return
        item = QTreeWidgetItem(root, [label, info])
        item.setData(0, 100, ("fuel_route", route_id))
        self._update_count(root)

    def remove_fuel_route(self, route_id: str):
        root = self._get_root("fuel_routes")
        if root is None:
            return
        self._remove_child(root, "fuel_route", route_id)
        self._update_count(root)

    def update_fuel_route(self, route_id: str, label: str, info: str = ""):
        root = self._get_root("fuel_routes")
        if root is None:
            return
        self._update_child(root, "fuel_route", route_id, label, info)

    # --- Fuels ---

    def add_fuel(self, fuel_id: str, name: str, info: str = ""):
        root = self._get_root("fuels")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("fuel", fuel_id))
        self._update_count(root)

    def remove_fuel(self, fuel_id: str):
        root = self._get_root("fuels")
        if root is None:
            return
        self._remove_child(root, "fuel", fuel_id)
        self._update_count(root)

    def update_fuel(self, fuel_id: str, name: str, info: str = ""):
        root = self._get_root("fuels")
        if root is None:
            return
        self._update_child(root, "fuel", fuel_id, name, info)

    # --- Electrolyzers ---

    def add_electrolyzer(self, el_id: str, name: str, info: str = ""):
        root = self._get_root("electrolyzers")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("electrolyzer", el_id))
        self._update_count(root)

    def remove_electrolyzer(self, el_id: str):
        root = self._get_root("electrolyzers")
        if root is None:
            return
        self._remove_child(root, "electrolyzer", el_id)
        self._update_count(root)

    def update_electrolyzer(self, el_id: str, name: str, info: str = ""):
        root = self._get_root("electrolyzers")
        if root is None:
            return
        self._update_child(root, "electrolyzer", el_id, name, info)

    # --- AC/DC Converters ---

    def add_acdc_converter(self, conv_id: str, name: str, info: str = ""):
        root = self._get_root("acdc_converters")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("acdc_converter", conv_id))
        self._update_count(root)

    def remove_acdc_converter(self, conv_id: str):
        root = self._get_root("acdc_converters")
        if root is None:
            return
        self._remove_child(root, "acdc_converter", conv_id)
        self._update_count(root)

    def update_acdc_converter(self, conv_id: str, name: str, info: str = ""):
        root = self._get_root("acdc_converters")
        if root is None:
            return
        self._update_child(root, "acdc_converter", conv_id, name, info)

    # --- Frequency Converters ---

    def add_freq_converter(self, conv_id: str, name: str, info: str = ""):
        root = self._get_root("freq_converters")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("freq_converter", conv_id))
        self._update_count(root)

    def remove_freq_converter(self, conv_id: str):
        root = self._get_root("freq_converters")
        if root is None:
            return
        self._remove_child(root, "freq_converter", conv_id)
        self._update_count(root)

    def update_freq_converter(self, conv_id: str, name: str, info: str = ""):
        root = self._get_root("freq_converters")
        if root is None:
            return
        self._update_child(root, "freq_converter", conv_id, name, info)

    # --- Technologies ---

    def add_technology(self, tech_id: str, name: str, info: str = ""):
        root = self._get_root("technologies")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("technology", tech_id))
        self._update_count(root)

    def remove_technology(self, tech_id: str):
        root = self._get_root("technologies")
        if root is None:
            return
        self._remove_child(root, "technology", tech_id)
        self._update_count(root)

    def update_technology(self, tech_id: str, name: str, info: str = ""):
        root = self._get_root("technologies")
        if root is None:
            return
        self._update_child(root, "technology", tech_id, name, info)

    # --- Investment Portfolio ---

    def add_investment_entry(self, entry_id: str, name: str, info: str = ""):
        root = self._get_root("investment_portfolio")
        if root is None:
            return
        item = QTreeWidgetItem(root, [name, info])
        item.setData(0, 100, ("investment_entry", entry_id))
        self._update_count(root)

    def remove_investment_entry(self, entry_id: str):
        root = self._get_root("investment_portfolio")
        if root is None:
            return
        self._remove_child(root, "investment_entry", entry_id)
        self._update_count(root)

    def update_investment_entry(self, entry_id: str, name: str, info: str = ""):
        root = self._get_root("investment_portfolio")
        if root is None:
            return
        self._update_child(root, "investment_entry", entry_id, name, info)

    def update_generator(self, gen_key: str, name: str, info: str = ""):
        root = self._get_root("generators")
        if root is None:
            return
        self._update_child(root, "generator", gen_key, name, info)

    def update_battery(self, bat_key: str, name: str, info: str = ""):
        root = self._get_root("batteries")
        if root is None:
            return
        self._update_child(root, "battery", bat_key, name, info)

    def update_line(self, line_id: str, label: str, info: str = ""):
        root = self._get_root("lines")
        if root is None:
            return
        self._update_child(root, "line", line_id, label, info)

    def select_element(self, element_type: str, element_id: str):
        """Programmatically select an element in the tree."""
        cat_key = _ELEMENT_TYPE_TO_CATEGORY.get(element_type)
        root = self._get_root(cat_key) if cat_key else None
        if not root:
            return
        for i in range(root.childCount()):
            child = root.child(i)
            data = child.data(0, 100)
            if data and data[0] == element_type and data[1] == element_id:
                self.tree.setCurrentItem(child)
                return

    def breadcrumb(self, element_type: str, element_id: str) -> str:
        """Return a hierarchical path string for the given element.

        Example: ``"Isla de la Juventud  >  Generators  >  Diesel Unit 1"``
        """
        cat_key = _ELEMENT_TYPE_TO_CATEGORY.get(element_type)
        if cat_key == "global_settings":
            return tr("tree.global_settings")
        if cat_key == "stochastic":
            return tr("tree.scenarios")
        root = self._get_root(cat_key) if cat_key else None
        if not root:
            return ""
        # Find child item
        for i in range(root.childCount()):
            child = root.child(i)
            data = child.data(0, 100)
            if data and data[0] == element_type and data[1] == element_id:
                parts: list[str] = [child.text(0)]
                parent = child.parent()
                while parent is not None:
                    # Strip count suffix e.g. "Generators (5)" → "Generators"
                    label = parent.text(0).split(" (")[0]
                    parts.append(label)
                    parent = parent.parent()
                parts.reverse()
                return "  \u203a  ".join(parts)
        # Fallback: system > category
        system = self._current_system or ""
        cat_label = _category_label(cat_key)
        return f"{system}  \u203a  {cat_label}" if system else cat_label

    def clear_all(self):
        """Clear all element children for the current system's categories."""
        roots = self._system_roots.get(self._current_system)
        if not roots:
            return
        for root in roots.values():
            root.takeChildren()
            self._update_count(root)

    # ------------------------------------------------------------------
    # Inter-system links
    # ------------------------------------------------------------------

    def add_inter_system_link(
        self, link_id: str, link_type: str, label: str, info: str = "",
    ):
        root = (
            self._islink_transmission_root
            if link_type == "transmission"
            else self._islink_fuel_root
        )
        item = QTreeWidgetItem(root, [label, info])
        item.setData(0, 100, ("inter_system_link", link_id))
        self._update_count(root)

    def remove_inter_system_link(self, link_id: str):
        for root in (self._islink_transmission_root, self._islink_fuel_root):
            self._remove_child(root, "inter_system_link", link_id)
            self._update_count(root)

    def update_inter_system_link(self, link_id: str, label: str, info: str = ""):
        for root in (self._islink_transmission_root, self._islink_fuel_root):
            self._update_child(root, "inter_system_link", link_id, label, info)

    def clear_inter_system_links(self):
        """Clear all inter-system link items."""
        for root in (self._islink_transmission_root, self._islink_fuel_root):
            root.takeChildren()
            self._update_count(root)

    # ------------------------------------------------------------------
    # Geo Assets (reference overlays)
    # ------------------------------------------------------------------

    geoAssetVisibilityChanged = Signal(str, bool)  # asset_id, visible
    parseGeoAssetRequested = Signal(str)  # asset_id

    def add_geo_asset(self, asset_id: str, name: str, info: str = ""):
        item = QTreeWidgetItem(self._geo_assets_root, [name, info])
        item.setData(0, 100, ("geo_asset", asset_id))
        item.setCheckState(0, Qt.CheckState.Checked)
        self._update_count(self._geo_assets_root)

    def remove_geo_asset(self, asset_id: str):
        self._remove_child(self._geo_assets_root, "geo_asset", asset_id)
        self._update_count(self._geo_assets_root)

    def update_geo_asset_info(self, asset_id: str, info: str):
        """Update the info column of a geo asset item."""
        for i in range(self._geo_assets_root.childCount()):
            child = self._geo_assets_root.child(i)
            data = child.data(0, 100)
            if data and data[0] == "geo_asset" and data[1] == asset_id:
                child.setText(1, info)
                return

    def contextMenuEvent(self, event):
        """Right-click context menu for tree items."""
        selected = self.tree.selectedItems()

        # If the right-click happened over an item that is itself part of a
        # multi-selection, treat the operation as batch — even if Qt reduced
        # the selection to one item between the press and the menu (some
        # styles do this).
        pos = self.tree.viewport().mapFrom(self, event.pos())
        clicked_item = self.tree.itemAt(pos)
        if clicked_item is not None and clicked_item not in selected:
            # Right-clicking outside the current multi-selection: keep the
            # full selection if the clicked item is of the same deletable
            # type, otherwise let the single-item menu handle it.
            d = clicked_item.data(0, 100)
            if (
                selected
                and d
                and d[0] in self._DELETABLE_TYPES
                and all(
                    (it.data(0, 100) or (None,))[0] == d[0]
                    for it in selected
                )
            ):
                clicked_item.setSelected(True)
                selected = self.tree.selectedItems()

        # ── Batch context menu when multiple items are selected ──
        if len(selected) > 1:
            types_ids = []
            for it in selected:
                d = it.data(0, 100)
                if d:
                    types_ids.append((d[0], d[1]))
            logger.debug(
                "contextMenuEvent multi: selected=%d, types_ids=%s",
                len(selected), types_ids,
            )
            if types_ids and all(t in self._DELETABLE_TYPES for t, _ in types_ids):
                # Items can live under different systems (the tree shows
                # all systems as top-level subtrees). The active model
                # state only matches the current system, so we must
                # switch before emitting deletes for items owned by
                # another system. Reject mixed-system selections — we
                # cannot reasonably batch-delete across systems in one
                # action without surprising the user.
                owning_systems = {self._system_for_item(it) for it in selected}
                # None entries (geo_assets / inter-system links) live
                # outside per-system subtrees and don't need a switch.
                non_global = {s for s in owning_systems if s is not None}
                if len(non_global) > 1:
                    logger.warning(
                        "Batch delete spans multiple systems %s — aborting",
                        non_global,
                    )
                    return
                target_system = next(iter(non_global), None)
                menu = QMenu(self)
                del_action = menu.addAction(tr("tree_ctx.delete_n_items", n=len(types_ids)))
                action = menu.exec(event.globalPos())
                if action == del_action:
                    sorted_items = _sort_deletes_reverse(types_ids)
                    logger.info(
                        "Batch delete requested for %d items in system %r: %s",
                        len(sorted_items), target_system, sorted_items,
                    )
                    if target_system and target_system != self._current_system:
                        self.systemSwitchRequested.emit(target_system)
                    if len(sorted_items) == 1:
                        self.deleteRequested.emit(sorted_items[0][0], sorted_items[0][1])
                    else:
                        self.batchDeleteRequested.emit(sorted_items)
            return

        pos = self.tree.viewport().mapFrom(self, event.pos())
        item = self.tree.itemAt(pos)
        if not item:
            return
        data = item.data(0, 100)
        if not data:
            return

        etype, eid = data[0], data[1]
        menu = QMenu(self)

        # System-level context menu
        if etype == "system":
            delete_sys_action = menu.addAction(tr("tree_ctx.delete_system"))
            action = menu.exec(event.globalPos())
            if action == delete_sys_action:
                self.deleteSystemRequested.emit(eid)
            return

        # Nodes category — add node
        if etype == "nodes_cat":
            add_node_action = menu.addAction(tr("tree_ctx.add_node"))
            action = menu.exec(event.globalPos())
            if action == add_node_action:
                self.addNodeRequested.emit(self._current_system)
            return

        # Fuels category — add fuel
        if etype == "fuels_cat":
            add_fuel_action = menu.addAction(tr("tree_ctx.add_fuel"))
            action = menu.exec(event.globalPos())
            if action == add_fuel_action:
                self.addFuelRequested.emit(self._current_system)
            return

        # Technologies category — add technology
        if etype == "technologies_cat":
            add_tech_action = menu.addAction(tr("tree_ctx.add_technology"))
            action = menu.exec(event.globalPos())
            if action == add_tech_action:
                self.addTechnologyRequested.emit(self._current_system)
            return

        # Investment Portfolio category — submenu per technology type
        if etype == "investment_portfolio_cat":
            _TECH_ACTIONS = [
                ("generator", tr("tree_ctx.add_generator")),
                ("battery", tr("tree_ctx.add_battery")),
                ("electrolyzer", tr("tree_ctx.add_electrolyzer")),
                ("acdc_converter", tr("tree_ctx.add_acdc")),
                ("freq_converter", tr("tree_ctx.add_freq")),
                ("transmission", tr("tree_ctx.add_transmission")),
                ("fuel_storage", tr("tree_ctx.add_fuel_storage")),
                ("fuel_transport", tr("tree_ctx.add_fuel_transport")),
            ]
            action_map = {}
            for tech_type, label in _TECH_ACTIONS:
                action_map[menu.addAction(label)] = tech_type
            action = menu.exec(event.globalPos())
            if action and action in action_map:
                self.addInvestmentRequested.emit(
                    self._current_system, action_map[action],
                )
            return

        # Geo asset specific actions
        if etype == "geo_asset":
            parse_action = menu.addAction(tr("tree_ctx.parse_to_elements"))
            menu.addSeparator()
        else:
            parse_action = None

        # Duplicate action
        duplicate_action = None
        if etype in self._DUPLICABLE_TYPES:
            duplicate_action = menu.addAction(tr("tree_ctx.duplicate"))

        # Copy / Paste actions
        copy_action = None
        paste_action = None
        if etype in self._COPYABLE_TYPES:
            copy_action = menu.addAction(tr("tree_ctx.copy_attributes"))
            paste_action = menu.addAction(tr("tree_ctx.paste_attributes"))
            paste_action.setEnabled(self._clipboard_type == etype)

        # Delete action
        delete_action = None
        if etype in self._DELETABLE_TYPES:
            if menu.actions():
                menu.addSeparator()
            delete_action = menu.addAction(tr("tree_ctx.delete"))

        if not menu.actions():
            return

        action = menu.exec(event.globalPos())
        if action is None:
            return
        if action == parse_action:
            self.parseGeoAssetRequested.emit(eid)
        elif action == duplicate_action:
            self.duplicateRequested.emit(etype, eid)
        elif action == copy_action:
            self._clipboard_type = etype
            self.copyRequested.emit(etype, eid)
        elif action == paste_action:
            self.pasteRequested.emit(etype, eid)
        elif action == delete_action:
            owning = self._system_for_item(item)
            if owning and owning != self._current_system:
                self.systemSwitchRequested.emit(owning)
            self.deleteRequested.emit(etype, eid)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_root(self, category: str) -> QTreeWidgetItem | None:
        """Get the category root item for the current system."""
        roots = self._system_roots.get(self._current_system)
        if roots:
            return roots.get(category)
        return None

    def _on_selection_changed(self):
        """Handle tree selection changes (single or multi)."""
        selected = self.tree.selectedItems()
        typed_items: list[tuple[str, str]] = []
        for it in selected:
            d = it.data(0, 100)
            if d:
                typed_items.append((d[0], d[1]))

        if not typed_items:
            self._delete_btn.setEnabled(False)
            return

        # Enforce same-type constraint
        primary_type = typed_items[-1][0]  # type of last-clicked item
        same_type = [(t, i) for t, i in typed_items if t == primary_type]

        if len(same_type) < len(typed_items):
            self.tree.blockSignals(True)
            for it in selected:
                d = it.data(0, 100)
                if d and d[0] != primary_type:
                    it.setSelected(False)
            self.tree.blockSignals(False)

        ids = [i for _, i in same_type]
        # If the selected item lives under a different system root than
        # the active one, prefix the emitted ID with ``<system>/`` so the
        # main window switches to that system before resolving the ID.
        # Without this prefix, ``model.get_node(5)`` runs on the wrong
        # system's state (which may have fewer nodes), returns None, and
        # the form silently keeps showing the previous selection.
        primary_item = selected[-1] if selected else None
        item_system = self._system_for_item(primary_item) if primary_item else None
        needs_prefix = (
            item_system is not None
            and item_system != self._current_system
        )

        def _qualify(elem_id: str) -> str:
            return f"{item_system}/{elem_id}" if needs_prefix else elem_id

        if len(ids) == 1:
            if primary_type == "system":
                self.systemSwitchRequested.emit(ids[0])
                self.elementSelected.emit("system_settings", ids[0])
            else:
                self.elementSelected.emit(primary_type, _qualify(ids[0]))
        elif len(ids) > 1:
            self.multiElementSelected.emit(
                primary_type, [_qualify(i) for i in ids],
            )

        self._delete_btn.setEnabled(
            primary_type in self._DELETABLE_TYPES and len(ids) >= 1
        )

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle system switching on click (selection handled by _on_selection_changed)."""
        data = item.data(0, 100)
        if data and data[0] == "system":
            self.systemSwitchRequested.emit(data[1])

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        data = item.data(0, 100)
        if data:
            if data[0] == "system":
                self.systemSwitchRequested.emit(data[1])
                self.elementSelected.emit("system_settings", data[1])
            else:
                self.elementFocused.emit(data[0], data[1])

    def _system_for_item(self, item) -> str | None:
        """Walk up the tree to find which system this item belongs to.

        Returns the system name if found, else None (item lives outside
        any per-system subtree, e.g. Geo Assets / Inter-system links).
        """
        cur = item
        while cur is not None:
            d = cur.data(0, 100)
            if d and d[0] == "system":
                return d[1]
            cur = cur.parent()
        return None

    def _remove_child(self, root: QTreeWidgetItem, etype: str, eid: str):
        for i in range(root.childCount()):
            child = root.child(i)
            data = child.data(0, 100)
            if data and data[0] == etype and data[1] == eid:
                root.removeChild(child)
                # Re-index remaining children for index-based types
                if etype in _INDEX_BASED_TYPES:
                    self._reindex_children(root, etype)
                return

    def _reindex_children(self, root: QTreeWidgetItem, etype: str):
        """Reassign sequential indices (0, 1, 2, ...) to children of *root*."""
        for i in range(root.childCount()):
            child = root.child(i)
            data = child.data(0, 100)
            if data and data[0] == etype:
                child.setData(0, 100, (etype, str(i)))

    def _update_child(
        self, root: QTreeWidgetItem, etype: str, eid: str, text: str, info: str
    ):
        for i in range(root.childCount()):
            child = root.child(i)
            data = child.data(0, 100)
            if data and data[0] == etype and data[1] == eid:
                child.setText(0, text)
                child.setText(1, info)
                return

    def _on_item_changed(self, item: QTreeWidgetItem, column: int):
        """Handle checkbox state changes (for geo asset visibility)."""
        data = item.data(0, 100)
        if not data or data[0] != "geo_asset":
            return
        visible = item.checkState(0) == Qt.CheckState.Checked
        self.geoAssetVisibilityChanged.emit(data[1], visible)

    def _on_delete_clicked(self):
        selected = self.tree.selectedItems()
        items: list[tuple[str, str]] = []
        owning_systems: set[str] = set()
        for it in selected:
            d = it.data(0, 100)
            if d and d[0] in self._DELETABLE_TYPES:
                items.append((d[0], d[1]))
                owner = self._system_for_item(it)
                if owner is not None:
                    owning_systems.add(owner)
        if len(owning_systems) > 1:
            logger.warning(
                "Delete button: selection spans systems %s — aborting",
                owning_systems,
            )
            self._delete_btn.setEnabled(False)
            return
        target_system = next(iter(owning_systems), None)
        # Sort index-based types in reverse order so that higher indices
        # are deleted first, preventing index-shift corruption.
        items = _sort_deletes_reverse(items)
        if target_system and target_system != self._current_system:
            self.systemSwitchRequested.emit(target_system)
        if len(items) == 1:
            self.deleteRequested.emit(items[0][0], items[0][1])
        elif items:
            self.batchDeleteRequested.emit(items)
        self._delete_btn.setEnabled(False)

    def retranslateUi(self):
        """Update all static labels after a language change."""
        self._search_box.setPlaceholderText(tr("tree.search_placeholder"))
        self.tree.setHeaderLabels([tr("tree.header_element"), tr("tree.header_info")])
        self._delete_btn.setText(tr("common.delete"))

        # Global items
        self._global_settings_item.setText(0, tr("tree.global_settings"))
        self._stochastic_item.setText(0, tr("tree.scenarios"))
        self._islinks_root.setText(0, tr("tree.inter_system_links"))

        # Inter-system sub-roots (preserve counts)
        for root, key in [
            (self._islink_transmission_root, "tree.transmission_lines"),
            (self._islink_fuel_root, "tree.fuel_routes"),
        ]:
            count = root.childCount()
            root.setText(0, f"{tr(key)} ({count})")

        # Geo assets
        count = self._geo_assets_root.childCount()
        self._geo_assets_root.setText(0, f"{tr('tree.geo_assets')} ({count})")

        # Per-system category labels
        _PURE_SINGLETONS = {"ev_config", "rooftop_solar"}
        for _sys_name, roots in self._system_roots.items():
            for cat_key, cat_item in roots.items():
                label = _category_label(cat_key)
                if cat_key in _PURE_SINGLETONS:
                    cat_item.setText(0, label)
                else:
                    count = cat_item.childCount()
                    cat_item.setText(0, f"{label} ({count})")

    def refresh_theme(self):
        """Re-apply per-category foreground colors from the active theme."""
        # Top-level permanent items
        for item, key in [
            (self._global_settings_item, "global_settings"),
            (self._stochastic_item, "stochastic"),
            (self._islinks_root, "inter_system_links"),
            (self._geo_assets_root, "geo_assets"),
        ]:
            color = get_tree_category_color(key)
            if color:
                item.setForeground(0, QBrush(QColor(color)))
            else:
                item.setForeground(0, QBrush())  # reset to default

        # Per-system items
        for sys_name, sys_item in self._system_items.items():
            sys_color = get_tree_category_color("system")
            if sys_color:
                sys_item.setForeground(0, QBrush(QColor(sys_color)))
            else:
                sys_item.setForeground(0, QBrush())

        for sys_name, roots in self._system_roots.items():
            for cat_key, cat_item in roots.items():
                color = get_tree_category_color(cat_key)
                if color:
                    cat_item.setForeground(0, QBrush(QColor(color)))
                    cat_font = cat_item.font(0)
                    cat_font.setBold(True)
                    cat_item.setFont(0, cat_font)
                else:
                    cat_item.setForeground(0, QBrush())

    def begin_batch(self) -> None:
        """Suppress count updates during bulk loading."""
        self._batch_mode = True

    def end_batch(self) -> None:
        """Re-enable count updates and refresh all counts."""
        self._batch_mode = False
        roots = self._system_roots.get(self._current_system, {})
        if roots:
            for root in roots.values():
                self._update_count(root)

    def _update_count(self, root: QTreeWidgetItem):
        if self._batch_mode:
            return
        base = root.text(0).split(" (")[0]
        count = root.childCount()
        root.setText(0, f"{base} ({count})")

    # ------------------------------------------------------------------
    # Search / filter
    # ------------------------------------------------------------------

    def _on_search_changed(self, text: str):
        """Debounce search to avoid filtering on every keystroke."""
        self._search_timer.start()

    def _on_search_debounced(self):
        """Filter tree items based on search text. Shows matching items
        and their parent chain; hides everything else."""
        query = self._search_box.text().strip().lower()

        if not query:
            # Reset: show everything, restore collapsed state
            self._set_all_visible(self.tree.invisibleRootItem(), True)
            return

        # Hide all, then selectively show matches + ancestors
        self._set_all_visible(self.tree.invisibleRootItem(), False)
        self._show_matches(self.tree.invisibleRootItem(), query)

    def _set_all_visible(self, parent: QTreeWidgetItem, visible: bool):
        """Recursively show or hide all items under *parent*."""
        for i in range(parent.childCount()):
            child = parent.child(i)
            child.setHidden(not visible)
            self._set_all_visible(child, visible)

    def _show_matches(self, parent: QTreeWidgetItem, query: str) -> bool:
        """Recursively show items whose text matches *query*.

        Returns True if *parent* (or any descendant) matched, so that
        ancestor items can be revealed as well.
        """
        any_child_matched = False

        for i in range(parent.childCount()):
            child = parent.child(i)

            # Check text in both columns
            text0 = child.text(0).lower()
            text1 = child.text(1).lower()
            self_match = query in text0 or query in text1

            # Recurse into children first
            child_match = self._show_matches(child, query)

            if self_match or child_match:
                child.setHidden(False)
                if self_match:
                    child.setExpanded(child.childCount() > 0)
                if child_match:
                    child.setExpanded(True)
                any_child_matched = True

        return any_child_matched
