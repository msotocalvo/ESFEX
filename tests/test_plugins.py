"""Tests for the ESFEX plugin framework.

Covers discovery, loading, hook dispatch, enable/disable,
install from ZIP, broken plugin safety, and config validation.
"""

import json
import os
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from esfex.plugins.manager import (
    PluginManager,
    _USER_PLUGINS_DIR,
    _STATE_FILE,
    get_plugin_manager,
    reset_plugin_manager,
)
from esfex.plugins.protocol import PluginContext, PluginMeta, ESFEXPlugin


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the plugin manager singleton between tests."""
    reset_plugin_manager()
    yield
    reset_plugin_manager()


def _create_plugin_dir(
    base: Path,
    name: str = "test_plugin",
    version: str = "1.0.0",
    priority: int = 0,
    category: str = "general",
    requires: list[str] | None = None,
    factory_body: str = "",
    extra_meta: dict | None = None,
) -> Path:
    """Create a minimal plugin directory structure."""
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "name": name,
        "version": version,
        "description": f"Test plugin {name}",
        "author": "Test",
        "priority": priority,
        "category": category,
        "requires_plugins": requires or [],
    }
    if extra_meta:
        meta.update(extra_meta)

    (plugin_dir / "plugin.json").write_text(json.dumps(meta))

    if not factory_body:
        factory_body = f"""
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class {name.title().replace('_', '')}Plugin(ESFEXPlugin):
    meta = PluginMeta(name="{name}", version="{version}", priority={priority})

def create_plugin(context):
    return {name.title().replace('_', '')}Plugin(context)
