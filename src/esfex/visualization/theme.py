"""Centralized theme system for the ESFEX Studio.

All visual styling — colors, fonts, spacing, QSS, map CSS — is defined here.
Individual widgets use Qt object names (``setObjectName``) so the app-level
stylesheet applies automatically.  No inline ``setStyleSheet`` calls needed.

Usage::

    from esfex.visualization.theme import apply_theme, current_theme
    app = QApplication(sys.argv)
    apply_theme(app)
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from PySide6.QtWidgets import QApplication


# ══════════════════════════════════════════════════════════════════
# Theme token dataclasses
# ══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ColorPalette:
    """Core UI color tokens."""

    # Surfaces
    surface_primary: str = "#FFFFFF"
    surface_secondary: str = "#F5F7FA"
    surface_elevated: str = "#FFFFFF"
    surface_dark: str = "#1E2A38"

    # Text
    text_primary: str = "#2C3E50"
    text_secondary: str = "#7F8C8D"
    text_on_dark: str = "#D4D4D4"
    text_disabled: str = "#BDC3C7"

    # Borders
    border_light: str = "#DEE2E6"
    border_medium: str = "#ADB5BD"
    border_dark: str = "#495057"

    # Accent
    accent_primary: str = "#2980B9"
    accent_primary_hover: str = "#2471A3"
    accent_primary_pressed: str = "#1A5276"
    accent_secondary: str = "#27AE60"
    accent_secondary_hover: str = "#229954"

    # Semantic
    status_success: str = "#27AE60"
    status_warning: str = "#F39C12"
    status_error: str = "#E74C3C"
    status_info: str = "#3498DB"

    # Danger / delete
    danger: str = "#C0392B"
    danger_hover: str = "#E74C3C"

    # Selection
    selection_bg: str = "#D6EAF8"
    selection_border: str = "#2980B9"

    # Status bar (optional override; empty = use surface_secondary)
    status_bar_bg: str = ""
    status_bar_fg: str = ""

    # Focus border (optional override; empty = use accent_primary)
    focus_border: str = ""

    # Toolbar icon color (optional; empty = use text_primary)
    toolbar_icon: str = ""


@dataclass(frozen=True)
class MapElementColors:
    """Map marker / polyline colors (must stay in sync with JS)."""

    node: str = "#3498DB"
    generator_renewable: str = "#27AE60"
    generator_nonrenewable: str = "#7F8C8D"
    battery: str = "#F39C12"
    fuel_entry: str = "#E74C3C"
    transformer: str = "#9B59B6"
    fuel_storage: str = "#D35400"
    electrolyzer: str = "#16A085"
    acdc_converter: str = "#2980B9"
    freq_converter: str = "#8E44AD"
    bus: str = "#34495E"
    transmission_line: str = "#3498DB"
    fuel_route: str = "#E74C3C"
    zone: str = "#3498DB"


@dataclass(frozen=True)
class ZoneColors:
    """Development zone technology → color mapping."""

    solar: str = "#F1C40F"
    wind: str = "#3498DB"
    battery: str = "#2ECC71"
    hydro: str = "#1ABC9C"
    biomass: str = "#8E44AD"
    hydrogen: str = "#E74C3C"


@dataclass(frozen=True)
class SyntaxColors:
    """Python editor syntax highlighting."""

    # Light-theme defaults: dark-on-light, VS Code Light+ palette
    keyword: str = "#0000FF"
    builtin: str = "#267F99"
    self_ref: str = "#001080"
    number: str = "#098658"
    decorator: str = "#795E26"
    string: str = "#A31515"
    comment: str = "#008000"
    editor_bg: str = "#FAFBFC"
    editor_fg: str = "#1E2A38"
    line_number_bg: str = "#F0F2F4"
    line_number_fg: str = "#6B7280"


@dataclass(frozen=True)
class ValidationColors:
    """Validation dialog severity colors."""

    error: str = "#E74C3C"
    warning: str = "#F39C12"
    info: str = "#3498DB"
    simplification: str = "#9B59B6"


@dataclass(frozen=True)
class TreeCategoryColors:
    """Per-category foreground colors for the element tree (VS Code-style)."""

    system: str = ""            # bold, uses text_primary
    nodes: str = ""
    generators: str = ""
    batteries: str = ""
    lines: str = ""
    transformers: str = ""
    zones: str = ""
    fuel_entries: str = ""
    fuel_sources: str = ""
    fuel_storages: str = ""
    fuel_routes: str = ""
    fuels: str = ""
    electrolyzers: str = ""
    ev_config: str = ""
    rooftop_solar: str = ""
    buses: str = ""
    acdc_converters: str = ""
    freq_converters: str = ""
    technologies: str = ""
    investment_portfolio: str = ""
    risk_scenarios: str = ""
    global_settings: str = ""
    stochastic: str = ""
    inter_system_links: str = ""
    geo_assets: str = ""


@dataclass(frozen=True)
class Typography:
    """Font families and sizes."""

    family_ui: str = "Segoe UI, Roboto, Ubuntu, sans-serif"
    family_mono: str = "JetBrains Mono, Consolas, Monospace"
    size_body: int = 12
    size_small: int = 10
    size_heading: int = 13
    size_code: int = 9


@dataclass(frozen=True)
class Spacing:
    """Margin, padding and radius constants."""

    form_margin: int = 6
    form_spacing: int = 4
    panel_margin: int = 4
    group_radius: int = 4
    button_padding_h: int = 12
    button_padding_v: int = 5


@dataclass(frozen=True)
class Animations:
    """Animation durations in milliseconds."""

    duration_fast: int = 150
    duration_normal: int = 250


def _default_generation_colors() -> dict[str, str]:
    return {
        "Solar": "#FFC300",
        "Rooftop_Solar": "#FFD700",
        "Wind": "#5DADE2",
        "Biomass": "#2ECC71",
        "Hydro": "#1ABC9C",
        "Hydroelectric": "#1ABC9C",
        "OTEC": "#1A5276",
        "Gas turbine": "#AF601A",
        "Gas turbines": "#AF601A",
        "Oil turbine": "#784212",
        "Oil turbines": "#784212",
        "Fuel turbine": "#D35400",
        "Fuel oil turbines": "#D35400",
        "Fuel engines": "#BA4A00",
        "Diesel engines": "#707B7C",
        "Battery discharge": "#9B59B6",
        "Hydro pump discharge": "#3498DB",
        "Hydrogen FC discharge": "#3CD3E7",
        "Hydrogen_FuelCell": "#3CD3E7",
        "V2G discharge": "#16A085",
        "Battery charge": "#D7BDE2",
        "Hydro pump charge": "#AED6F1",
        "V2G charge": "#A9DFBF",
        "Curtailment": "#FC0707",
        "Battery spillage": "#8B0000",
        "Dynamic reserve": "#F39C12",
        "Static reserve": "#7E5109",
        "Solar rooftop": "#FFD700",
        "Solar PV": "#FFC300",
        "Fuel Oil": "#D35400",
        "Gas Turbine": "#AF601A",
        "Diesel": "#707B7C",
        "Thermal": "#A04000",
        "Industrial": "#566573",
        "Coal": "#2C3E50",
        "Nuclear": "#8E44AD",
    }


def _default_heatmap_gradients() -> dict[str, tuple[str, str]]:
    return {
        "RE": ("#E74C3C", "#27AE60"),           # red → green
        "CO2": ("#F1C40F", "#E74C3C"),           # yellow → red
        "Load Shedding": ("#F1C40F", "#E74C3C"),
        "Price": ("#3498DB", "#E74C3C"),         # blue → red
        "LMP": ("#3498DB", "#E74C3C"),
        "Investment": ("#BDC3C7", "#2ECC71"),    # grey → green
        "Power Flow": ("#3498DB", "#E74C3C"),     # blue → red
        "Fuel Transport": ("#F39C12", "#8E44AD"),  # orange → purple
        "_default": ("#3498DB", "#E74C3C"),
    }


_DEFAULT_TAB10 = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]


@dataclass(frozen=True)
class ChartColors:
    """Colors for matplotlib charts and result overlays."""

    generation: dict[str, str] = field(default_factory=_default_generation_colors)
    heatmap_gradients: dict[str, tuple[str, str]] = field(
        default_factory=_default_heatmap_gradients,
    )
    tab10_fallback: list[str] = field(default_factory=lambda: list(_DEFAULT_TAB10))
    default_color: str = "#95A5A6"


@dataclass(frozen=True)
class Theme:
    """Complete application theme."""

    name: str = "ESFEX Professional"
    colors: ColorPalette = field(default_factory=ColorPalette)
    map_elements: MapElementColors = field(default_factory=MapElementColors)
    zones: ZoneColors = field(default_factory=ZoneColors)
    charts: ChartColors = field(default_factory=ChartColors)
    syntax: SyntaxColors = field(default_factory=SyntaxColors)
    validation: ValidationColors = field(default_factory=ValidationColors)
    tree_categories: TreeCategoryColors = field(default_factory=TreeCategoryColors)
    typography: Typography = field(default_factory=Typography)
    spacing: Spacing = field(default_factory=Spacing)
    animations: Animations = field(default_factory=Animations)


# ══════════════════════════════════════════════════════════════════
# Built-in themes
# ══════════════════════════════════════════════════════════════════

THEME_LIGHT_CLASSIC = Theme(name="Light")  # original flat defaults

THEME_GITHUB_LIGHT = Theme(
    name="GitHub Light",
    colors=ColorPalette(
        surface_primary="#FFFFFF",
        surface_secondary="#F6F8FA",
        surface_elevated="#FFFFFF",
        surface_dark="#24292F",
        text_primary="#1F2328",
        text_secondary="#656D76",
        text_on_dark="#FFFFFF",
        text_disabled="#8C959F",
        border_light="#D0D7DE",
        border_medium="#AFB8C1",
        border_dark="#6E7781",
        accent_primary="#0969DA",
        accent_primary_hover="#0550AE",
        accent_primary_pressed="#033D8B",
        accent_secondary="#1F883D",
        accent_secondary_hover="#1A7F37",
        status_success="#1A7F37",
        status_warning="#9A6700",
        status_error="#D1242F",
        status_info="#0969DA",
        danger="#CF222E",
        danger_hover="#A40E26",
        selection_bg="#DDF4FF",
        selection_border="#0969DA",
    ),
    map_elements=MapElementColors(
        node="#0969DA",
        generator_renewable="#1A7F37",
        generator_nonrenewable="#6E7781",
        battery="#9A6700",
        fuel_entry="#CF222E",
        transformer="#8250DF",
        fuel_storage="#BF5700",
        electrolyzer="#0969DA",
        acdc_converter="#0550AE",
        freq_converter="#8250DF",
        bus="#24292F",
        transmission_line="#0969DA",
        fuel_route="#CF222E",
        zone="#0969DA",
    ),
    zones=ZoneColors(
        solar="#9A6700",
        wind="#0969DA",
        battery="#1A7F37",
        hydro="#0550AE",
        biomass="#8250DF",
        hydrogen="#CF222E",
    ),
    syntax=SyntaxColors(
        keyword="#CF222E",
        builtin="#0550AE",
        self_ref="#953800",
        number="#0550AE",
        decorator="#6639BA",
        string="#0A3069",
        comment="#57606A",
        editor_bg="#FFFFFF",
        editor_fg="#1F2328",
        line_number_bg="#FFFFFF",
        line_number_fg="#656D76",
    ),
    validation=ValidationColors(
        error="#CF222E",
        warning="#9A6700",
        info="#0969DA",
        simplification="#8250DF",
    ),
    tree_categories=TreeCategoryColors(
        system="#1F2328",
        nodes="#0969DA",
        generators="#1A7F37",
        batteries="#9A6700",
        lines="#0550AE",
        transformers="#8250DF",
        zones="#9A6700",
        fuel_entries="#CF222E",
        fuel_sources="#BF5700",
        fuel_storages="#BF5700",
        fuel_routes="#BF5700",
        fuels="#953800",
        electrolyzers="#0550AE",
        ev_config="#8250DF",
        rooftop_solar="#9A6700",
        buses="#24292F",
        acdc_converters="#6639BA",
        freq_converters="#6639BA",
        technologies="#0550AE",
        investment_portfolio="#0550AE",
        risk_scenarios="#CF222E",
        global_settings="#57606A",
        stochastic="#8250DF",
        inter_system_links="#0969DA",
        geo_assets="#6E7781",
    ),
)

THEME_VSCODE_DARK = Theme(
    name="VS Code Dark+",
    colors=ColorPalette(
        surface_primary="#1E1E1E",
        surface_secondary="#252526",
        surface_elevated="#3C3C3C",
        surface_dark="#181818",
        text_primary="#D4D4D4",
        text_secondary="#CCCCCC",
        text_on_dark="#FFFFFF",
        text_disabled="#A6A6A6",
        border_light="#3C3C3C",
        border_medium="#454545",
        border_dark="#6B6B6B",
        accent_primary="#007ACC",
        accent_primary_hover="#0098FF",
        accent_primary_pressed="#005A9E",
        accent_secondary="#16825D",
        accent_secondary_hover="#1B9E6F",
        status_success="#369432",
        status_warning="#CCA700",
        status_error="#F48771",
        status_info="#007ACC",
        danger="#F44747",
        danger_hover="#F48771",
        selection_bg="#264F78",
        selection_border="#007ACC",
        status_bar_bg="#007ACC",
        status_bar_fg="#FFFFFF",
        focus_border="#007FD4",
        toolbar_icon="#4FC1FF",
    ),
    map_elements=MapElementColors(
        node="#007ACC",
        generator_renewable="#4EC9B0",
        generator_nonrenewable="#858585",
        battery="#DCDCAA",
        fuel_entry="#F44747",
        transformer="#C586C0",
        fuel_storage="#CE9178",
        electrolyzer="#4EC9B0",
        acdc_converter="#007ACC",
        freq_converter="#C586C0",
        bus="#D4D4D4",
        transmission_line="#007ACC",
        fuel_route="#F44747",
        zone="#007ACC",
    ),
    zones=ZoneColors(
        solar="#DCDCAA",
        wind="#007ACC",
        battery="#4EC9B0",
        hydro="#569CD6",
        biomass="#C586C0",
        hydrogen="#F44747",
    ),
    syntax=SyntaxColors(
        keyword="#569CD6",
        builtin="#4EC9B0",
        self_ref="#9CDCFE",
        number="#B5CEA8",
        decorator="#DCDCAA",
        string="#CE9178",
        comment="#6A9955",
        editor_bg="#1E1E1E",
        editor_fg="#D4D4D4",
        line_number_bg="#1E1E1E",
        line_number_fg="#858585",
    ),
    validation=ValidationColors(
        error="#F44747",
        warning="#CCA700",
        info="#007ACC",
        simplification="#C586C0",
    ),
    tree_categories=TreeCategoryColors(
        system="#D4D4D4",
        nodes="#569CD6",
        generators="#4EC9B0",
        batteries="#DCDCAA",
        lines="#007ACC",
        transformers="#C586C0",
        zones="#DCDCAA",
        fuel_entries="#F44747",
        fuel_sources="#CE9178",
        fuel_storages="#CE9178",
        fuel_routes="#CE9178",
        fuels="#D16969",
        electrolyzers="#4EC9B0",
        ev_config="#C586C0",
        rooftop_solar="#DCDCAA",
        buses="#D4D4D4",
        acdc_converters="#9CDCFE",
        freq_converters="#9CDCFE",
        technologies="#569CD6",
        investment_portfolio="#569CD6",
        risk_scenarios="#F44747",
        global_settings="#858585",
        stochastic="#C586C0",
        inter_system_links="#007ACC",
        geo_assets="#858585",
    ),
)

THEME_DRACULA = Theme(
    name="Dracula",
    colors=ColorPalette(
        surface_primary="#282A36",
        surface_secondary="#21222C",
        surface_elevated="#343746",
        surface_dark="#191A21",
        text_primary="#F8F8F2",
        text_secondary="#6272A4",
        text_on_dark="#F8F8F2",
        text_disabled="#6272A4",
        border_light="#44475A",
        border_medium="#6272A4",
        border_dark="#21222C",
        accent_primary="#BD93F9",
        accent_primary_hover="#D6ACFF",
        accent_primary_pressed="#9B6FD7",
        accent_secondary="#50FA7B",
        accent_secondary_hover="#69FF94",
        status_success="#50FA7B",
        status_warning="#F1FA8C",
        status_error="#FF5555",
        status_info="#8BE9FD",
        danger="#FF5555",
        danger_hover="#FF6E6E",
        selection_bg="#44475A",
        selection_border="#BD93F9",
        focus_border="#6272A4",
        toolbar_icon="#8BE9FD",
    ),
    map_elements=MapElementColors(
        node="#8BE9FD",
        generator_renewable="#50FA7B",
        generator_nonrenewable="#6272A4",
        battery="#F1FA8C",
        fuel_entry="#FF5555",
        transformer="#BD93F9",
        fuel_storage="#FFB86C",
        electrolyzer="#8BE9FD",
        acdc_converter="#8BE9FD",
        freq_converter="#BD93F9",
        bus="#F8F8F2",
        transmission_line="#8BE9FD",
        fuel_route="#FF5555",
        zone="#BD93F9",
    ),
    zones=ZoneColors(
        solar="#F1FA8C",
        wind="#8BE9FD",
        battery="#50FA7B",
        hydro="#8BE9FD",
        biomass="#BD93F9",
        hydrogen="#FF5555",
    ),
    syntax=SyntaxColors(
        keyword="#FF79C6",
        builtin="#8BE9FD",
        self_ref="#BD93F9",
        number="#BD93F9",
        decorator="#50FA7B",
        string="#F1FA8C",
        comment="#6272A4",
        editor_bg="#282A36",
        editor_fg="#F8F8F2",
        line_number_bg="#282A36",
        line_number_fg="#6272A4",
    ),
    validation=ValidationColors(
        error="#FF5555",
        warning="#F1FA8C",
        info="#8BE9FD",
        simplification="#BD93F9",
    ),
    tree_categories=TreeCategoryColors(
        system="#F8F8F2",
        nodes="#8BE9FD",
        generators="#50FA7B",
        batteries="#F1FA8C",
        lines="#8BE9FD",
        transformers="#BD93F9",
        zones="#F1FA8C",
        fuel_entries="#FF5555",
        fuel_sources="#FFB86C",
        fuel_storages="#FFB86C",
        fuel_routes="#FFB86C",
        fuels="#FF5555",
        electrolyzers="#8BE9FD",
        ev_config="#BD93F9",
        rooftop_solar="#F1FA8C",
        buses="#F8F8F2",
        acdc_converters="#8BE9FD",
        freq_converters="#BD93F9",
        technologies="#8BE9FD",
        investment_portfolio="#8BE9FD",
        risk_scenarios="#FF5555",
        global_settings="#6272A4",
        stochastic="#FF79C6",
        inter_system_links="#8BE9FD",
        geo_assets="#6272A4",
    ),
)

THEME_ONE_DARK = Theme(
    name="One Dark Pro",
    colors=ColorPalette(
        surface_primary="#282C34",
        surface_secondary="#21252B",
        surface_elevated="#2C313A",
        surface_dark="#181A1F",
        text_primary="#ABB2BF",
        text_secondary="#7F848E",
        text_on_dark="#D7DAE0",
        text_disabled="#495162",
        border_light="#3E4452",
        border_medium="#4B5362",
        border_dark="#181A1F",
        accent_primary="#61AFEF",
        accent_primary_hover="#528BFF",
        accent_primary_pressed="#4D78CC",
        accent_secondary="#98C379",
        accent_secondary_hover="#8CC265",
        status_success="#109868",
        status_warning="#D19A66",
        status_error="#E05561",
        status_info="#61AFEF",
        danger="#E05561",
        danger_hover="#FF616E",
        selection_bg="#323842",
        selection_border="#61AFEF",
        focus_border="#528BFF",
        toolbar_icon="#56D6BA",
    ),
    map_elements=MapElementColors(
        node="#61AFEF",
        generator_renewable="#98C379",
        generator_nonrenewable="#7F848E",
        battery="#D19A66",
        fuel_entry="#E05561",
        transformer="#C678DD",
        fuel_storage="#D19A66",
        electrolyzer="#56B6C2",
        acdc_converter="#61AFEF",
        freq_converter="#C678DD",
        bus="#ABB2BF",
        transmission_line="#61AFEF",
        fuel_route="#E05561",
        zone="#61AFEF",
    ),
    zones=ZoneColors(
        solar="#D19A66",
        wind="#61AFEF",
        battery="#98C379",
        hydro="#56B6C2",
        biomass="#C678DD",
        hydrogen="#E05561",
    ),
    syntax=SyntaxColors(
        keyword="#C678DD",
        builtin="#56B6C2",
        self_ref="#E06C75",
        number="#D19A66",
        decorator="#61AFEF",
        string="#98C379",
        comment="#5C6370",
        editor_bg="#282C34",
        editor_fg="#ABB2BF",
        line_number_bg="#282C34",
        line_number_fg="#495162",
    ),
    validation=ValidationColors(
        error="#E05561",
        warning="#D19A66",
        info="#61AFEF",
        simplification="#C678DD",
    ),
    tree_categories=TreeCategoryColors(
        system="#D7DAE0",
        nodes="#61AFEF",
        generators="#98C379",
        batteries="#D19A66",
        lines="#61AFEF",
        transformers="#C678DD",
        zones="#D19A66",
        fuel_entries="#E05561",
        fuel_sources="#D19A66",
        fuel_storages="#D19A66",
        fuel_routes="#D19A66",
        fuels="#E06C75",
        electrolyzers="#56B6C2",
        ev_config="#C678DD",
        rooftop_solar="#D19A66",
        buses="#ABB2BF",
        acdc_converters="#56B6C2",
        freq_converters="#C678DD",
        technologies="#61AFEF",
        investment_portfolio="#61AFEF",
        risk_scenarios="#E05561",
        global_settings="#5C6370",
        stochastic="#C678DD",
        inter_system_links="#61AFEF",
        geo_assets="#7F848E",
    ),
)

# Backward-compatible aliases
THEME_LIGHT = THEME_LIGHT_CLASSIC
THEME_DARK = THEME_VSCODE_DARK
THEME_TWILIGHT = THEME_DRACULA
THEME_VIVID = THEME_ONE_DARK

# Registry of available themes
THEMES: dict[str, Theme] = {
    "Light": THEME_LIGHT_CLASSIC,
    "GitHub Light": THEME_GITHUB_LIGHT,
    "VS Code Dark+": THEME_VSCODE_DARK,
    "Dracula": THEME_DRACULA,
    "One Dark Pro": THEME_ONE_DARK,
}

# Aliases for old theme names (user preferences migration)
_THEME_ALIASES: dict[str, str] = {
    "Dark": "VS Code Dark+",
    "Twilight": "Dracula",
    "Vivid": "One Dark Pro",
}


# ══════════════════════════════════════════════════════════════════
# Singleton
# ══════════════════════════════════════════════════════════════════

_current_theme: Theme = THEME_LIGHT


def current_theme() -> Theme:
    """Return the active theme."""
    return _current_theme


def set_theme(theme: Theme) -> None:
    """Replace the active theme."""
    global _current_theme
    _current_theme = theme


def get_theme_by_name(name: str) -> Theme:
    """Return a theme by its display name, defaulting to Light."""
    if name in THEMES:
        return THEMES[name]
    alias = _THEME_ALIASES.get(name)
    if alias:
        return THEMES.get(alias, THEME_LIGHT_CLASSIC)
    return THEME_LIGHT_CLASSIC


# ══════════════════════════════════════════════════════════════════
# QSS generator
# ══════════════════════════════════════════════════════════════════


def _theme_extras(theme: Theme) -> str:  # noqa: C901
    """Per-theme QSS personality beyond just color swaps."""
    name = theme.name
    c = theme.colors
    tc = theme.tree_categories
    fb = c.focus_border or c.accent_primary
    sb_bg = c.status_bar_bg or c.surface_secondary
    sb_fg = c.status_bar_fg or c.text_primary

    # ── VS Code Dark+: flat, blue status bar, minimal radius ───
    if name == "VS Code Dark+":
        return f"""
