"""Coverage tests for esfex.plugins.manager.

These tests exercise the directory-based plugin manager without touching
the user's real ~/.esfex directory: the module-level path constants are
monkeypatched onto a temp dir via the ``mgr`` fixture.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

import esfex.plugins.manager as mod
from esfex.plugins.manager import (
    PluginManager,
    _is_safe_name,
    _validate_zip_paths,
    _hash_directory,
    _read_state,
    _write_state,
    get_plugin_manager,
    reset_plugin_manager,
)
from esfex.plugins.protocol import ESFEXPlugin, PluginMeta, PluginContext


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect all module-level path constants into a temp dir."""
    user_plugins = tmp_path / "plugins"
    user_data = tmp_path / "plugin_data"
    state_file = tmp_path / "plugins.json"
    monkeypatch.setattr(mod, "_USER_PLUGINS_DIR", user_plugins)
    monkeypatch.setattr(mod, "_USER_DATA_DIR", user_data)
    monkeypatch.setattr(mod, "_STATE_FILE", state_file)
    # Make sure ESFEX_PLUGIN_PATH does not leak from the environment.
    monkeypatch.delenv("ESFEX_PLUGIN_PATH", raising=False)
    return tmp_path


@pytest.fixture
def mgr(sandbox):
    return PluginManager()


def _make_plugin_dir(
    root: Path,
    name: str,
    *,
    version: str = "1.0.0",
    extra_meta: dict | None = None,
    init_body: str | None = None,
    write_init: bool = True,
    write_meta: bool = True,
) -> Path:
    """Create a directory-based plugin under *root*."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if write_meta:
        meta = {"name": name, "version": version}
        if extra_meta:
            meta.update(extra_meta)
        (d / "plugin.json").write_text(json.dumps(meta), encoding="utf-8")
    if write_init:
        if init_body is None:
            init_body = (
                "from esfex.plugins.protocol import ESFEXPlugin, PluginMeta\n"
                "class P(ESFEXPlugin):\n"
                f"    meta = PluginMeta(name={name!r}, version={version!r})\n"
                "def create_plugin(ctx):\n"
                "    return P(ctx)\n"
            )
        (d / "__init__.py").write_text(init_body, encoding="utf-8")
    return d


def _zip_from_dir(src_dir: Path, zip_path: Path, arc_root: str) -> Path:
    """Zip *src_dir*'s contents under an *arc_root* directory."""
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in src_dir.rglob("*"):
            if f.is_file():
                zf.write(f, f"{arc_root}/{f.relative_to(src_dir)}")
    return zip_path


# ── _is_safe_name ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["foo", "foo_bar", "foo-bar", "Foo123", "a", "x-y_z9"],
)
def test_is_safe_name_accepts_valid(name):
    assert _is_safe_name(name) is True


@pytest.mark.parametrize(
    "name",
    ["", "../etc", "foo/bar", "foo.bar", "_leading", "-leading", "a b", "foo..bar"],
)
def test_is_safe_name_rejects_invalid(name):
    assert _is_safe_name(name) is False


# ── _validate_zip_paths ──────────────────────────────────────────────────


def test_validate_zip_paths_ok(tmp_path):
    z = tmp_path / "ok.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("plug/plugin.json", "{}")
        zf.writestr("plug/__init__.py", "")
    with zipfile.ZipFile(z, "r") as zf:
        # Should not raise.
        _validate_zip_paths(zf, tmp_path)