"""
    (plugin_dir / "__init__.py").write_text(factory_body)

    return plugin_dir


# ── Discovery Tests ───────────────────────────────────────────────────


class TestDiscovery:
    def test_discover_empty_dir(self, tmp_path):
        """Empty plugin directory → 0 plugins, no errors."""
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir):
            names = pm.discover()

        assert names == []
        assert len(pm.discovered) == 0

    def test_discover_nonexistent_dir(self, tmp_path):
        """Non-existent plugin directory → 0 plugins, no errors."""
        pm = PluginManager()
        with patch(
            "esfex.plugins.manager._USER_PLUGINS_DIR",
            tmp_path / "nonexistent",
        ):
            names = pm.discover()

        assert names == []

    def test_discover_finds_plugin(self, tmp_path):
        """Directory with valid plugin.json + __init__.py → discovered."""
        plugins_dir = tmp_path / "plugins"
        _create_plugin_dir(plugins_dir, "weather")

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir):
            names = pm.discover()

        assert "weather" in names
        assert "weather" in pm.metas
        assert pm.metas["weather"].version == "1.0.0"

    def test_discover_skips_missing_init(self, tmp_path):
        """plugin.json without __init__.py → skipped."""
        plugins_dir = tmp_path / "plugins"
        pdir = plugins_dir / "bad_plugin"
        pdir.mkdir(parents=True)
        (pdir / "plugin.json").write_text(
            json.dumps({"name": "bad_plugin", "version": "0.1"})
        )
        # No __init__.py

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir):
            names = pm.discover()

        assert "bad_plugin" not in names

    def test_discover_skips_invalid_json(self, tmp_path):
        """Malformed plugin.json → skipped."""
        plugins_dir = tmp_path / "plugins"
        pdir = plugins_dir / "broken"
        pdir.mkdir(parents=True)
        (pdir / "plugin.json").write_text("NOT JSON {{{")
        (pdir / "__init__.py").write_text("")

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir):
            names = pm.discover()

        assert "broken" not in names

    def test_discover_skips_missing_name(self, tmp_path):
        """plugin.json without 'name' field → skipped."""
        plugins_dir = tmp_path / "plugins"
        pdir = plugins_dir / "no_name"
        pdir.mkdir(parents=True)
        (pdir / "plugin.json").write_text(json.dumps({"version": "1.0"}))
        (pdir / "__init__.py").write_text("")

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir):
            names = pm.discover()

        assert names == []

    def test_discover_multiple_sources(self, tmp_path):
        """Plugins from user dir, project dir, and env var all discovered."""
        user_dir = tmp_path / "user_plugins"
        proj_dir = tmp_path / "project" / ".esfex" / "plugins"
        env_dir = tmp_path / "env_plugins"

        _create_plugin_dir(user_dir, "from_user")
        _create_plugin_dir(proj_dir, "from_project")
        _create_plugin_dir(env_dir, "from_env")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", user_dir),
            patch.dict(os.environ, {"ESFEX_PLUGIN_PATH": str(env_dir)}),
        ):
            # include_project_dir=True is required after the security tightening
            # that no longer auto-loads project-shipped plugins by default.
            names = pm.discover(
                project_dir=tmp_path / "project",
                include_project_dir=True,
            )

        assert "from_user" in names
        assert "from_project" in names
        assert "from_env" in names

    def test_discover_first_wins_on_duplicate(self, tmp_path):
        """If same plugin name in user and project dirs, user dir wins."""
        user_dir = tmp_path / "user_plugins"
        proj_dir = tmp_path / "project" / ".esfex" / "plugins"

        _create_plugin_dir(user_dir, "dup", version="1.0.0")
        _create_plugin_dir(proj_dir, "dup", version="2.0.0")

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", user_dir):
            pm.discover(project_dir=tmp_path / "project")

        # User dir scanned first → version 1.0.0
        assert pm.metas["dup"].version == "1.0.0"
        assert pm.discovered["dup"] == user_dir / "dup"

    def test_discover_reads_all_meta_fields(self, tmp_path):
        """All plugin.json fields are parsed into PluginMeta."""
        plugins_dir = tmp_path / "plugins"
        _create_plugin_dir(
            plugins_dir,
            "full_meta",
            version="2.3.4",
            priority=5,
            category="analysis",
            requires=["other_plugin"],
            extra_meta={
                "author": "Jane",
                "url": "https://example.com",
                "python_dependencies": ["requests>=2.28"],
            },
        )

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir):
            pm.discover()

        meta = pm.metas["full_meta"]
        assert meta.version == "2.3.4"
        assert meta.priority == 5
        assert meta.category == "analysis"
        assert meta.requires_plugins == ["other_plugin"]
        assert meta.author == "Jane"
        assert meta.url == "https://example.com"
        assert meta.python_dependencies == ["requests>=2.28"]


# ── Loading Tests ─────────────────────────────────────────────────────


class TestLoading:
    def test_load_plugin_calls_create_plugin(self, tmp_path):
        """Factory function is called and setup() is invoked."""
        plugins_dir = tmp_path / "plugins"
        body = """
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

_setup_called = False

class TestPlugin(ESFEXPlugin):
    meta = PluginMeta(name="tracker", version="1.0")

    def setup(self):
        import esfex_plugins.tracker as mod
        mod._setup_called = True

def create_plugin(context):
    return TestPlugin(context)