/* VSCode: flat buttons */
QPushButton {{
    background: #0E639C;
    border: none;
    border-radius: 2px;
    color: #FFFFFF;
    padding: 5px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: #1177BB;
}}
QPushButton:pressed {{
    background: {c.accent_primary_pressed};
}}
QPushButton:disabled {{
    background: #3A3D41;
    color: {c.text_disabled};
}}
QPushButton[objectName="deleteButton"] {{
    background: transparent;
    color: {c.danger};
    border: 1px solid {c.danger};
}}
QPushButton[objectName="deleteButton"]:hover {{
    background: {c.danger};
    color: #FFFFFF;
}}
QPushButton[objectName="runButton"] {{
    background: {c.accent_secondary};
    color: #FFFFFF;
    font-weight: bold;
}}
QPushButton[objectName="runButton"]:hover {{
    background: {c.accent_secondary_hover};
}}

/* VSCode: focus ring */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {fb};
}}

/* VSCode: blue status bar (signature) */
QStatusBar {{
    background: {sb_bg};
    color: {sb_fg};
    border-top: none;
}}

/* VSCode: tabs - active matches editor bg */
QTabBar::tab:selected {{
    background: {c.surface_primary};
    color: {c.text_primary};
    border-bottom: none;
}}
QTabBar::tab {{
    background: {c.surface_secondary};
    color: {c.text_secondary};
}}

