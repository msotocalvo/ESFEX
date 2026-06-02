"""Path-safety helpers.

When a config field carries a filesystem path (e.g. ``availability_file``
in a generator block, ``demand_path`` for a node, ``csv_path`` etc.),
the value originates from a YAML file that may have been authored by
another user — opening a colleague's project or downloading a sample
from the web. A path like ``"../../../home/$USER/.ssh/id_rsa"`` would
otherwise let that YAML read arbitrary files on the host when the
runner / GUI later reads the referenced file.

``safe_resolve_under`` resolves a candidate against a known-trusted
root and refuses anything that escapes it. Callers should use it
*before* any ``open()`` / ``Path.read_text()`` / ``pd.read_csv()``
against a path that came in from configuration data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union


def safe_resolve_under(root: Union[str, Path], candidate: Union[str, Path]) -> Path:
    """Return ``candidate`` resolved under ``root``, or raise ValueError.

    Behaviour:
    * If ``candidate`` is relative, it is joined onto ``root`` first.
    * If ``candidate`` is absolute, it is used as-is.
    * The result is then resolved (symlinks + ``..`` collapsed) and
      checked against the resolved ``root``. Anything outside raises.

    This intentionally rejects absolute paths *outside* the root even
    though the user *could* have written them — the trust model is
    that a project's data files live under that project's directory,
    not under arbitrary system locations.

    Notes
    -----
    Resolves with ``strict=False`` because the candidate file may not
    yet exist (caller handles ``FileNotFoundError`` itself).
    """
    root_p = Path(root).resolve()
    cand_p = Path(candidate)
    if not cand_p.is_absolute():
        cand_p = root_p / cand_p
    resolved = cand_p.resolve()
    try:
        resolved.relative_to(root_p)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to access {candidate!r}: resolves to {resolved} "
            f"which is outside the trusted root {root_p}. "
            "Project data files must live under the project directory."
        ) from exc
    return resolved