def test_validate_zip_paths_zip_slip(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escape.txt", "pwned")
    with zipfile.ZipFile(z, "r") as zf:
        with pytest.raises(ValueError, match="escapes target"):
            _validate_zip_paths(zf, target)


# ── _hash_directory ──────────────────────────────────────────────────────


def test_hash_directory_deterministic_and_sensitive(tmp_path):
    d = tmp_path / "h"
    d.mkdir()
    (d / "a.txt").write_text("hello")
    h1 = _hash_directory(d)
    h2 = _hash_directory(d)
    assert h1 == h2
    assert len(h1) == 64
    (d / "a.txt").write_text("changed")
    assert _hash_directory(d) != h1


# ── _read_state / _write_state ───────────────────────────────────────────


def test_read_state_missing_returns_empty(sandbox):
    assert _read_state() == {}


def test_write_then_read_state_roundtrip(sandbox):
    _write_state({"disabled": ["x"], "n": 1})
    assert _read_state() == {"disabled": ["x"], "n": 1}


def test_read_state_corrupted_returns_empty(sandbox):
    mod._STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    mod._STATE_FILE.write_text("{not json", encoding="utf-8")
    assert _read_state() == {}


# ── discover ─────────────────────────────────────────────────────────────


def test_discover_empty(mgr):
    assert mgr.discover() == []
    assert mgr.discovered == {}
    assert mgr.metas == {}


def test_discover_finds_plugin(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    names = mgr.discover()
    assert names == ["alpha"]
    assert "alpha" in mgr.discovered
    meta = mgr.metas["alpha"]
    assert isinstance(meta, PluginMeta)
    assert meta.version == "1.0.0"
    assert meta.category == "general"


def test_discover_skips_missing_init(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "noinit", write_init=False)
    assert mgr.discover() == []


def test_discover_skips_missing_meta(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "nometa", write_meta=False)
    assert mgr.discover() == []


def test_discover_skips_invalid_json(mgr, sandbox):
    d = mod._USER_PLUGINS_DIR / "bad"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text("{not valid", encoding="utf-8")
    (d / "__init__.py").write_text("", encoding="utf-8")
    assert mgr.discover() == []


def test_discover_skips_meta_missing_required_keys(mgr, sandbox):
    d = mod._USER_PLUGINS_DIR / "incomplete"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(json.dumps({"name": "incomplete"}), encoding="utf-8")
    (d / "__init__.py").write_text("", encoding="utf-8")
    assert mgr.discover() == []


def test_discover_skips_unsafe_name_in_meta(mgr, sandbox):
    d = mod._USER_PLUGINS_DIR / "safe_dir"
    d.mkdir(parents=True)
    (d / "plugin.json").write_text(
        json.dumps({"name": "../evil", "version": "1.0"}), encoding="utf-8"
    )
    (d / "__init__.py").write_text("", encoding="utf-8")
    assert mgr.discover() == []


def test_discover_skips_non_directory_entries(mgr, sandbox):
    mod._USER_PLUGINS_DIR.mkdir(parents=True)
    (mod._USER_PLUGINS_DIR / "loose_file.txt").write_text("x")
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    assert mgr.discover() == ["alpha"]


def test_discover_dedups_same_name(mgr, sandbox, tmp_path, monkeypatch):
    # Same plugin name appears in user dir and in an env-path dir; first wins.
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "dup", version="1.0.0")
    env_dir = tmp_path / "env"
    second = _make_plugin_dir(env_dir, "dup", version="9.9.9")
    monkeypatch.setenv("ESFEX_PLUGIN_PATH", str(env_dir))
    names = mgr.discover()
    assert names == ["dup"]
    # The user-dir one wins (discovered first).
    assert mgr.discovered["dup"] == mod._USER_PLUGINS_DIR / "dup"
    assert mgr.metas["dup"].version == "1.0.0"
    assert second.exists()


def test_discover_env_path_ignores_nonexistent_dirs(mgr, sandbox, monkeypatch):
    monkeypatch.setenv("ESFEX_PLUGIN_PATH", "/nonexistent/xyz")
    assert mgr.discover() == []


def test_discover_project_dir_skipped_by_default(mgr, sandbox, tmp_path):
    project = tmp_path / "proj"
    _make_plugin_dir(project / ".esfex" / "plugins", "projplug")
    # Default: project plugins are NOT scanned.
    assert mgr.discover(project_dir=project) == []


def test_discover_project_dir_included_when_opted_in(mgr, sandbox, tmp_path):
    project = tmp_path / "proj"
    _make_plugin_dir(project / ".esfex" / "plugins", "projplug")
    names = mgr.discover(project_dir=project, include_project_dir=True)
    assert names == ["projplug"]


# ── load_all / load_single ───────────────────────────────────────────────


def test_load_all_legacy_mode_loads(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha", extra_meta={"priority": 5})
    mgr.load_all()
    assert [p.meta.name for p in mgr.plugins] == ["alpha"]
    # data_dir was created
    assert (mod._USER_DATA_DIR / "alpha").is_dir()


def test_load_all_is_idempotent(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    mgr.load_all()
    first = mgr.plugins
    mgr.load_all()  # second call should be a no-op (already loaded)
    assert [p.meta.name for p in mgr.plugins] == [p.meta.name for p in first]


def test_load_all_sorts_by_priority_then_name(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "bbb", extra_meta={"priority": 1})
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "aaa", extra_meta={"priority": 1})
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "zzz", extra_meta={"priority": 0})
    mgr.load_all()
    assert [p.meta.name for p in mgr.plugins] == ["zzz", "aaa", "bbb"]


def test_load_all_skips_disabled(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "beta")
    _write_state({"disabled": ["beta"]})
    mgr.load_all()
    assert [p.meta.name for p in mgr.plugins] == ["alpha"]


def test_load_all_setup_called(mgr, sandbox):
    init_body = (
        "from esfex.plugins.protocol import ESFEXPlugin, PluginMeta\n"
        "class P(ESFEXPlugin):\n"
        "    meta = PluginMeta(name='sp', version='1.0')\n"
        "    def setup(self):\n"
        "        (self.context.data_dir / 'setup_ran').write_text('1')\n"
        "def create_plugin(ctx):\n"
        "    return P(ctx)\n"
    )
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "sp", init_body=init_body)
    mgr.load_all()
    assert (mod._USER_DATA_DIR / "sp" / "setup_ran").is_file()


