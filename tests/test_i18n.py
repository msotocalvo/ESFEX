"""Tests for the i18n (internationalization) module."""

from __future__ import annotations

from pathlib import Path

import pytest

from esfex.visualization import i18n as _i18n_mod
from esfex.visualization.i18n import (
    _flatten,
    get_available_languages,
    get_language,
    init_i18n,
    set_language,
    tr,
)


@pytest.fixture(autouse=True)
def _restore_language_notifier():
    """Ensure the QObject-backed language_changed signal stays alive across
    tests. Earlier tests in the suite can garbage-collect the singleton
    notifier (or its Qt parent), leaving language_changed.emit() to fail
    with AttributeError on None. Re-pointing the module-level reference at a
    fresh instance per-test restores deterministic behavior."""
    from PySide6.QtCore import QObject, Signal

    class _Notifier(QObject):
        changed = Signal()

    n = _Notifier()
    saved = _i18n_mod.language_changed
    _i18n_mod._notifier = n
    _i18n_mod.language_changed = n.changed
    try:
        yield
    finally:
        _i18n_mod.language_changed = saved

# Path to real translation files shipped with the package.
_TRANSLATIONS_DIR = (
    Path(__file__).parent.parent / "src" / "esfex" / "visualization" / "translations"
)


# ===========================================================================
# _flatten
# ===========================================================================


class TestFlatten:
    """Tests for the private _flatten() helper."""

    def test_empty_dict(self):
        assert _flatten({}) == {}

    def test_flat_dict_no_nesting(self):
        d = {"a": "1", "b": "2"}
        assert _flatten(d) == {"a": "1", "b": "2"}

    def test_nested_dict_one_level(self):
        d = {"menu": {"file": "File", "edit": "Edit"}}
        assert _flatten(d) == {"menu.file": "File", "menu.edit": "Edit"}

    def test_deeply_nested_dict(self):
        d = {"a": {"b": {"c": {"d": "deep"}}}}
        assert _flatten(d) == {"a.b.c.d": "deep"}

    def test_mixed_depth(self):
        d = {
            "top": "T",
            "group": {"mid": "M", "sub": {"leaf": "L"}},
        }
        result = _flatten(d)
        assert result == {"top": "T", "group.mid": "M", "group.sub.leaf": "L"}

    def test_numeric_value_converted_to_str(self):
        d = {"count": 42}
        result = _flatten(d)
        assert result == {"count": "42"}

    def test_boolean_value_converted_to_str(self):
        d = {"flag": True}
        result = _flatten(d)
        assert result == {"flag": "True"}

    def test_prefix_parameter(self):
        d = {"key": "val"}
        result = _flatten(d, prefix="root")
        assert result == {"root.key": "val"}


# ===========================================================================
# tr
# ===========================================================================


class TestTr:
    """Tests for the tr() translation function."""

    def setup_method(self):
        """Load English so we have known keys available."""
        init_i18n("en")

    def test_known_key_returns_value(self):
        result = tr("app.title")
        assert result == "ESFEX Studio"

    def test_unknown_key_returns_key(self):
        result = tr("this.key.does.not.exist")
        assert result == "this.key.does.not.exist"

    def test_interpolation_with_kwargs(self):
        # "properties.n_selected" = "{n} selected"
        result = tr("properties.n_selected", n=5)
        assert result == "5 selected"

    def test_interpolation_multiple_kwargs(self):
        # "node_form.hours_val" = "Hours: {n}"
        result = tr("node_form.hours_val", n=8760)
        assert result == "Hours: 8760"

    def test_interpolation_with_string_kwarg(self):
        # "messages.duplicate_name" = "A system named '{name}' already exists."
        result = tr("messages.duplicate_name", name="TestSys")
        assert result == "A system named 'TestSys' already exists."

    def test_bad_format_returns_text_without_error(self):
        """If format kwargs are wrong, tr() should return the raw text."""
        # "properties.n_selected" expects {n}, pass wrong keyword
        result = tr("properties.n_selected", x=5)
        # Should return the raw text (with {n} still in it), no exception
        assert "{n}" in result

    def test_missing_key_with_kwargs_returns_key(self):
        result = tr("nonexistent.key", name="hello")
        assert result == "nonexistent.key"

    def test_key_with_no_placeholders_and_extra_kwargs(self):
        # "app.title" has no format placeholders
        result = tr("app.title", extra="unused")
        assert result == "ESFEX Studio"