/* VSCode: group/panel titles */
QGroupBox::title {{ color: {c.accent_primary}; }}
QLabel[objectName="panelTitle"] {{ color: {c.accent_primary}; }}
QToolButton#collapsibleHeader {{ color: {c.accent_primary}; }}

/* VSCode: tree selection */
QTreeWidget::item:selected {{ background: {c.selection_bg}; }}
QTreeWidget::item:hover {{ background: {c.surface_elevated}; }}

/* VSCode: menu */
QMenu::item:selected {{ background: #0078D4; color: #FFFFFF; }}
QMenuBar::item:selected {{ background: {c.surface_elevated}; }}

/* VSCode: progress bar */
QProgressBar::chunk {{ background: {c.accent_primary}; }}

/* VSCode: toolbar */
QToolButton:checked {{ background: {c.surface_primary}; color: {c.text_primary}; border-radius: 0; }}
QToolButton:hover {{ background: {c.surface_elevated}; border-radius: 0; }}

/* VSCode: table headers */
QHeaderView::section {{ color: {c.text_secondary}; background: {c.surface_secondary}; font-weight: 600; }}

/* VSCode: scrollbar */
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: rgba(121,121,121,0.4); }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: rgba(100,100,100,0.7); }}

/* VSCode: checkbox */
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c.border_dark}; border-radius: 2px; background: {c.surface_elevated}; }}
QCheckBox::indicator:hover {{ border-color: {c.accent_primary}; }}
QCheckBox::indicator:checked {{ background: {c.accent_primary}; border-color: {c.accent_primary}; }}

