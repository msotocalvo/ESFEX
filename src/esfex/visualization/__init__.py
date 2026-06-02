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
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        raise ImportError(
            "PySide6 is required for the Studio. "
            "Install it with:  pip install 'esfex[gui]'"
        )

    from esfex.visualization.app import _get_or_create_app, run_studio

    app = _get_or_create_app()
    window = run_studio(config=config, system=system)

    if blocking:
        app.exec()
        return getattr(window, "_result_config", None)

    return None