def test_load_all_setup_exception_does_not_crash(mgr, sandbox):
    init_body = (
        "from esfex.plugins.protocol import ESFEXPlugin, PluginMeta\n"
        "class P(ESFEXPlugin):\n"
        "    meta = PluginMeta(name='boom', version='1.0')\n"
        "    def setup(self):\n"
        "        raise RuntimeError('nope')\n"
        "def create_plugin(ctx):\n"
        "    return P(ctx)\n"
    )
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "boom", init_body=init_body)
    mgr.load_all()  # should not raise
    assert [p.meta.name for p in mgr.plugins] == ["boom"]


def test_load_all_hash_allowlist_rejects_untrusted(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    # Activate allowlist mode by recording a (different) trusted hash entry.
    _write_state({"trusted_hashes": {"other": "deadbeef"}})
    mgr.load_all()
    # alpha's hash is not on record -> rejected
    assert mgr.plugins == []


def test_load_all_hash_allowlist_accepts_matching(mgr, sandbox):
    d = _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    good = _hash_directory(d)
    _write_state({"trusted_hashes": {"alpha": good}})
    mgr.load_all()
    assert [p.meta.name for p in mgr.plugins] == ["alpha"]


def test_load_plugin_no_create_plugin_returns_none(mgr, sandbox):
    init_body = "X = 1\n"  # no create_plugin
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "nofac", init_body=init_body)
    mgr.load_all()
    assert mgr.plugins == []


def test_load_plugin_factory_wrong_type_returns_none(mgr, sandbox):
    init_body = "def create_plugin(ctx):\n    return object()\n"
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "wrong", init_body=init_body)
    mgr.load_all()
    assert mgr.plugins == []


def test_load_plugin_import_error_returns_none(mgr, sandbox):
    init_body = "raise ImportError('missing dep')\n"
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "broken", init_body=init_body)
    mgr.load_all()
    assert mgr.plugins == []
    # module removed from sys.modules to allow retry
    assert "esfex_plugins.broken" not in sys.modules


