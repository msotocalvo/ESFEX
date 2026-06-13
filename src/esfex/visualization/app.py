"""QApplication lifecycle management for the ESFEX Studio."""

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from esfex.visualization.main_window import MainWindow
from esfex.visualization.theme import apply_theme

_ICONS_DIR = Path(__file__).resolve().parents[1] / "icons"


def _get_or_create_app() -> QApplication:
    """Return the existing QApplication or create one.

    Sets the app-wide window icon to ``icons/icon.svg`` so every top-level
    window (main window, dialogs, splash) inherits it on platforms that read
    QApplication.windowIcon() for the taskbar / window decorations.
    """
    app = QApplication.instance()
    if app is None:
        # Required for QWebEngineView — must be set before QApplication creation
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app = QApplication(sys.argv)
    icon_path = _ICONS_DIR / "icon.svg"
    if icon_path.exists() and app.windowIcon().isNull():
        app.setWindowIcon(QIcon(str(icon_path)))
    return app


def run_studio(config=None, system: str | None = None) -> MainWindow:
    """Create and show the editor window.

    Parameters
    ----------
    config : ESFEXConfig | str | Path | None
        An existing configuration to load into the editor.
    system : str | None
        Name of the system to focus on (default: first).

    Returns
    -------
    MainWindow
        The main window instance.
    """
    app = _get_or_create_app()

    # Show splash screen while the editor initializes
    from esfex.visualization.splash import SplashScreen

    splash = SplashScreen()
    splash.show()
    splash.set_progress(5, "Loading preferences...")

    # Load saved theme preference
    from esfex.visualization.preferences import load_preferences
    from esfex.visualization.theme import get_theme_by_name

    prefs = load_preferences()
    splash.set_progress(15, "Loading preferences...")

    # Initialize i18n before creating any widgets
    from esfex.visualization.i18n import init_i18n, tr

    lang = prefs.get("general", {}).get("language", "en")
    init_i18n(lang)
    splash.set_progress(25, tr("splash.init_translations"))

    theme_name = prefs.get("general", {}).get("theme", "GitHub Light")
    if theme_name == "System":
        theme_name = "Light"
    font_size = prefs.get("general", {}).get("font_size", None)
    if font_size is None:
        # No explicit user override → scale the base font with the screen so
        # all GUI text (toolbar, side/bottom panels, forms) stays proportionate
        # across display sizes. Neutral (1.0) on a 1080p screen.
        from esfex.visualization.ui_scale import font_scale
        base_body = get_theme_by_name(theme_name).typography.size_body
        font_size = round(base_body * font_scale())
    apply_theme(app, get_theme_by_name(theme_name), font_size=font_size)
    splash.restyle()
    splash.set_progress(35, tr("splash.creating_editor"))

    window = MainWindow()
    # Pass loaded preferences to the window for runtime use
    window._user_prefs = prefs
    # Apply debug mode from advanced preferences
    if prefs.get("general", {}).get("debug_mode", False):
        import logging
        logging.getLogger().setLevel(logging.DEBUG)
    splash.set_progress(75, tr("splash.loading_config"))

    if config is not None:
        # Deferred import to keep module lightweight
        from pathlib import Path

        import yaml as _yaml

        from esfex.config.loader import load_config
        from esfex.config.schema import ESFEXConfig

        config_path = None
        if isinstance(config, (str, Path)):
            config_path = Path(config)
            config = load_config(config_path)
        if isinstance(config, ESFEXConfig):
            window._loaded_config = config
            # Read raw YAML dict for GUI-only keys (e.g. visual_scaling)
            if config_path and config_path.is_file():
                with open(config_path, "r", encoding="utf-8") as fh:
                    window._raw_config_dict = _yaml.safe_load(fh) or {}
            # Actual population happens in Phase 3 (GuiModel + serializer)

    splash.set_progress(85, tr("splash.loading_plugins"))

    # Load and register plugins in GUI mode
    try:
        from esfex.plugins import get_plugin_manager

        pm = get_plugin_manager()
        loaded_config = getattr(window, "_loaded_config", None)
        pm.load_all(loaded_config, gui_mode=True)
        pm.register_gui_extensions(window)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Plugin loading failed in GUI mode")

    splash.set_progress(100, tr("splash.ready"))
    splash.finish(window)
    return window
