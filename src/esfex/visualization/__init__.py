"""ESFEX Studio — GIS-based power system designer.

Usage::

    from esfex.visualization import launch_studio

    # Create a new grid from scratch
    config = launch_studio()

    # Edit an existing YAML configuration
    config = launch_studio("configs/cuba_system.yaml")
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from esfex.config.schema import ESFEXConfig

__all__ = ["launch_studio"]


def _ensure_qt_runtime_on_path() -> None:
    """Put the conda Qt runtime dir on PATH for QtWebEngine's child process.

    On a conda-forge layout the Qt DLLs live in ``<prefix>/Library/bin`` while
    the WebEngine helper ``QtWebEngineProcess.exe`` lives in
    ``<prefix>/Library/lib/qt6``. When the Studio is launched outside an
    activated conda env (e.g. from the installer's Start-Menu/Desktop
    shortcut), ``Library/bin`` is not on PATH, so the freshly-spawned helper
    process cannot load ``Qt6WebEngineCore.dll`` and dies with
    STATUS_DLL_NOT_FOUND (0xC0000135). The map then crash-loops with
    "Map render process terminated ... Reloading". Child processes inherit
    ``os.environ['PATH']``, so prepending the dir there fixes it. No-op off
    Windows or outside a conda layout (e.g. a pip-only venv).
    """
    import os
    import sys

    if sys.platform != "win32":
        return
    libbin = os.path.join(sys.prefix, "Library", "bin")
    if not os.path.isdir(libbin):
        return
    for d in (libbin, os.path.join(sys.prefix, "Library", "lib", "qt6")):
        if os.path.isdir(d):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(d)
            except (OSError, AttributeError):
                pass


def launch_studio(
    config: Optional[Union["ESFEXConfig", str, Path]] = None,
    system: Optional[str] = None,
    blocking: bool = True,
) -> Optional["ESFEXConfig"]:
    """Launch the GIS-based power system editor.

    Parameters
    ----------
    config : ESFEXConfig | str | Path | None
        Existing configuration to edit.  Pass a :class:`ESFEXConfig`,
        a path to a YAML file, or ``None`` to start from scratch.
    system : str | None
        System name to focus on (default: first system in config).
    blocking : bool
        If ``True`` (default), block until the editor window is closed
        and return the (possibly modified) configuration.

    Returns
    -------
    ESFEXConfig | None
        The configuration object if the user saved, ``None`` if they
        cancelled or closed without saving.
    """
    # Must run before QtWebEngine spawns its render helper, or the map
    # crash-loops with STATUS_DLL_NOT_FOUND when launched outside an
    # activated conda env (e.g. the installer shortcut).
    _ensure_qt_runtime_on_path()

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        raise ImportError(
            "PySide6 is required for the Studio. It ships with esfex; "
            "reinstall with: pip install --upgrade --force-reinstall esfex"
        )

    from esfex.visualization.app import _get_or_create_app, run_studio

    app = _get_or_create_app()
    window = run_studio(config=config, system=system)

    if blocking:
        app.exec()
        return getattr(window, "_result_config", None)

    return None