def test_load_plugin_meta_from_json_overrides_class(mgr, sandbox):
    # Class meta says version 0.0; plugin.json says 2.5 — json wins.
    init_body = (
        "from esfex.plugins.protocol import ESFEXPlugin, PluginMeta\n"
        "class P(ESFEXPlugin):\n"
        "    meta = PluginMeta(name='ov', version='0.0')\n"
        "def create_plugin(ctx):\n"
        "    return P(ctx)\n"
    )
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "ov", version="2.5", init_body=init_body)
    mgr.load_all()
    assert mgr.plugins[0].meta.version == "2.5"


def test_load_single_requires_discovery(mgr, sandbox):
    assert mgr.load_single("ghost") is None


def test_load_single_loads_and_appends(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    mgr.discover()
    p = mgr.load_single("alpha")
    assert p is not None
    assert [x.meta.name for x in mgr.plugins] == ["alpha"]


def test_load_single_already_loaded_returns_existing(mgr, sandbox):
    _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    mgr.discover()
    p1 = mgr.load_single("alpha")
    p2 = mgr.load_single("alpha")
    assert p1 is p2
    assert len(mgr.plugins) == 1


# ── enable / disable / is_enabled ────────────────────────────────────────


def test_is_enabled_default_true(mgr, sandbox):
    assert mgr.is_enabled("anything") is True


def test_disable_then_enable(mgr, sandbox):
    mgr.disable("alpha")
    assert mgr.is_enabled("alpha") is False
    assert _read_state()["disabled"] == ["alpha"]
    mgr.enable("alpha")
    assert mgr.is_enabled("alpha") is True
    assert _read_state()["disabled"] == []


def test_disable_idempotent(mgr, sandbox):
    mgr.disable("alpha")
    mgr.disable("alpha")
    assert _read_state()["disabled"] == ["alpha"]


def test_enable_not_disabled_is_noop(mgr, sandbox):
    mgr.enable("never")  # not present -> no state written / no crash
    assert mgr.is_enabled("never") is True


# ── install_from_zip ─────────────────────────────────────────────────────


def test_install_from_zip_success(mgr, sandbox, tmp_path):
    src = _make_plugin_dir(tmp_path / "src", "zipplug")
    zp = _zip_from_dir(src, tmp_path / "zipplug.zip", "zipplug")
    name = mgr.install_from_zip(zp)
    assert name == "zipplug"
    assert (mod._USER_PLUGINS_DIR / "zipplug" / "plugin.json").is_file()
    # Hash recorded as trusted
    trusted = _read_state()["trusted_hashes"]
    assert "zipplug" in trusted


def test_install_from_zip_unsafe_root(mgr, sandbox, tmp_path):
    zp = tmp_path / "bad.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("..evil/plugin.json", "{}")
    with pytest.raises(ValueError, match="unsafe"):
        mgr.install_from_zip(zp)


def test_install_from_zip_zip_slip(mgr, sandbox, tmp_path):
    zp = tmp_path / "slip.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        # single root so root_name detection passes, plus an escaping member
        zf.writestr("plug/ok.txt", "x")
        zf.writestr("plug/../../escape.txt", "x")
    with pytest.raises(ValueError, match="escapes target"):
        mgr.install_from_zip(zp)


def test_install_from_zip_existing_no_force(mgr, sandbox, tmp_path):
    src = _make_plugin_dir(tmp_path / "src", "dup")
    zp = _zip_from_dir(src, tmp_path / "dup.zip", "dup")
    mgr.install_from_zip(zp)
    with pytest.raises(ValueError, match="already exists"):
        mgr.install_from_zip(zp)


def test_install_from_zip_force_overwrites(mgr, sandbox, tmp_path):
    src = _make_plugin_dir(tmp_path / "src", "dup", version="1.0")
    zp = _zip_from_dir(src, tmp_path / "dup.zip", "dup")
    mgr.install_from_zip(zp)
    # rebuild with a new version
    src2 = _make_plugin_dir(tmp_path / "src2", "dup", version="2.0")
    zp2 = _zip_from_dir(src2, tmp_path / "dup2.zip", "dup")
    name = mgr.install_from_zip(zp2, force=True)
    assert name == "dup"
    meta = json.loads(
        (mod._USER_PLUGINS_DIR / "dup" / "plugin.json").read_text()
    )
    assert meta["version"] == "2.0"


def test_install_from_zip_invalid_plugin_cleans_up(mgr, sandbox, tmp_path):
    zp = tmp_path / "notplug.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        # valid safe root name but no plugin.json/__init__.py
        zf.writestr("notplug/readme.txt", "hi")
    with pytest.raises(ValueError, match="not a valid ESFEX plugin"):
        mgr.install_from_zip(zp)
    assert not (mod._USER_PLUGINS_DIR / "notplug").exists()


def test_install_from_zip_multiple_roots_uses_stem(mgr, sandbox, tmp_path):
    zp = tmp_path / "stemname.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("a/x.txt", "1")
        zf.writestr("b/y.txt", "2")
    # Two roots -> root_name falls back to zip stem "stemname"; not a valid
    # plugin so it raises after extraction-cleanup.
    with pytest.raises(ValueError, match="not a valid ESFEX plugin"):
        mgr.install_from_zip(zp)


# ── install_from_git (validation only — no network) ──────────────────────


def test_install_from_git_bad_scheme(mgr, sandbox):
    with pytest.raises(ValueError, match="Unsupported git URL scheme"):
        mgr.install_from_git("ftp://github.com/u/r.git")


def test_install_from_git_no_host(mgr, sandbox):
    with pytest.raises(ValueError, match="Could not extract host"):
        mgr.install_from_git("https:///path/only")


def test_install_from_git_untrusted_host_requires_consent(mgr, sandbox):
    with pytest.raises(ValueError, match="not in the default trusted"):
        mgr.install_from_git("https://evil.example.com/u/r.git")


def test_install_from_git_unsafe_target_name(mgr, sandbox):
    # Trusted host, but explicit target_name is unsafe.
    with pytest.raises(ValueError, match="unsafe"):
        mgr.install_from_git(
            "https://github.com/u/r.git", target_name="../escape"
        )


def test_install_from_git_existing_no_force(mgr, sandbox):
    # Pre-create the target so the existence check trips before any clone.
    mod._USER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    (mod._USER_PLUGINS_DIR / "r").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        mgr.install_from_git("https://github.com/u/r.git")


# ── trust_plugin / _trust_plugin / uninstall ─────────────────────────────


def test_trust_plugin_not_discovered_raises(mgr, sandbox):
    with pytest.raises(ValueError, match="not discovered"):
        mgr.trust_plugin("ghost")


def test_trust_plugin_records_hash(mgr, sandbox):
    d = _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    mgr.discover()
    mgr.trust_plugin("alpha")
    assert _read_state()["trusted_hashes"]["alpha"] == _hash_directory(d)


def test_uninstall_removes_dir_and_trust(mgr, sandbox):
    d = _make_plugin_dir(mod._USER_PLUGINS_DIR, "alpha")
    mgr.discover()
    mgr.trust_plugin("alpha")
    mgr.disable("alpha")
    mgr.uninstall("alpha")
    assert not d.exists()
    state = _read_state()
    assert "alpha" not in state.get("trusted_hashes", {})
    # uninstall calls enable() so it should not be disabled anymore
    assert "alpha" not in state.get("disabled", [])


def test_uninstall_unsafe_name_noop(mgr, sandbox):
    # Should just log + return, no exception.
    mgr.uninstall("../evil")


def test_uninstall_missing_dir_noop(mgr, sandbox):
    mgr.uninstall("ghost")  # not present -> warning only


# ── call_hook ────────────────────────────────────────────────────────────


def test_call_hook_collects_non_none(mgr, sandbox):
    class P(ESFEXPlugin):
        meta = PluginMeta(name="h1", version="1.0")

        def myhook(self, **kw):
            return kw.get("x", 0) + 1

    class Q(ESFEXPlugin):
        meta = PluginMeta(name="h2", version="1.0")

        def myhook(self, **kw):
            return None  # filtered out

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx), Q(ctx)]
    assert mgr.call_hook("myhook", x=10) == [11]