/* VSCode: radio */
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {c.border_dark}; border-radius: 9px; background: {c.surface_elevated}; }}
QRadioButton::indicator:checked {{ border: 2px solid {c.accent_primary}; background: {c.surface_elevated}; }}

/* VSCode: combobox */
QComboBox::drop-down {{ border: none; width: 22px; background: {c.surface_elevated}; }}
"""

    # ── Dracula: purple/pink neon on dark ─────────────────────
    if name == "Dracula":
        return f"""
/* Dracula: purple accent buttons */
QPushButton {{
    background: {c.border_light};
    border: none;
    border-radius: 4px;
    color: {c.text_primary};
    padding: 5px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {c.accent_primary};
    color: {c.surface_primary};
}}
QPushButton:pressed {{
    background: {c.accent_primary_pressed};
    color: {c.surface_primary};
}}
QPushButton:disabled {{
    background: {c.surface_secondary};
    color: {c.text_disabled};
}}
QPushButton[objectName="deleteButton"] {{
    background: transparent;
    color: {c.danger};
    border: 1px solid {c.danger};
}}
QPushButton[objectName="deleteButton"]:hover {{
    background: {c.danger};
    color: {c.surface_primary};
}}
QPushButton[objectName="runButton"] {{
    background: {c.accent_secondary};
    color: {c.surface_primary};
    font-weight: bold;
}}
QPushButton[objectName="runButton"]:hover {{
    background: {c.accent_secondary_hover};
}}