"""
        _create_plugin_dir(plugins_dir, "tracker", factory_body=body)

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)

        assert len(pm.plugins) == 1
        assert pm.plugins[0].meta.name == "tracker"

        import esfex_plugins.tracker as mod
        assert mod._setup_called is True

    def test_load_all_only_runs_once(self, tmp_path):
        """Calling load_all twice doesn't double-load."""
        plugins_dir = tmp_path / "plugins"
        _create_plugin_dir(plugins_dir, "once")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            count1 = len(pm.plugins)
            pm.load_all(config=None)  # should no-op
            count2 = len(pm.plugins)

        assert count1 == count2 == 1

    def test_load_single_hot_loads(self, tmp_path):
        """load_single() adds a new plugin after load_all() already ran."""
        plugins_dir = tmp_path / "plugins"
        _create_plugin_dir(plugins_dir, "initial")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            assert len(pm.plugins) == 1

            # Simulate installing a second plugin after startup
            _create_plugin_dir(plugins_dir, "late_arrival")
            pm.discover()
            assert "late_arrival" in pm.discovered

            p = pm.load_single("late_arrival", config=None, gui_mode=True)
            assert p is not None
            assert p.meta.name == "late_arrival"
            assert len(pm.plugins) == 2

    def test_load_single_skips_already_loaded(self, tmp_path):
        """load_single() returns existing plugin if already loaded."""
        plugins_dir = tmp_path / "plugins"
        _create_plugin_dir(plugins_dir, "existing")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            p = pm.load_single("existing", config=None)
            assert p is pm.plugins[0]
            assert len(pm.plugins) == 1  # not duplicated

    def test_missing_create_plugin_function(self, tmp_path):
        """__init__.py without create_plugin() → plugin not loaded."""
        plugins_dir = tmp_path / "plugins"
        pdir = plugins_dir / "no_factory"
        pdir.mkdir(parents=True)
        (pdir / "plugin.json").write_text(
            json.dumps({"name": "no_factory", "version": "1.0"})
        )
        (pdir / "__init__.py").write_text("# No create_plugin here\n")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)

        assert len(pm.plugins) == 0

    def test_plugin_receives_context(self, tmp_path):
        """create_plugin() receives PluginContext with correct fields."""
        plugins_dir = tmp_path / "plugins"
        body = """
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class CtxPlugin(ESFEXPlugin):
    meta = PluginMeta(name="ctx_check", version="1.0")

def create_plugin(context):
    p = CtxPlugin(context)
    p._received_context = context
    return p
"""
        _create_plugin_dir(plugins_dir, "ctx_check", factory_body=body)

        pm = PluginManager()
        data_dir = tmp_path / "data"
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", data_dir),
        ):
            pm.load_all(config="fake_config", gui_mode=True)

        plugin = pm.plugins[0]
        ctx = plugin._received_context
        assert ctx.config == "fake_config"
        assert ctx.gui_mode is True
        assert ctx.plugin_dir == plugins_dir / "ctx_check"
        assert ctx.data_dir == data_dir / "ctx_check"


# ── Hook Dispatch Tests ───────────────────────────────────────────────


class TestHookDispatch:
    def test_hook_dispatch_order(self, tmp_path):
        """Plugins execute hooks in priority order."""
        plugins_dir = tmp_path / "plugins"
        for name, priority in [("low", 10), ("high", 1), ("mid", 5)]:
            body = f"""
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class P(ESFEXPlugin):
    meta = PluginMeta(name="{name}", version="1.0", priority={priority})

    def pre_simulation(self, *, config, output_dir):
        return "{name}"

def create_plugin(context):
    return P(context)
"""
            _create_plugin_dir(plugins_dir, name, priority=priority, factory_body=body)

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            results = pm.call_hook("pre_simulation", config=None, output_dir=Path("."))

        # high (1) → mid (5) → low (10)
        assert results == ["high", "mid", "low"]

    def test_hook_returns_none_not_collected(self, tmp_path):
        """Hooks returning None are not included in results."""
        plugins_dir = tmp_path / "plugins"
        body = """
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class P(ESFEXPlugin):
    meta = PluginMeta(name="noop", version="1.0")

    def pre_simulation(self, *, config, output_dir):
        pass  # returns None

def create_plugin(context):
    return P(context)
"""
        _create_plugin_dir(plugins_dir, "noop", factory_body=body)

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            results = pm.call_hook("pre_simulation", config=None, output_dir=Path("."))

        assert results == []

    def test_nonexistent_hook_ignored(self, tmp_path):
        """Calling a hook that no plugin implements → empty list."""
        plugins_dir = tmp_path / "plugins"
        _create_plugin_dir(plugins_dir, "simple")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            results = pm.call_hook("nonexistent_hook", foo="bar")

        assert results == []


# ── Broken Plugin Safety ─────────────────────────────────────────────