def test_call_hook_missing_method_skipped(mgr, sandbox):
    class P(ESFEXPlugin):
        meta = PluginMeta(name="h1", version="1.0")

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx)]
    assert mgr.call_hook("does_not_exist") == []


def test_call_hook_exception_isolated(mgr, sandbox):
    class Bad(ESFEXPlugin):
        meta = PluginMeta(name="bad", version="1.0")

        def myhook(self, **kw):
            raise RuntimeError("boom")

    class Good(ESFEXPlugin):
        meta = PluginMeta(name="good", version="1.0")

        def myhook(self, **kw):
            return "ok"

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [Bad(ctx), Good(ctx)]
    assert mgr.call_hook("myhook") == ["ok"]


# ── julia module accessors ───────────────────────────────────────────────


def test_get_julia_module_paths_filters_nonexistent(mgr, sandbox, tmp_path):
    real = tmp_path / "mod.jl"
    real.write_text("# jl")
    missing = tmp_path / "missing.jl"

    class P(ESFEXPlugin):
        meta = PluginMeta(name="jl", version="1.0")

        def get_julia_modules(self):
            return [real, missing]

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx)]
    assert mgr.get_julia_module_paths() == [real]


def test_register_julia_modules_handles_exception(mgr, sandbox):
    class P(ESFEXPlugin):
        meta = PluginMeta(name="jl", version="1.0")

        def get_julia_modules(self):
            raise RuntimeError("boom")

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx)]
    mgr.register_julia_modules()  # should not raise