/* Dracula: purple focus */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {fb};
}}

/* Dracula: status bar */
QStatusBar {{ background: {c.surface_dark}; border-top: none; }}

/* Dracula: tabs */
QTabBar::tab:selected {{ background: {c.surface_primary}; color: {c.text_primary}; border-bottom: 2px solid #FF79C6; }}
QTabBar::tab {{ background: {c.surface_secondary}; color: {c.text_secondary}; }}
QTabBar::tab:hover:!selected {{ background: {c.surface_elevated}; color: {c.text_primary}; }}

/* Dracula: group/panel */
QGroupBox {{ border: 1px solid {c.accent_primary}; border-radius: 4px; }}
QGroupBox::title {{ color: {c.accent_primary}; font-weight: 700; }}
QLabel[objectName="panelTitle"] {{ color: #FF79C6; }}
QToolButton#collapsibleHeader {{ color: {c.accent_primary}; }}
QToolButton#collapsibleHeader:hover {{ background: {c.surface_elevated}; }}

/* Dracula: tree */
QTreeWidget::item:selected {{ background: {c.selection_bg}; }}
QTreeWidget::item:hover {{ background: {c.surface_elevated}; }}

/* Dracula: menu */
QMenu::item:selected {{ background: {c.accent_primary}; color: {c.surface_primary}; }}
QMenuBar::item:selected {{ background: {c.surface_elevated}; }}

/* Dracula: pink progress bar (signature) */
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #FF79C6, stop:1 {c.accent_primary}); }}

/* Dracula: toolbar */
QToolButton:checked {{ background: {c.accent_primary}; color: {c.surface_primary}; border-radius: 4px; }}
QToolButton:hover {{ background: {c.surface_elevated}; border-radius: 4px; }}

/* Dracula: table headers */
QHeaderView::section {{ color: #FF79C6; background: {c.surface_dark}; font-weight: 600; }}

/* Dracula: scrollbar */
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {c.border_light}; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {c.border_medium}; }}

/* Dracula: checkbox */
QCheckBox::indicator {{ width: 16px; height: 16px; border: 2px solid {c.border_medium}; border-radius: 3px; background: {c.surface_primary}; }}
QCheckBox::indicator:hover {{ border-color: {c.accent_primary}; }}
QCheckBox::indicator:checked {{ background: {c.accent_primary}; border-color: {c.accent_primary}; }}

/* Dracula: radio */
QRadioButton::indicator {{ width: 16px; height: 16px; border: 2px solid {c.border_medium}; border-radius: 9px; background: {c.surface_primary}; }}
QRadioButton::indicator:checked {{ border-color: {c.accent_primary}; background: {c.surface_primary}; }}

/* Dracula: combobox */
QComboBox::drop-down {{ border: none; width: 22px; background: {c.surface_elevated}; border-radius: 0 4px 4px 0; }}
"""

    # ── One Dark Pro: warm muted dark, seamless title bar ─────
    if name == "One Dark Pro":
        return f"""
/* OneDark: muted warm buttons */
QPushButton {{
    background: #404754;
    border: none;
    border-radius: 3px;
    color: {c.text_primary};
    padding: 5px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {c.border_medium};
}}
QPushButton:pressed {{
    background: {c.accent_primary_pressed};
    color: #FFFFFF;
}}
QPushButton:disabled {{
    background: #30333D;
    color: {c.text_disabled};
}}
QPushButton[objectName="deleteButton"] {{
    background: transparent;
    color: {c.danger};
    border: 1px solid {c.danger};
}}
QPushButton[objectName="deleteButton"]:hover {{
    background: {c.danger};
    color: {c.surface_primary};
}}
QPushButton[objectName="runButton"] {{
    background: {c.accent_secondary};
    color: {c.surface_dark};
    font-weight: bold;
}}
QPushButton[objectName="runButton"]:hover {{
    background: {c.accent_secondary_hover};
}}

/* OneDark: cursor-blue focus */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {fb};
}}

/* OneDark: dark status bar (not colored, unlike VS Code) */
QStatusBar {{ background: {c.surface_secondary}; border-top: none; }}

/* OneDark: tabs - active with subtle top border */
QTabBar::tab:selected {{ background: {c.surface_primary}; color: {c.text_on_dark}; border-bottom: none; }}
QTabBar::tab {{ background: {c.surface_secondary}; color: {c.text_secondary}; }}
QTabBar::tab:hover:!selected {{ background: {c.selection_bg}; }}

/* OneDark: warm group titles */
QGroupBox::title {{ color: {c.accent_primary}; }}
QLabel[objectName="panelTitle"] {{ color: {c.accent_primary}; }}
QToolButton#collapsibleHeader {{ color: {c.accent_primary}; }}
QToolButton#collapsibleHeader:hover {{ background: {c.surface_elevated}; }}

/* OneDark: tree */
QTreeWidget::item:selected {{ background: {c.selection_bg}; }}
QTreeWidget::item:hover {{ background: {c.surface_elevated}; }}

/* OneDark: menu */
QMenu::item:selected {{ background: {c.accent_primary}; color: {c.surface_dark}; }}
QMenuBar::item:selected {{ background: {c.surface_elevated}; }}

/* OneDark: warm progress bar */
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {c.accent_primary}, stop:1 {c.accent_secondary}); }}

/* OneDark: toolbar */
QToolButton:checked {{ background: {c.surface_elevated}; color: {c.text_on_dark}; border-radius: 3px; }}
QToolButton:hover {{ background: {c.surface_elevated}; border-radius: 3px; }}

/* OneDark: table headers */
QHeaderView::section {{ color: {c.text_secondary}; background: {c.surface_secondary}; font-weight: 600; }}

/* OneDark: scrollbar */
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: rgba(78,86,102,0.38); }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: rgba(90,99,117,0.5); }}

/* OneDark: checkbox */
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid #404754; border-radius: 3px; background: {c.surface_primary}; }}
QCheckBox::indicator:hover {{ border-color: {c.accent_primary}; }}
QCheckBox::indicator:checked {{ background: {c.accent_primary}; border-color: {c.accent_primary}; }}

/* OneDark: radio */
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid #404754; border-radius: 9px; background: {c.surface_primary}; }}
QRadioButton::indicator:checked {{ border: 2px solid {c.accent_primary}; background: {c.surface_primary}; }}

/* OneDark: combobox */
QComboBox::drop-down {{ border: none; width: 22px; background: {c.surface_elevated}; }}

/* OneDark: input bg darker than editor (signature) */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: #1D1F23; }}
"""

    # ── GitHub Light: clean, rounded, green primary action ────
    if name == "GitHub Light":
        return f"""