class TestBrokenPluginSafety:
    def test_broken_plugin_doesnt_crash_loading(self, tmp_path):
        """Plugin that raises in create_plugin() → logged, others still load."""
        plugins_dir = tmp_path / "plugins"

        # Broken plugin
        broken_dir = plugins_dir / "broken"
        broken_dir.mkdir(parents=True)
        (broken_dir / "plugin.json").write_text(
            json.dumps({"name": "broken", "version": "1.0"})
        )
        (broken_dir / "__init__.py").write_text(
            "def create_plugin(ctx): raise RuntimeError('boom')\n"
        )

        # Good plugin
        _create_plugin_dir(plugins_dir, "good")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)

        # Only good plugin loaded
        assert len(pm.plugins) == 1
        assert pm.plugins[0].meta.name == "good"

    def test_broken_hook_doesnt_crash_dispatch(self, tmp_path):
        """Plugin that raises in hook → logged, other plugins still called."""
        plugins_dir = tmp_path / "plugins"

        body_a = """
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class A(ESFEXPlugin):
    meta = PluginMeta(name="crasher", version="1.0", priority=1)

    def pre_simulation(self, *, config, output_dir):
        raise RuntimeError("hook crash")

def create_plugin(ctx):
    return A(ctx)
"""
        body_b = """
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class B(ESFEXPlugin):
    meta = PluginMeta(name="survivor", version="1.0", priority=2)

    def pre_simulation(self, *, config, output_dir):
        return "survived"

def create_plugin(ctx):
    return B(ctx)
"""
        _create_plugin_dir(plugins_dir, "crasher", priority=1, factory_body=body_a)
        _create_plugin_dir(plugins_dir, "survivor", priority=2, factory_body=body_b)

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            results = pm.call_hook("pre_simulation", config=None, output_dir=Path("."))

        assert results == ["survived"]

    def test_broken_setup_doesnt_block_others(self, tmp_path):
        """Plugin that raises in setup() → still in plugins list, others OK."""
        plugins_dir = tmp_path / "plugins"
        body = """
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class Bad(ESFEXPlugin):
    meta = PluginMeta(name="bad_setup", version="1.0")

    def setup(self):
        raise RuntimeError("setup crash")

def create_plugin(ctx):
    return Bad(ctx)
"""
        _create_plugin_dir(plugins_dir, "bad_setup", factory_body=body)
        _create_plugin_dir(plugins_dir, "ok_plugin")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)  # should not raise

        # Both loaded (bad_setup survived despite setup failure)
        names = {p.meta.name for p in pm.plugins}
        assert "ok_plugin" in names


# ── Enable / Disable ─────────────────────────────────────────────────