# ===========================================================================
# get_language
# ===========================================================================


class TestGetLanguage:
    """Tests for get_language()."""

    def test_returns_en_after_init_en(self):
        init_i18n("en")
        assert get_language() == "en"

    def test_returns_es_after_init_es(self):
        init_i18n("es")
        assert get_language() == "es"

    def test_returns_en_after_set_language_en(self):
        set_language("en")
        assert get_language() == "en"


# ===========================================================================
# set_language / init_i18n
# ===========================================================================


class TestSetLanguageAndInit:
    """Tests for set_language() and init_i18n()."""

    def test_init_en_loads_english(self):
        init_i18n("en")
        assert tr("toolbar.select") == "Select"

    def test_init_es_loads_spanish(self):
        init_i18n("es")
        assert tr("toolbar.select") == "Seleccionar"

    def test_set_language_switches(self):
        set_language("en")
        assert tr("toolbar.select") == "Select"
        set_language("es")
        assert tr("toolbar.select") == "Seleccionar"

    def test_nonexistent_language_falls_back_to_english(self):
        init_i18n("xx_nonexistent")
        # Should fall back to English
        assert get_language() == "en"
        assert tr("toolbar.select") == "Select"

    def test_init_clears_previous_strings(self):
        init_i18n("en")
        assert tr("app.title") == "ESFEX Studio"
        init_i18n("es")
        assert tr("app.title") == "ESFEX Studio"

    def test_spanish_menu_file(self):
        init_i18n("es")
        assert tr("menu.file") == "&Archivo"

    def test_english_menu_file(self):
        init_i18n("en")
        assert tr("menu.file") == "&File"


# ===========================================================================
# get_available_languages
# ===========================================================================


class TestGetAvailableLanguages:
    """Tests for get_available_languages()."""

    def test_returns_dict(self):
        result = get_available_languages()
        assert isinstance(result, dict)

    def test_contains_en(self):
        result = get_available_languages()
        assert "en" in result

    def test_contains_es(self):
        result = get_available_languages()
        assert "es" in result

    def test_en_display_name_is_english(self):
        result = get_available_languages()
        assert result["en"] == "English"

    def test_es_display_name_is_espanol(self):
        result = get_available_languages()
        assert result["es"] == "Español"  # noqa: RUF001

    def test_at_least_two_languages(self):
        result = get_available_languages()
        assert len(result) >= 2


# ===========================================================================
# Tests with real translation files
# ===========================================================================


class TestRealTranslationFiles:
    """Integration tests that verify real translation files are consistent."""

    def test_en_json_exists(self):
        assert (_TRANSLATIONS_DIR / "en.json").exists()

    def test_es_json_exists(self):
        assert (_TRANSLATIONS_DIR / "es.json").exists()

    def test_en_has_toolbar_keys(self):
        init_i18n("en")
        for key in [
            "toolbar.select",
            "toolbar.generator",
            "toolbar.line",
            "toolbar.validate",
            "toolbar.run",
        ]:
            result = tr(key)
            # If key is found, result should NOT be the key itself
            assert result != key, f"Key {key!r} not found in English translations"

    def test_es_has_toolbar_keys(self):
        init_i18n("es")
        for key in [
            "toolbar.select",
            "toolbar.generator",
            "toolbar.line",
            "toolbar.validate",
            "toolbar.run",
        ]:
            result = tr(key)
            assert result != key, f"Key {key!r} not found in Spanish translations"

    def test_en_and_es_differ_for_known_keys(self):
        """English and Spanish translations should differ for most keys."""
        init_i18n("en")
        en_title = tr("app.title")
        init_i18n("es")
        es_title = tr("app.title")
        assert en_title != es_title

    def test_interpolation_works_in_spanish(self):
        init_i18n("es")
        result = tr("properties.n_selected", n=3)
        assert "3" in result