/* GitHub: rounded buttons, subtle shadow feel */
QPushButton {{
    background: {c.surface_secondary};
    border: 1px solid #D0D7DE;
    border-radius: 6px;
    color: {c.text_primary};
    padding: 5px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: #F3F4F6;
    border-color: #C0C8D0;
}}
QPushButton:pressed {{
    background: #EBECF0;
}}
QPushButton:disabled {{
    color: {c.text_disabled};
    background: {c.surface_secondary};
    border-color: #D0D7DE;
}}
QPushButton:default {{
    background: {c.accent_secondary};
    color: #FFFFFF;
    border: 1px solid rgba(31,35,40,0.15);
    border-radius: 6px;
}}
QPushButton:default:hover {{
    background: {c.accent_secondary_hover};
}}
QPushButton[objectName="deleteButton"] {{
    background: transparent;
    color: {c.danger};
    border: 1px solid {c.danger};
    border-radius: 6px;
}}
QPushButton[objectName="deleteButton"]:hover {{
    background: {c.danger};
    color: #FFFFFF;
}}
QPushButton[objectName="runButton"] {{
    background: {c.accent_secondary};
    color: #FFFFFF;
    border: 1px solid rgba(31,35,40,0.15);
    border-radius: 6px;
    font-weight: bold;
}}
QPushButton[objectName="runButton"]:hover {{
    background: {c.accent_secondary_hover};
}}

/* GitHub: blue focus ring */
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {c.accent_primary};
    border-radius: 6px;
}}