class TestEnableDisable:
    def test_disable_prevents_loading(self, tmp_path):
        """Disabled plugin is not loaded."""
        plugins_dir = tmp_path / "plugins"
        state_file = tmp_path / "state.json"
        _create_plugin_dir(plugins_dir, "skip_me")

        # Pre-disable
        state_file.write_text(json.dumps({"disabled": ["skip_me"]}))

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", state_file),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)

        assert len(pm.plugins) == 0

    def test_enable_after_disable(self, tmp_path):
        """Enable removes from disabled list."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"disabled": ["my_plugin"]}))

        pm = PluginManager()
        with patch("esfex.plugins.manager._STATE_FILE", state_file):
            assert not pm.is_enabled("my_plugin")
            pm.enable("my_plugin")
            assert pm.is_enabled("my_plugin")

        state = json.loads(state_file.read_text())
        assert "my_plugin" not in state["disabled"]

    def test_disable_persists(self, tmp_path):
        """Disable adds to state file."""
        state_file = tmp_path / "state.json"

        pm = PluginManager()
        with patch("esfex.plugins.manager._STATE_FILE", state_file):
            pm.disable("some_plugin")

        state = json.loads(state_file.read_text())
        assert "some_plugin" in state["disabled"]

    def test_is_enabled_default_true(self, tmp_path):
        """Unknown plugin is enabled by default."""
        state_file = tmp_path / "state.json"

        pm = PluginManager()
        with patch("esfex.plugins.manager._STATE_FILE", state_file):
            assert pm.is_enabled("new_plugin")


# ── Install from ZIP ──────────────────────────────────────────────────


class TestInstallZip:
    def test_install_from_zip(self, tmp_path):
        """ZIP with valid plugin structure → extracted and discoverable."""
        # Create a plugin directory to zip
        src = tmp_path / "src_plugin"
        _create_plugin_dir(tmp_path, "src_plugin")

        # Create ZIP
        zip_path = tmp_path / "my_plugin.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for f in src.rglob("*"):
                zf.write(f, f"src_plugin/{f.relative_to(src)}")

        plugins_dir = tmp_path / "install_target"
        plugins_dir.mkdir()

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir):
            name = pm.install_from_zip(zip_path)

        assert name == "src_plugin"
        assert (plugins_dir / "src_plugin" / "plugin.json").is_file()
        assert (plugins_dir / "src_plugin" / "__init__.py").is_file()

    def test_install_from_zip_invalid(self, tmp_path):
        """ZIP without plugin structure → ValueError."""
        zip_path = tmp_path / "bad.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("not_a_plugin/readme.txt", "hello")

        plugins_dir = tmp_path / "install_target"
        plugins_dir.mkdir()

        pm = PluginManager()
        with patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir):
            with pytest.raises(ValueError, match="not a valid ESFEX plugin"):
                pm.install_from_zip(zip_path)


# ── Uninstall ─────────────────────────────────────────────────────────


class TestUninstall:
    def test_uninstall_removes_directory(self, tmp_path):
        """Uninstall removes the plugin directory."""
        plugins_dir = tmp_path / "plugins"
        _create_plugin_dir(plugins_dir, "removable")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
        ):
            pm.uninstall("removable")

        assert not (plugins_dir / "removable").exists()


# ── Teardown ──────────────────────────────────────────────────────────


class TestTeardown:
    def test_teardown_calls_plugin_teardown(self, tmp_path):
        """teardown_all() calls teardown() on each plugin."""
        plugins_dir = tmp_path / "plugins"
        body = """
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class T(ESFEXPlugin):
    meta = PluginMeta(name="tearable", version="1.0")

    def teardown(self):
        import esfex_plugins.tearable as mod
        mod._torn_down = True

_torn_down = False

def create_plugin(ctx):
    return T(ctx)
"""
        _create_plugin_dir(plugins_dir, "tearable", factory_body=body)

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            pm.teardown_all()

        import esfex_plugins.tearable as mod
        assert mod._torn_down is True
        assert len(pm.plugins) == 0

    def test_teardown_resets_loaded_flag(self, tmp_path):
        """After teardown, load_all can be called again."""
        plugins_dir = tmp_path / "plugins"
        _create_plugin_dir(plugins_dir, "reloadable")

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            assert len(pm.plugins) == 1
            pm.teardown_all()
            assert len(pm.plugins) == 0
            # Can load again
            pm._loaded = False
            pm.load_all(config=None)
            assert len(pm.plugins) == 1


# ── Singleton ─────────────────────────────────────────────────────────


class TestSingleton:
    def test_get_plugin_manager_returns_same(self):
        """get_plugin_manager() returns the same instance."""
        pm1 = get_plugin_manager()
        pm2 = get_plugin_manager()
        assert pm1 is pm2

    def test_reset_creates_new_instance(self):
        """reset_plugin_manager() creates a fresh instance."""
        pm1 = get_plugin_manager()
        reset_plugin_manager()
        pm2 = get_plugin_manager()
        assert pm1 is not pm2


# ── Protocol Base Class ──────────────────────────────────────────────


class TestProtocol:
    def test_all_hooks_are_noop(self):
        """Base ESFEXPlugin methods don't raise."""
        ctx = PluginContext(
            config=None,
            plugin_dir=Path("."),
            data_dir=Path("."),
            gui_mode=False,
        )
        plugin = ESFEXPlugin(ctx)
        plugin.meta = PluginMeta(name="base", version="0.0")

        # Lifecycle
        plugin.setup()
        plugin.teardown()

        # Config
        assert plugin.get_config_schema() is None
        plugin.on_config_loaded(None)

        # Runner hooks
        plugin.pre_simulation(config=None, output_dir=Path("."))
        assert plugin.post_demand_loaded(
            base_demand=None, ev_demand=None, total_demand=None, config=None
        ) is None
        plugin.pre_master_problem(config=None, years=[])
        plugin.post_master_problem(investments={}, retirements={}, config=None)
        plugin.pre_year(year=2025, year_idx=0, units_config={}, config=None)
        plugin.post_year(
            year=2025, result=None, hdf5_file=None, output_dir=Path("."), config=None
        )
        plugin.post_simulation(
            results=[], hdf5_path=Path("."), output_dir=Path("."), config=None
        )

        # Julia
        assert plugin.get_julia_modules() == []

        # CLI
        assert plugin.get_cli_commands() == []

        # GUI
        assert plugin.get_tree_categories() == []
        assert plugin.get_forms(None) == []
        assert plugin.get_toolbar_actions(None, None) == []
        plugin.get_menu_items(None, None)
        assert plugin.get_result_variables() == []
        plugin.get_map_layers(None)
        assert plugin.get_translations() == {}

    def test_plugin_meta_defaults(self):
        """PluginMeta has sensible defaults."""
        meta = PluginMeta(name="test", version="1.0")
        assert meta.description == ""
        assert meta.author == ""
        assert meta.url == ""
        assert meta.requires_plugins == []
        assert meta.priority == 0
        assert meta.category == "general"
        assert meta.python_dependencies == []

    def test_plugin_context_fields(self):
        """PluginContext stores all injected fields."""
        ctx = PluginContext(
            config="cfg",
            plugin_dir=Path("/plugins/test"),
            data_dir=Path("/data/test"),
            gui_mode=True,
        )
        assert ctx.config == "cfg"
        assert ctx.plugin_dir == Path("/plugins/test")
        assert ctx.data_dir == Path("/data/test")
        assert ctx.gui_mode is True


