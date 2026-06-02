"""Internationalization support for the ESFEX Studio.

Provides a simple ``tr()`` function that looks up translated strings
from JSON language files stored in ``translations/``.

Usage::

    from esfex.visualization.i18n import tr, init_i18n

    init_i18n("es")          # call once at startup
    label = tr("toolbar.select")   # → "Seleccionar"
    msg = tr("messages.save_ok", path="/tmp/out.yaml")  # with interpolation
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Signal as _Signal

_TRANSLATIONS_DIR = Path(__file__).resolve().parent / "translations"

_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "es": "Español",
    "ja": "日本語",
}

_current_lang: str = "en"
_strings: dict[str, str] = {}


class _LanguageNotifier(QObject):
    """Singleton emitter for language-change notifications."""
    changed = _Signal()


_notifier = _LanguageNotifier()

#: Connect to this signal to retranslate widgets when the language changes.
#: Usage: ``from esfex.visualization.i18n import language_changed``
language_changed = _notifier.changed


def _flatten(d: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict into dot-notation keys."""
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = str(v)
    return out


def init_i18n(lang: str = "en") -> None:
    """Load a language file.  Call once before creating any widgets."""
    global _current_lang, _strings
    _current_lang = lang

    path = _TRANSLATIONS_DIR / f"{lang}.json"
    if not path.exists():
        if lang != "en":
            path = _TRANSLATIONS_DIR / "en.json"
            _current_lang = "en"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            _strings = _flatten(json.load(fh))
    else:
        _strings = {}


def set_language(lang: str) -> None:
    """Switch language at runtime.

    Loads the new translation strings and emits ``language_changed`` so
    that connected widgets can call their ``retranslateUi()`` methods.
    """
    init_i18n(lang)
    # The Qt-backed signal object can be garbage-collected if its parent
    # QObject was destroyed (e.g. test isolation that tears down QApplication).
    # Skip emit silently in that case — translations still get applied via
    # init_i18n above; only the live-refresh hook is lost.
    if language_changed is not None:
        try:
            language_changed.emit()
        except (RuntimeError, AttributeError):
            pass


def get_language() -> str:
    """Return current language code."""
    return _current_lang


def get_available_languages() -> dict[str, str]:
    """Return ``{code: display_name}`` for every available language."""
    available: dict[str, str] = {}
    for p in sorted(_TRANSLATIONS_DIR.glob("*.json")):
        code = p.stem
        available[code] = _LANGUAGE_NAMES.get(code, code)
    return available


def tr(key: str, **kwargs) -> str:
    """Translate *key* into the current language.

    Parameters
    ----------
    key:
        Dot-notation key, e.g. ``"toolbar.select"``.
    **kwargs:
        Optional ``str.format()`` arguments for interpolation.

    Returns
    -------
    str
        Translated string, or *key* itself if not found (makes missing
        translations easy to spot in the UI).
    """
    text = _strings.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            return text
    return text