/* GitHub: status bar - clean with top border */
QStatusBar {{ background: #FFFFFF; border-top: 1px solid {c.border_light}; }}

/* GitHub: tabs */
QTabBar::tab:selected {{ background: {c.surface_primary}; color: {c.text_primary}; border-bottom: 2px solid #FD8C73; font-weight: 600; }}
QTabBar::tab {{ background: {c.surface_secondary}; color: {c.text_secondary}; border-radius: 6px 6px 0 0; }}
QTabBar::tab:hover:!selected {{ background: #FFFFFF; }}

/* GitHub: group/panel */
QGroupBox {{ border-radius: 6px; }}
QGroupBox::title {{ color: {c.accent_primary}; font-weight: 600; }}
QLabel[objectName="panelTitle"] {{ color: {c.text_primary}; font-weight: 700; }}
QToolButton#collapsibleHeader {{ color: {c.text_primary}; font-weight: 600; }}
QToolButton#collapsibleHeader:hover {{ background: {c.surface_secondary}; }}

/* GitHub: tree */
QTreeWidget::item:selected {{ background: {c.selection_bg}; }}
QTreeWidget::item:hover {{ background: {c.surface_secondary}; }}

/* GitHub: menu */
QMenu::item:selected {{ background: #0969DA; color: #FFFFFF; border-radius: 6px; }}
QMenuBar::item:selected {{ background: rgba(208,215,222,0.4); }}

/* GitHub: progress bar */
QProgressBar::chunk {{ background: {c.accent_secondary}; border-radius: 3px; }}

/* GitHub: toolbar */
QToolButton:checked {{ background: {c.selection_bg}; color: {c.accent_primary}; border-radius: 6px; }}
QToolButton:hover {{ background: {c.surface_secondary}; border-radius: 6px; }}

/* GitHub: table headers */
QHeaderView::section {{ color: {c.text_secondary}; background: {c.surface_secondary}; font-weight: 600; }}

/* GitHub: scrollbar */
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background: {c.border_medium}; border-radius: 5px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {c.border_dark}; }}

/* GitHub: checkbox */
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid #858F99; border-radius: 3px; background: #FFFFFF; }}
QCheckBox::indicator:hover {{ border-color: {c.accent_primary}; }}
QCheckBox::indicator:checked {{ background: {c.accent_primary}; border-color: {c.accent_primary}; }}

/* GitHub: radio */
QRadioButton::indicator {{ width: 15px; height: 15px; border: 1px solid #858F99; border-radius: 8px; background: #FFFFFF; }}
QRadioButton::indicator:checked {{ border: 2px solid {c.accent_primary}; background: #FFFFFF; }}

/* GitHub: combobox rounded */
QComboBox::drop-down {{ border: none; border-left: 1px solid #D0D7DE; width: 24px; background: {c.surface_secondary}; border-radius: 0 6px 6px 0; }}

/* GitHub: inputs rounded */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ border-radius: 6px; }}
"""

    # ── Light Classic: minimal, flat, no extras ───────────────
    return ""


def generate_qss(theme: Theme | None = None) -> str:
    """Build the full QApplication-level stylesheet."""
    if theme is None:
        theme = _current_theme
    c = theme.colors
    t = theme.typography
    s = theme.spacing
    syn = theme.syntax
    return f"""
/* ── Global ─────────────────────────────────────────────── */
QWidget {{
    font-family: {t.family_ui};
    font-size: {t.size_body}px;
    color: {c.text_primary};
    background-color: {c.surface_primary};
}}

/* ── Group Boxes ────────────────────────────────────────── */
QGroupBox {{
    font-weight: 600;
    border: 1px solid {c.border_light};
    border-radius: {s.group_radius}px;
    margin-top: 10px;
    padding: 14px 6px 6px 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {c.accent_primary};
    font-size: {t.size_body}px;
}}

/* ── Buttons ────────────────────────────────────────────── */
QPushButton {{
    background-color: {c.surface_secondary};
    border: 1px solid {c.border_light};
    border-radius: 3px;
    padding: {s.button_padding_v}px {s.button_padding_h}px;
    min-height: 20px;
    max-width: 150px;
}}
QPushButton:hover {{
    background-color: {c.accent_primary};
    color: white;
    border-color: {c.accent_primary_hover};
}}
QPushButton:pressed {{
    background-color: {c.accent_primary_pressed};
    color: white;
}}
QPushButton:disabled {{
    color: {c.text_disabled};
    border-color: {c.border_light};
    background-color: {c.surface_primary};
}}
QPushButton:default {{
    border-color: {c.accent_primary};
    font-weight: 600;
}}

/* Delete buttons */
QPushButton[objectName="deleteButton"] {{
    color: {c.danger};
    border: 1px solid {c.danger};
    background: transparent;
}}
QPushButton[objectName="deleteButton"]:hover {{
    background: {c.danger};
    color: white;
}}

/* Run button (script editor) */
QPushButton[objectName="runButton"] {{
    background-color: #264F28;
    color: #8FDF8F;
    border: 1px solid #3A7A3E;
    font-weight: bold;
}}
QPushButton[objectName="runButton"]:hover {{
    background-color: #2E6B31;
}}
QPushButton[objectName="runButton"]:pressed {{
    background-color: #1E4420;
}}

/* Edit/trace toggle buttons */
QPushButton[objectName="editTraceButton"] {{
    padding: 4px 12px;
}}
QPushButton[objectName="editTraceButton"]:checked {{
    background: {c.accent_primary};
    color: white;
    border-color: {c.accent_primary_hover};
}}

/* ── Inputs ─────────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    border: 1px solid {c.border_light};
    border-radius: 3px;
    padding: 3px 6px;
    background: {c.surface_primary};
    min-height: 20px;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {c.accent_primary};
}}
QComboBox:focus {{
    border-color: {c.accent_primary};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    border: 1px solid {c.border_light};
    background-color: {c.surface_primary};
    selection-background-color: {c.selection_bg};
    selection-color: {c.text_primary};
}}

/* Unified input width (properties panel) */
QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit {{
    max-width: 150px;
}}
/* Hide spin-box up/down arrow buttons globally */
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    width: 0px;
    height: 0px;
    border: none;
}}

/* ── Tree Widget ────────────────────────────────────────── */
QTreeWidget {{
    border: 1px solid {c.border_light};
    alternate-background-color: {c.surface_secondary};
    background-color: {c.surface_primary};
    outline: none;
}}
QTreeWidget::item {{
    padding: 3px 4px;
}}
QTreeWidget::item:selected {{
    background: {c.selection_bg};
    color: {c.text_primary};
}}
QTreeWidget::item:hover {{
    background: {c.surface_secondary};
}}

/* ── Table Widget ───────────────────────────────────────── */
QTableWidget {{
    border: 1px solid {c.border_light};
    gridline-color: {c.border_light};
    alternate-background-color: {c.surface_secondary};
}}
QTableWidget::item:selected {{
    background: {c.selection_bg};
    color: {c.text_primary};
}}
QHeaderView::section {{
    background-color: {c.surface_secondary};
    border: 1px solid {c.border_light};
    padding: 4px 6px;
    font-weight: 600;
}}

/* ── Tab Widget ─────────────────────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {c.border_light};
    background: {c.surface_primary};
}}
QTabBar::tab {{
    background: {c.surface_secondary};
    border: 1px solid {c.border_light};
    border-bottom: none;
    padding: 5px 14px;
    margin-right: 1px;
}}
QTabBar::tab:selected {{
    background: {c.surface_primary};
    border-bottom: 2px solid {c.accent_primary};
}}
QTabBar::tab:hover:!selected {{
    background: {c.selection_bg};
}}

/* ── Scroll Area ────────────────────────────────────────── */
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: {c.surface_secondary};
    width: 10px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {c.border_medium};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c.accent_primary};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: {c.surface_secondary};
    height: 10px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background: {c.border_medium};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c.accent_primary};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ── Progress Bar ───────────────────────────────────────── */
QProgressBar {{
    border: 1px solid {c.border_light};
    border-radius: 3px;
    text-align: center;
    background: {c.surface_secondary};
    min-height: 18px;
}}
QProgressBar::chunk {{
    background: {c.accent_primary};
    border-radius: 2px;
}}

/* ── Splitter ───────────────────────────────────────────── */
QSplitter::handle {{
    background: {c.border_light};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}

/* ── ToolBar ────────────────────────────────────────────── */
QToolBar {{
    background: {c.surface_secondary};
    border-bottom: 1px solid {c.border_light};
    spacing: 2px;
    padding: 2px;
    font-size: {round(t.size_body * 72 / 96)}pt;
}}
QToolBar::separator {{
    background: {c.border_medium};
    width: 1px;
    margin: 4px 6px;
}}
QToolButton {{
    border: 1px solid transparent;
    border-radius: 3px;
    padding: 4px;
    font-size: {round(t.size_body * 72 / 96)}pt;
}}
QToolButton:hover {{
    background: {c.selection_bg};
    border-color: {c.border_light};
}}
QToolButton:checked {{
    background: {c.accent_primary};
    color: white;
    border-color: {c.accent_primary_hover};
}}
QToolButton#collapsibleHeader {{
    background: {c.surface_secondary};
    border: 1px solid {c.border_light};
    border-radius: {s.group_radius}px;
    padding: 4px 8px;
    font-weight: bold;
    font-size: {round(t.size_small * 72 / 96)}pt;
    color: {c.text_primary};
    text-align: left;
}}
QToolButton#collapsibleHeader:hover {{
    background: {c.selection_bg};
}}
QToolButton#collapsibleHeader:checked {{
    background: {c.surface_secondary};
    color: {c.text_primary};
    border-color: {c.border_light};
}}
QToolBar QComboBox {{
    font-size: {round(t.size_body * 72 / 96)}pt;
}}
QToolBar QLabel {{
    font-size: {round(t.size_body * 72 / 96)}pt;
}}

/* ── Menu Bar ───────────────────────────────────────────── */
QMenuBar {{
    background: {c.surface_secondary};
    border-bottom: 1px solid {c.border_light};
    font-size: {t.size_body}px;
}}
QMenuBar::item {{
    padding: 4px 8px;
}}
QMenuBar::item:selected {{
    background: {c.selection_bg};
}}
QMenu {{
    background: {c.surface_primary};
    border: 1px solid {c.border_light};
    font-size: {t.size_body}px;
    padding: 4px 0px;
}}
QMenu::item {{
    padding: 6px 24px 6px 12px;
}}
QMenu::item:selected {{
    background: {c.selection_bg};
}}
QMenu::separator {{
    height: 1px;
    background: {c.border_light};
    margin: 4px 8px;
}}

/* ── Status Bar ─────────────────────────────────────────── */
QStatusBar {{
    background: {c.surface_secondary};
    border-top: 1px solid {c.border_light};
}}

/* ── Named labels ───────────────────────────────────────── */
QLabel[objectName="panelTitle"] {{
    font-weight: bold;
    font-size: {t.size_heading}px;
    color: {c.text_primary};
}}
QLabel[objectName="headerLabel"] {{
    color: {c.text_secondary};
    font-style: italic;
    padding: 2px 4px;
}}
QLabel[objectName="infoLabel"] {{
    color: {c.text_secondary};
    font-style: italic;
    padding: 4px;
}}
QLabel[objectName="statusLabel"] {{
    font-weight: bold;
    font-size: {t.size_heading}px;
}}
QLabel[objectName="sectionHeader"] {{
    font-weight: bold;
    padding: 2px 4px;
}}

/* ── Search box (element tree) ──────────────────────────── */
QLineEdit[objectName="searchBox"] {{
    padding: 4px 8px;
    margin: 2px 4px;
}}

/* ── Console / code editor ───────────────────────────────── */
QPlainTextEdit[objectName="consoleWidget"] {{
    background-color: {syn.editor_bg};
    color: {syn.editor_fg};
    font-family: {t.family_mono};
    font-size: {t.size_code}pt;
    border: 1px solid {c.border_dark};
}}
QTextEdit[objectName="logViewer"] {{
    background-color: {syn.editor_bg};
    color: {syn.editor_fg};
    font-family: {t.family_mono};
    font-size: {t.size_code}pt;
    border: 1px solid {c.border_dark};
}}

/* Read-only spin box (dimmed) */
QDoubleSpinBox[objectName="readOnlyField"] {{
    color: {c.text_secondary};
}}

/* ── Dialog buttons: no icons ──────────────────────────── */
QDialogButtonBox QPushButton {{
    icon-size: 0px;
}}
""" + _theme_extras(theme)


# ══════════════════════════════════════════════════════════════════
# Map CSS generator (injected into Leaflet HTML via JS)
# ══════════════════════════════════════════════════════════════════


def generate_map_css(theme: Theme | None = None) -> str:
    """CSS for Leaflet marker shapes and overlays."""
    if theme is None:
        theme = _current_theme
    m = theme.map_elements
    c = theme.colors
    t = theme.typography
    # Read label font size from preferences (default 10)
    try:
        from esfex.visualization.preferences import load_preferences, get_preference
        _prefs = load_preferences()
        _label_fs = get_preference(_prefs, "map", "label_font_size", 10)
    except Exception:
        _label_fs = 10
    label_font_size = f"{_label_fs}px"
    shadow = "0 2px 4px rgba(0,0,0,0.3)"
    # Parse surface_primary for rgba node label bg
    sp = c.surface_primary
    r, g, b = int(sp[1:3], 16), int(sp[3:5], 16), int(sp[5:7], 16)
    label_bg = f"rgba({r},{g},{b},0.88)"
    border_col = c.border_medium
    return f"""
/* Node label */
.node-label {{
    font-family: {t.family_ui};
    font-size: {label_font_size};
    font-weight: bold;
    white-space: nowrap;
    background: {label_bg};
    color: {c.text_primary};
    padding: 1px 4px;
    border-radius: 3px;
    border: 1px solid {border_col};
}}

/* Generator markers */
.gen-marker-renewable {{
    background: {m.generator_renewable};
    border: 2px solid #fff;
    border-radius: 4px;
    box-shadow: {shadow};
}}
.gen-marker-nonrenewable {{
    background: {m.generator_nonrenewable};
    border: 2px solid #fff;
    border-radius: 4px;
    box-shadow: {shadow};
}}

/* Battery */
.bat-marker {{
    background: {m.battery};
    border: 2px solid #fff;
    border-radius: 4px;
    box-shadow: {shadow};
}}

/* Fuel entry – circle */
.fuel-marker {{
    background: {m.fuel_entry};
    border: 2px solid #fff;
    border-radius: 50%;
    box-shadow: {shadow};
}}

/* Transformer – diamond */
.transformer-marker {{
    background: {m.transformer};
    border: 2px solid #fff;
    border-radius: 3px;
    box-shadow: {shadow};
    transform: rotate(45deg);
}}

/* Fuel storage – circle */
.fuel-storage-marker {{
    background: {m.fuel_storage};
    border: 2px solid #fff;
    border-radius: 50%;
    box-shadow: {shadow};
}}

/* Electrolyzer */
.electrolyzer-marker {{
    background: {m.electrolyzer};
    border: 2px solid #fff;
    border-radius: 4px;
    box-shadow: {shadow};
}}

/* AC/DC converter */
.acdc-marker {{
    background: {m.acdc_converter};
    border: 2px solid #fff;
    border-radius: 3px;
    box-shadow: {shadow};
}}

/* Frequency converter */
.freq-marker {{
    background: {m.freq_converter};
    border: 2px solid #fff;
    border-radius: 3px;
    box-shadow: {shadow};
}}

/* Bus – small square */
.bus-marker {{
    background: {m.bus};
    border: 2px solid #ecf0f1;
    border-radius: 2px;
    box-shadow: {shadow};
}}

/* Selection highlight */
.marker-selected {{
    border-color: {c.status_error} !important;
    box-shadow: 0 0 10px rgba(231,76,60,0.6) !important;
}}
.marker-selected svg {{
    filter: drop-shadow(0 0 6px rgba(231,76,60,0.7));
}}

/* Permanent marker labels */
.marker-label {{
    background: transparent !important;
    border: none !important;
    box-shadow: none !important;
    font-size: {label_font_size};
    font-weight: 600;
    color: {c.text_primary};
    text-shadow: 0 0 3px {c.surface_primary}, 0 0 3px {c.surface_primary}, 0 0 5px {c.surface_primary};
    padding: 0 !important;
    white-space: nowrap;
}}
.marker-label::before {{
    display: none !important;
}}

/* Results legend */
.results-legend {{
    background: {label_bg};
    color: {c.text_primary};
    padding: 8px 12px;
    border-radius: 5px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.25);
    font-family: {t.family_ui};
    font-size: 12px;
    line-height: 1.4;
}}
"""


def generate_map_js_colors(theme: Theme | None = None) -> str:
    """JS snippet to synchronize ``_defaultColors`` with the theme."""
    if theme is None:
        theme = _current_theme
    m = theme.map_elements
    return f"""