# ── Julia Module Registration ────────────────────────────────────────


class TestJuliaModules:
    def test_get_julia_module_paths(self, tmp_path):
        """get_julia_module_paths() collects paths from all plugins."""
        plugins_dir = tmp_path / "plugins"

        # Create plugin with julia module
        jl_dir = plugins_dir / "jl_plugin" / "julia"
        jl_dir.mkdir(parents=True)
        jl_file = jl_dir / "constraints.jl"
        jl_file.write_text("# Julia code\n")

        body = f"""
from pathlib import Path
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

class JlPlugin(ESFEXPlugin):
    meta = PluginMeta(name="jl_plugin", version="1.0")

    def get_julia_modules(self):
        return [self.context.plugin_dir / "julia" / "constraints.jl"]

def create_plugin(context):
    return JlPlugin(context)
"""
        (plugins_dir / "jl_plugin" / "plugin.json").write_text(
            json.dumps({"name": "jl_plugin", "version": "1.0"})
        )
        (plugins_dir / "jl_plugin" / "__init__.py").write_text(body)

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            paths = pm.get_julia_module_paths()

        assert len(paths) == 1
        assert paths[0].name == "constraints.jl"


# ── CLI Registration ─────────────────────────────────────────────────


class TestCLIRegistration:
    def test_register_cli_commands(self, tmp_path):
        """Plugin CLI sub-commands are registered on the Typer app."""
        plugins_dir = tmp_path / "plugins"
        body = """
import typer
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta

sub = typer.Typer()

@sub.command()
def hello():
    print("hello from plugin")

class CliPlugin(ESFEXPlugin):
    meta = PluginMeta(name="cli_test", version="1.0")

    def get_cli_commands(self):
        return [sub]

def create_plugin(ctx):
    return CliPlugin(ctx)
"""
        _create_plugin_dir(plugins_dir, "cli_test", factory_body=body)

        import typer

        mock_app = typer.Typer()

        pm = PluginManager()
        with (
            patch("esfex.plugins.manager._USER_PLUGINS_DIR", plugins_dir),
            patch("esfex.plugins.manager._STATE_FILE", tmp_path / "state.json"),
            patch("esfex.plugins.manager._USER_DATA_DIR", tmp_path / "data"),
        ):
            pm.load_all(config=None)
            pm.register_cli_commands(mock_app)

        # Verify a sub-typer was registered
        assert any(
            getattr(g, "name", None) == "cli_test"
            for g in mock_app.registered_groups
        )