def test_get_julia_module_paths_handles_exception(mgr, sandbox):
    class P(ESFEXPlugin):
        meta = PluginMeta(name="jl", version="1.0")

        def get_julia_modules(self):
            raise RuntimeError("boom")

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx)]
    assert mgr.get_julia_module_paths() == []


# ── CLI registration ─────────────────────────────────────────────────────


def test_register_cli_commands(mgr, sandbox):
    calls = []

    class FakeApp:
        def add_typer(self, sub_app, name):
            calls.append((sub_app, name))

    class P(ESFEXPlugin):
        meta = PluginMeta(name="cliplug", version="1.0")

        def get_cli_commands(self):
            return ["subapp1", "subapp2"]

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx)]
    app = FakeApp()
    mgr.register_cli_commands(app)
    assert calls == [("subapp1", "cliplug"), ("subapp2", "cliplug")]


def test_register_cli_commands_exception_isolated(mgr, sandbox):
    class P(ESFEXPlugin):
        meta = PluginMeta(name="cliplug", version="1.0")

        def get_cli_commands(self):
            raise RuntimeError("boom")

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx)]
    mgr.register_cli_commands(object())  # should not raise


# ── GUI registration ─────────────────────────────────────────────────────


class _TreePanel:
    def __init__(self):
        self.registered = []

    def register_plugin_category(self, key, label, element_type):
        self.registered.append((key, label, element_type))


class _Props:
    def __init__(self):
        self.forms = []

    def register_form(self, element_type, widget):
        self.forms.append((element_type, widget))


class _ResultsPanel:
    def __init__(self):
        self.vars = []

    def register_result_variable(self, *args):
        self.vars.append(args)