_defaultColors = {{
    'gen-marker-renewable': '{m.generator_renewable}',
    'gen-marker-nonrenewable': '{m.generator_nonrenewable}',
    'bat-marker': '{m.battery}',
    'fuel-marker': '{m.fuel_entry}',
    'transformer-marker': '{m.transformer}',
    'fuel-storage-marker': '{m.fuel_storage}',
    'electrolyzer-marker': '{m.electrolyzer}',
    'acdc-marker': '{m.acdc_converter}',
    'freq-marker': '{m.freq_converter}',
    'bus-marker': '{m.bus}',
    'node-marker': '{m.node}'
}};
"""


# ══════════════════════════════════════════════════════════════════
# Application entry point
# ══════════════════════════════════════════════════════════════════


def apply_theme(
    app: QApplication,
    theme: Theme | None = None,
    *,
    font_size: int | None = None,
) -> None:
    """Apply the theme to the entire application.

    Parameters
    ----------
    font_size : int | None
        Override the theme's default body font size (in px).  When
        provided the active theme singleton is updated with the new
        typography so all subsequent ``current_theme()`` calls
        reflect the user's preference.
    """
    if theme is not None:
        set_theme(theme)
    active = current_theme()
    if font_size is not None and font_size != active.typography.size_body:
        active = replace(
            active,
            typography=replace(active.typography, size_body=font_size),
        )
        set_theme(active)
    app.setStyleSheet(generate_qss(active))


# ══════════════════════════════════════════════════════════════════
# Accessor helpers
# ══════════════════════════════════════════════════════════════════


def get_zone_colors() -> dict[str, str]:
    """Return ``{technology: hex_color}`` for development zone overlays."""
    z = current_theme().zones
    return {
        "Solar": z.solar,
        "Wind": z.wind,
        "Battery": z.battery,
        "Hydro": z.hydro,
        "Biomass": z.biomass,
        "Hydrogen": z.hydrogen,
    }


def get_generation_colors() -> dict[str, str]:
    """Return generation technology → color mapping for charts."""
    return dict(current_theme().charts.generation)


def get_generation_default_color() -> str:
    """Return fallback color for unknown generation types."""
    return current_theme().charts.default_color


def get_heatmap_gradient(variable_name: str) -> tuple[str, str]:
    """Return ``(color_min, color_max)`` for a results variable."""
    gradients = current_theme().charts.heatmap_gradients
    for key, val in gradients.items():
        if key != "_default" and key in variable_name:
            return val
    return gradients.get("_default", ("#3498DB", "#E74C3C"))


def get_validation_color(severity: str) -> str:
    """Return hex color for a validation severity level."""
    v = current_theme().validation
    return {
        "error": v.error,
        "warning": v.warning,
        "info": v.info,
        "simplification": v.simplification,
    }.get(severity, v.info)


def get_tab10() -> list[str]:
    """Return the Tab10 fallback palette."""
    return list(current_theme().charts.tab10_fallback)


def get_tree_category_color(category_key: str) -> str | None:
    """Return the foreground hex color for a tree category, or None."""
    tc = current_theme().tree_categories
    color = getattr(tc, category_key, "")
    return color if color else None