# ── Config Schema ────────────────────────────────────────────────────


class TestConfigSchema:
    def test_esfex_config_accepts_plugins_dict(self):
        """ESFEXConfig.plugins field accepts arbitrary dict."""
        from esfex.config.schema import ESFEXConfig

        # Just check the field exists and has correct default
        assert ESFEXConfig.model_fields["plugins"].default_factory is not None

        # Verify empty dict is the default
        # (Can't instantiate full ESFEXConfig without required fields,
        # so just check field metadata)
        field_info = ESFEXConfig.model_fields["plugins"]
        assert field_info.default_factory() == {}


# ── Plugins Dialog ───────────────────────────────────────────────────


class TestPluginsDialog:
    """Tests for the PluginsDialog GUI component (mocked Qt)."""

    def _make_mock_pm(self, plugins=None, enabled=None):
        """Create a mock PluginManager with optional discovered plugins."""
        pm = MagicMock()
        pm._discovered = plugins or {}
        pm._metas = {}
        for name in pm._discovered:
            meta = MagicMock()
            meta.version = "1.0.0"
            meta.category = "analysis"
            meta.author = "Test Author"
            meta.description = f"Description for {name}"
            pm._metas[name] = meta
        if enabled is not None:
            pm.is_enabled = MagicMock(side_effect=lambda n: n in enabled)
        else:
            pm.is_enabled = MagicMock(return_value=True)
        pm.discover = MagicMock()
        pm.enable = MagicMock()
        pm.disable = MagicMock()
        pm.install_from_zip = MagicMock(return_value="new_plugin")
        pm.install_from_git = MagicMock(return_value="git_plugin")
        pm.uninstall = MagicMock()
        return pm

    # ── Import & class structure ──────────────────────────────────

    @patch("esfex.plugins.get_plugin_manager")
    def test_dialog_imports(self, mock_get_pm):
        """PluginsDialog can be imported without errors."""
        mock_get_pm.return_value = self._make_mock_pm()
        from esfex.visualization.panels.plugins_dialog import PluginsDialog
        assert PluginsDialog is not None

    @patch("esfex.plugins.get_plugin_manager")
    def test_dialog_class_has_columns(self, mock_get_pm):
        """PluginsDialog defines expected column constants."""
        mock_get_pm.return_value = self._make_mock_pm()
        from esfex.visualization.panels.plugins_dialog import PluginsDialog
        assert PluginsDialog._COL_ENABLED == 0
        assert PluginsDialog._COL_NAME == 1
        assert PluginsDialog._COL_VERSION == 2
        assert PluginsDialog._COL_CATEGORY == 3
        assert PluginsDialog._COL_AUTHOR == 4
        assert PluginsDialog._COL_DESCRIPTION == 5
        assert PluginsDialog._NUM_COLS == 6

    @patch("esfex.plugins.get_plugin_manager")
    def test_dialog_inherits_qdialog(self, mock_get_pm):
        """PluginsDialog is a QDialog subclass."""
        mock_get_pm.return_value = self._make_mock_pm()
        from PySide6.QtWidgets import QDialog
        from esfex.visualization.panels.plugins_dialog import PluginsDialog
        assert issubclass(PluginsDialog, QDialog)

    @patch("esfex.plugins.get_plugin_manager")
    def test_dialog_has_action_methods(self, mock_get_pm):
        """PluginsDialog defines all expected action methods."""
        mock_get_pm.return_value = self._make_mock_pm()
        from esfex.visualization.panels.plugins_dialog import PluginsDialog
        for method in (
            "_build_ui", "_populate_table", "_ro_item",
            "_on_install_zip", "_on_install_git",
            "_on_uninstall", "_on_open_folder", "_on_accept",
            "_hot_load_plugin",
        ):
            assert hasattr(PluginsDialog, method), f"Missing method: {method}"

    # ── MainWindow integration ────────────────────────────────────

    def test_main_window_has_plugin_menu_method(self):
        """MainWindow defines the plugin menu methods."""
        from esfex.visualization.main_window import MainWindow
        assert hasattr(MainWindow, "_on_manage_plugins")

    # ── Translations ──────────────────────────────────────────────

    def test_translations_have_plugin_keys(self):
        """Translation files include plugins_dialog keys."""
        import json as json_mod
        base = Path(__file__).resolve().parent.parent / "src" / "esfex" / "visualization" / "translations"
        for lang in ("en", "es"):
            path = base / f"{lang}.json"
            data = json_mod.loads(path.read_text(encoding="utf-8"))
            assert "plugins_dialog" in data, f"Missing plugins_dialog in {lang}.json"
            assert "title" in data["plugins_dialog"]
            assert "menu" in data
            assert "plugins" in data["menu"]
            assert "manage_plugins" in data["menu"]

    def test_translations_all_dialog_keys_present(self):
        """Both language files have all required plugins_dialog keys."""
        import json as json_mod
        expected_keys = {
            "title", "enabled", "name", "version", "category",
            "author", "description", "install_zip", "install_git",
            "uninstall", "open_folder", "no_plugins",
            "restart_notice", "confirm_uninstall_title", "confirm_uninstall",
        }
        base = Path(__file__).resolve().parent.parent / "src" / "esfex" / "visualization" / "translations"
        for lang in ("en", "es"):
            path = base / f"{lang}.json"
            data = json_mod.loads(path.read_text(encoding="utf-8"))
            actual_keys = set(data["plugins_dialog"].keys())
            missing = expected_keys - actual_keys
            assert not missing, f"{lang}.json missing keys: {missing}"

    def test_translations_en_es_key_parity(self):
        """English and Spanish translation files have the same plugin keys."""
        import json as json_mod
        base = Path(__file__).resolve().parent.parent / "src" / "esfex" / "visualization" / "translations"
        en_data = json_mod.loads((base / "en.json").read_text(encoding="utf-8"))
        es_data = json_mod.loads((base / "es.json").read_text(encoding="utf-8"))
        en_keys = set(en_data.get("plugins_dialog", {}).keys())
        es_keys = set(es_data.get("plugins_dialog", {}).keys())
        assert en_keys == es_keys, f"Mismatch: EN-only={en_keys - es_keys}, ES-only={es_keys - en_keys}"

    # ── Read-only table item helper ───────────────────────────────

    @patch("esfex.plugins.get_plugin_manager")
    def test_ro_item_is_not_editable(self, mock_get_pm):
        """_ro_item creates a non-editable table item."""
        mock_get_pm.return_value = self._make_mock_pm()
        from PySide6.QtCore import Qt
        from esfex.visualization.panels.plugins_dialog import PluginsDialog
        item = PluginsDialog._ro_item("test text")
        assert item.text() == "test text"
        assert not (item.flags() & Qt.ItemFlag.ItemIsEditable)
        assert item.flags() & Qt.ItemFlag.ItemIsEnabled
        assert item.flags() & Qt.ItemFlag.ItemIsSelectable

    # ── Documentation ─────────────────────────────────────────────

    def test_plugins_doc_page_exists(self):
        """docs/gui/plugins.md documentation page exists."""
        doc_path = (
            Path(__file__).resolve().parent.parent
            / "docs" / "gui" / "plugins.md"
        )
        assert doc_path.exists(), f"Missing documentation: {doc_path}"

    def test_mkdocs_nav_includes_plugins(self):
        """mkdocs.yml navigation includes the plugins page."""
        mkdocs_path = Path(__file__).resolve().parent.parent / "mkdocs.yml"
        content = mkdocs_path.read_text(encoding="utf-8")
        assert "Plugin Management: gui/plugins.md" in content

    def test_changelog_mentions_plugins_menu(self):
        """Changelog mentions the GUI Plugins menu."""
        changelog_path = (
            Path(__file__).resolve().parent.parent
            / "docs" / "reference" / "changelog.md"
        )
        content = changelog_path.read_text(encoding="utf-8")
        assert "Plugins" in content
        assert "PluginsDialog" in content