def test_register_gui_extensions_full(mgr, sandbox):
    tree = _TreePanel()
    props = _Props()
    results = _ResultsPanel()
    toolbar_seen = []
    menu_seen = []
    map_seen = []

    class W:
        _tree = tree
        _props = props
        _model = "MODEL"
        _results_panel = results
        _toolbar = "TOOLBAR"
        _map = "MAP"

        def menuBar(self):
            return "MENUBAR"

    class P(ESFEXPlugin):
        meta = PluginMeta(name="gui", version="1.0")

        def get_tree_categories(self):
            return [{"key": "k1", "label": "L1"}]

        def get_forms(self, model):
            assert model == "MODEL"
            return [("etype", "WIDGET")]

        def get_toolbar_actions(self, toolbar, win):
            toolbar_seen.append((toolbar, win))
            return []

        def get_menu_items(self, menu_bar, win):
            menu_seen.append((menu_bar, win))

        def get_result_variables(self):
            return [("disp", "key", "sum", "line")]

        def get_map_layers(self, map_widget):
            map_seen.append(map_widget)

        def get_translations(self):
            return {}

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    win = W()
    mgr._plugins = [P(ctx)]
    mgr.register_gui_extensions(win)

    # tree category: element_type defaults to key when absent
    assert tree.registered == [("k1", "L1", "k1")]
    assert props.forms == [("etype", "WIDGET")]
    assert results.vars == [("disp", "key", "sum", "line")]
    assert toolbar_seen == [("TOOLBAR", win)]
    assert menu_seen == [("MENUBAR", win)]
    assert map_seen == ["MAP"]


def test_register_gui_extensions_missing_window_attrs(mgr, sandbox):
    # A window without any of the expected attributes -> everything no-ops.
    class W:
        pass

    class P(ESFEXPlugin):
        meta = PluginMeta(name="gui", version="1.0")

        def get_tree_categories(self):
            return [{"key": "k", "label": "l"}]

        def get_forms(self, model):
            return [("e", "w")]

        def get_result_variables(self):
            return [("a", "b", "c", "d")]

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx)]
    mgr.register_gui_extensions(W())  # should not raise


def test_register_gui_extensions_isolates_failures(mgr, sandbox):
    class W:
        _tree = _TreePanel()

    class P(ESFEXPlugin):
        meta = PluginMeta(name="gui", version="1.0")

        def get_tree_categories(self):
            raise RuntimeError("boom")

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx)]
    mgr.register_gui_extensions(W())  # should not raise


def test_register_menu_with_non_callable_menubar(mgr, sandbox):
    seen = []

    class W:
        menuBar = "PLAIN_MENUBAR"  # not callable

    class P(ESFEXPlugin):
        meta = PluginMeta(name="gui", version="1.0")

        def get_menu_items(self, menu_bar, win):
            seen.append(menu_bar)

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._register_menu(P(ctx), W())
    assert seen == ["PLAIN_MENUBAR"]


# ── teardown_all ─────────────────────────────────────────────────────────


def test_teardown_all_reverse_order_and_resets(mgr, sandbox):
    order = []

    class P(ESFEXPlugin):
        def __init__(self, ctx, name):
            super().__init__(ctx)
            self.meta = PluginMeta(name=name, version="1.0")

        def teardown(self):
            order.append(self.meta.name)

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [P(ctx, "a"), P(ctx, "b"), P(ctx, "c")]
    mgr._loaded = True
    mgr.teardown_all()
    assert order == ["c", "b", "a"]
    assert mgr.plugins == []
    assert mgr._loaded is False


def test_teardown_all_exception_isolated(mgr, sandbox):
    class Bad(ESFEXPlugin):
        meta = PluginMeta(name="bad", version="1.0")

        def teardown(self):
            raise RuntimeError("boom")

    ctx = PluginContext(config=None, plugin_dir=sandbox, data_dir=sandbox)
    mgr._plugins = [Bad(ctx)]
    mgr.teardown_all()  # should not raise
    assert mgr.plugins == []


# ── singleton ────────────────────────────────────────────────────────────


def test_singleton_identity(sandbox):
    reset_plugin_manager()
    a = get_plugin_manager()
    b = get_plugin_manager()
    assert a is b
    reset_plugin_manager()
    c = get_plugin_manager()
    assert c is not a
    reset_plugin_manager()


def test_reset_plugin_manager_when_none():
    # Reset twice — second call hits the "_instance is None" branch.
    reset_plugin_manager()
    reset_plugin_manager()  # should not raise
