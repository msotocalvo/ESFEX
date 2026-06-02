"""Tests for the preferences module.

We skip testing ``apply_shortcuts()`` because it requires Qt's QAction.
The module-level import of ``PySide6.QtGui`` is needed by the source, so
we mock it at import time if Qt is not available (tests focus on the pure
Python functions).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

# The preferences module imports PySide6.QtGui at module level.
# If Qt is not installed in the test environment, we mock it so we
# can still test the non-Qt functions.
try:
    from esfex.visualization.preferences import (
        DEFAULT_SHORTCUTS,
        get_shortcuts,
        load_preferences,
        save_preferences,
    )
except ImportError:
    # PySide6 not available -- mock it
    import sys

    sys.modules["PySide6"] = mock.MagicMock()
    sys.modules["PySide6.QtGui"] = mock.MagicMock()
    from esfex.visualization.preferences import (
        DEFAULT_SHORTCUTS,
        get_shortcuts,
        load_preferences,
        save_preferences,
    )


# ===========================================================================
# DEFAULT_SHORTCUTS
# ===========================================================================


class TestDefaultShortcuts:
    """Sanity checks on the DEFAULT_SHORTCUTS constant."""

    def test_is_dict(self):
        assert isinstance(DEFAULT_SHORTCUTS, dict)

    def test_not_empty(self):
        assert len(DEFAULT_SHORTCUTS) > 0

    def test_has_file_save(self):
        assert "file.save" in DEFAULT_SHORTCUTS

    def test_has_file_import(self):
        assert "file.import" in DEFAULT_SHORTCUTS

    def test_has_file_export(self):
        assert "file.export" in DEFAULT_SHORTCUTS

    def test_has_file_preferences(self):
        assert "file.preferences" in DEFAULT_SHORTCUTS

    def test_has_tool_select(self):
        assert "tool.select" in DEFAULT_SHORTCUTS

    def test_has_tool_generator(self):
        assert "tool.generator" in DEFAULT_SHORTCUTS

    def test_has_tool_line(self):
        assert "tool.line" in DEFAULT_SHORTCUTS

    def test_has_tool_validate(self):
        assert "tool.validate" in DEFAULT_SHORTCUTS

    def test_has_tool_run(self):
        assert "tool.run" in DEFAULT_SHORTCUTS

    def test_all_values_are_strings(self):
        for key, val in DEFAULT_SHORTCUTS.items():
            assert isinstance(val, str), f"Value for {key!r} is not a string"

    def test_file_save_is_ctrl_s(self):
        assert DEFAULT_SHORTCUTS["file.save"] == "Ctrl+S"

    def test_file_import_is_ctrl_o(self):
        assert DEFAULT_SHORTCUTS["file.import"] == "Ctrl+O"

    def test_file_export_is_ctrl_shift_s(self):
        assert DEFAULT_SHORTCUTS["file.export"] == "Ctrl+Shift+S"

    def test_file_preferences_is_ctrl_comma(self):
        assert DEFAULT_SHORTCUTS["file.preferences"] == "Ctrl+,"

    def test_has_tool_keys(self):
        tool_keys = [k for k in DEFAULT_SHORTCUTS if k.startswith("tool.")]
        assert len(tool_keys) >= 10  # many tool entries


# ===========================================================================
# get_shortcuts
# ===========================================================================


class TestGetShortcuts:
    """Tests for get_shortcuts()."""

    def test_empty_prefs_returns_defaults(self):
        result = get_shortcuts({})
        assert result == DEFAULT_SHORTCUTS

    def test_empty_shortcuts_key_returns_defaults(self):
        result = get_shortcuts({"shortcuts": {}})
        assert result == DEFAULT_SHORTCUTS

    def test_no_shortcuts_key_returns_defaults(self):
        result = get_shortcuts({"theme": "dark"})
        assert result == DEFAULT_SHORTCUTS

    def test_partial_override_merges_correctly(self):
        prefs = {"shortcuts": {"file.save": "Ctrl+Shift+X"}}
        result = get_shortcuts(prefs)
        assert result["file.save"] == "Ctrl+Shift+X"
        # Other keys remain default
        assert result["file.import"] == DEFAULT_SHORTCUTS["file.import"]

    def test_multiple_overrides(self):
        prefs = {
            "shortcuts": {
                "file.save": "F2",
                "file.import": "F3",
                "tool.run": "F5",
            }
        }
        result = get_shortcuts(prefs)
        assert result["file.save"] == "F2"
        assert result["file.import"] == "F3"
        assert result["tool.run"] == "F5"
        # Untouched key
        assert result["file.export"] == DEFAULT_SHORTCUTS["file.export"]

    def test_unknown_keys_in_overrides_are_ignored(self):
        prefs = {"shortcuts": {"nonexistent.action": "Ctrl+Z"}}
        result = get_shortcuts(prefs)
        assert "nonexistent.action" not in result
        assert result == DEFAULT_SHORTCUTS

    def test_override_to_empty_string(self):
        prefs = {"shortcuts": {"file.save": ""}}
        result = get_shortcuts(prefs)
        assert result["file.save"] == ""

    def test_returns_new_dict_not_defaults(self):
        """Mutating returned dict must not affect DEFAULT_SHORTCUTS."""
        result = get_shortcuts({})
        original_save = DEFAULT_SHORTCUTS["file.save"]
        result["file.save"] = "MODIFIED"
        assert DEFAULT_SHORTCUTS["file.save"] == original_save

    def test_all_default_keys_present_in_result(self):
        result = get_shortcuts({"shortcuts": {"file.save": "X"}})
        for key in DEFAULT_SHORTCUTS:
            assert key in result


# ===========================================================================
# load_preferences / save_preferences
# ===========================================================================


class TestLoadSavePreferences:
    """Tests for load_preferences() and save_preferences()."""

    def test_round_trip(self, tmp_path, monkeypatch):
        """Save prefs, then load them back -- should be identical."""
        prefs_file = tmp_path / "prefs.json"
        prefs_dir = tmp_path

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)
        monkeypatch.setattr(prefs_mod, "_PREFS_DIR", prefs_dir)

        data = {
            "theme": "dark",
            "shortcuts": {"file.save": "F2"},
            "font_size": 14,
        }
        save_preferences(data)
        loaded = load_preferences()
        assert loaded == data

    def test_save_creates_directory(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "c"
        prefs_file = nested / "prefs.json"

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)
        monkeypatch.setattr(prefs_mod, "_PREFS_DIR", nested)

        save_preferences({"key": "val"})
        assert prefs_file.exists()

    def test_save_overwrites_existing(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / "prefs.json"

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)
        monkeypatch.setattr(prefs_mod, "_PREFS_DIR", tmp_path)

        save_preferences({"v": 1})
        save_preferences({"v": 2})
        loaded = load_preferences()
        assert loaded == {"v": 2}

    def test_save_writes_valid_json(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / "prefs.json"

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)
        monkeypatch.setattr(prefs_mod, "_PREFS_DIR", tmp_path)

        save_preferences({"a": [1, 2, 3]})
        raw = prefs_file.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed == {"a": [1, 2, 3]}


class TestLoadPreferencesEdgeCases:
    """Edge cases for load_preferences()."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / "nonexistent.json"

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)

        result = load_preferences()
        assert result == {}

    def test_returns_empty_dict_on_invalid_json(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / "bad.json"
        prefs_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)

        result = load_preferences()
        assert result == {}

    def test_returns_empty_dict_on_empty_file(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / "empty.json"
        prefs_file.write_text("", encoding="utf-8")

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)

        result = load_preferences()
        assert result == {}

    def test_returns_empty_dict_on_truncated_json(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / "trunc.json"
        prefs_file.write_text('{"key": "val', encoding="utf-8")

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)

        result = load_preferences()
        assert result == {}

    def test_loads_complex_nested_structure(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / "prefs.json"

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)
        monkeypatch.setattr(prefs_mod, "_PREFS_DIR", tmp_path)

        data = {
            "shortcuts": {"file.save": "F2", "tool.run": "F5"},
            "map": {"basemap": "satellite", "zoom": 12},
            "solver": {"name": "HiGHS", "threads": 4},
        }
        save_preferences(data)
        loaded = load_preferences()
        assert loaded == data

    def test_unicode_in_preferences(self, tmp_path, monkeypatch):
        prefs_file = tmp_path / "prefs.json"

        import esfex.visualization.preferences as prefs_mod

        monkeypatch.setattr(prefs_mod, "_PREFS_FILE", prefs_file)
        monkeypatch.setattr(prefs_mod, "_PREFS_DIR", tmp_path)

        data = {"language": "es", "label": "Configuracion"}  # noqa: RUF001
        save_preferences(data)
        loaded = load_preferences()
        assert loaded == data
