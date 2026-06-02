"""
Plugin manager for ESFEX.

Discovers, loads, and manages plugins from directory-based locations
(QGIS/KiCad style).  No pip or PyPI required — plugins are plain
directories with ``plugin.json`` + ``__init__.py``.

Scan order:
    1. ``~/.esfex/plugins/``
    2. ``<project_dir>/.esfex/plugins/``
    3. Directories in ``$ESFEX_PLUGIN_PATH`` (colon-separated)

Security hardening:
    - ZIP extraction validates every path against Zip Slip (CWE-22)
    - Plugin names are sanitized (alphanumeric + underscore/hyphen only)
    - ``git clone`` runs with hooks disabled to prevent pre-checkout RCE
    - Git URLs are restricted to ``https://`` and ``git://`` schemes
    - Installing over an existing plugin requires explicit confirmation
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional

from esfex.plugins.protocol import PluginContext, PluginMeta, ESFEXPlugin

logger = logging.getLogger(__name__)

_USER_PLUGINS_DIR = Path.home() / ".esfex" / "plugins"
_USER_DATA_DIR = Path.home() / ".esfex" / "plugin_data"
_STATE_FILE = Path.home() / ".esfex" / "plugins.json"

# Only alphanumeric, underscore, hyphen — no path separators, no dots
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Allowed git URL schemes
_ALLOWED_GIT_SCHEMES = ("https://", "git://")

# Hosts pre-trusted for `install_from_git` without an extra confirmation.
# Public code-hosting platforms whose URLs are at least syntactically
# vetted by the platform. Any other host requires `trust_host=True` from
# the caller so the GUI can prompt the user before cloning unknown
# origins (an attacker-supplied URL pointing at evil.example.com would
# otherwise clone+install with the same code path as a github URL).
_TRUSTED_GIT_HOSTS = frozenset({
    "github.com", "www.github.com",
    "gitlab.com", "www.gitlab.com",
    "bitbucket.org", "www.bitbucket.org",
    "codeberg.org", "www.codeberg.org",
    "git.sr.ht",
})


# ── Helpers ──────────────────────────────────────────────────────────────


def _is_safe_name(name: str) -> bool:
    """Check that *name* is safe for use as a directory name."""
    return bool(_SAFE_NAME_RE.match(name)) and ".." not in name


def _validate_zip_paths(zf: zipfile.ZipFile, target_root: Path) -> None:
    """Raise ValueError if any ZIP entry escapes *target_root* (Zip Slip)."""
    resolved_root = target_root.resolve()
    for info in zf.infolist():
        member_path = (target_root / info.filename).resolve()
        if not str(member_path).startswith(str(resolved_root) + os.sep) \
                and member_path != resolved_root:
            raise ValueError(
                f"Unsafe path in ZIP: {info.filename!r} escapes target "
                f"directory {target_root}"
            )


def _hash_directory(path: Path) -> str:
    """Compute a SHA-256 digest of all files in *path* for audit logging."""
    h = hashlib.sha256()
    for fpath in sorted(path.rglob("*")):
        if fpath.is_file():
            h.update(str(fpath.relative_to(path)).encode())
            h.update(fpath.read_bytes())
    return h.hexdigest()


def _read_state() -> dict[str, Any]:
    """Read the enable/disable state file."""
    if _STATE_FILE.is_file():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupted plugins.json — using defaults")
    return {}


def _write_state(state: dict[str, Any]) -> None:
    """Persist the enable/disable state file."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


class PluginManager:
    """Central registry for ESFEX plugins."""

    def __init__(self) -> None:
        self._discovered: dict[str, Path] = {}  # name → plugin_dir
        self._metas: dict[str, PluginMeta] = {}
        self._plugins: list[ESFEXPlugin] = []  # loaded & active, sorted
        self._loaded = False

    # ── Discovery ─────────────────────────────────────────────────────

    def discover(
        self,
        project_dir: Optional[Path] = None,
        include_project_dir: bool = False,
    ) -> list[str]:
        """Scan plugin directories and return discovered plugin names.

        Does NOT instantiate plugins — call :meth:`load_all` for that.

        Per default, plugins inside ``<project_dir>/.esfex/plugins/``
        are NOT scanned: a colleague's project (yaml + ``.esfex/``
        bundle) opened in the studio would otherwise auto-execute any
        ``__init__.py`` they placed there — open-and-pwn. Pass
        ``include_project_dir=True`` only after the user has explicitly
        consented to trust the project source.
        """
        self._discovered.clear()
        self._metas.clear()

        dirs_to_scan: list[Path] = [_USER_PLUGINS_DIR]

        if include_project_dir and project_dir is not None:
            local = project_dir / ".esfex" / "plugins"
            if local.is_dir():
                logger.info(
                    "Scanning project-local plugins under %s "
                    "(include_project_dir=True; caller must have asked "
                    "the user to trust this project)",
                    local,
                )
                dirs_to_scan.append(local)
        elif project_dir is not None:
            # Tell the user / log that we deliberately ignored project plugins.
            local = project_dir / ".esfex" / "plugins"
            if local.is_dir():
                logger.warning(
                    "Project at %s ships plugins in .esfex/plugins/ but "
                    "they were skipped (auto-loading project plugins is a "
                    "security risk). To enable them, the caller must pass "
                    "include_project_dir=True after asking the user.",
                    project_dir,
                )

        env_path = os.environ.get("ESFEX_PLUGIN_PATH", "")
        if env_path:
            for p in env_path.split(os.pathsep):
                d = Path(p)
                if d.is_dir():
                    dirs_to_scan.append(d)

        for scan_dir in dirs_to_scan:
            if not scan_dir.is_dir():
                continue
            for candidate in sorted(scan_dir.iterdir()):
                if not candidate.is_dir():
                    continue
                meta = self._read_plugin_meta(candidate)
                if meta is None:
                    continue
                name = meta.name
                if name in self._discovered:
                    logger.debug(
                        "Plugin %r already discovered at %s, skipping %s",
                        name,
                        self._discovered[name],
                        candidate,
                    )
                    continue
                self._discovered[name] = candidate
                self._metas[name] = meta

        logger.info("Discovered %d plugin(s): %s", len(self._discovered), list(self._discovered))
        return list(self._discovered)

    def _read_plugin_meta(self, plugin_dir: Path) -> Optional[PluginMeta]:
        """Read and validate ``plugin.json`` from *plugin_dir*."""
        meta_file = plugin_dir / "plugin.json"
        init_file = plugin_dir / "__init__.py"

        if not meta_file.is_file():
            return None
        if not init_file.is_file():
            logger.warning("Plugin dir %s has plugin.json but no __init__.py — skipping", plugin_dir)
            return None

        try:
            raw = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot read %s: %s", meta_file, exc)
            return None

        if "name" not in raw or "version" not in raw:
            logger.warning("plugin.json in %s missing 'name' or 'version'", plugin_dir)
            return None

        name = raw["name"]
        if not _is_safe_name(name):
            logger.warning(
                "Plugin name %r in %s contains unsafe characters — skipping",
                name, plugin_dir,
            )
            return None

        return PluginMeta(
            name=name,
            version=raw["version"],
            description=raw.get("description", ""),
            author=raw.get("author", ""),
            url=raw.get("url", ""),
            requires_plugins=raw.get("requires_plugins", []),
            priority=raw.get("priority", 0),
            category=raw.get("category", "general"),
            python_dependencies=raw.get("python_dependencies", []),
        )

    # ── Loading ───────────────────────────────────────────────────────

    def load_all(
        self,
        config: Any = None,
        gui_mode: bool = False,
        project_dir: Optional[Path] = None,
        include_project_dir: bool = False,
    ) -> None:
        """Discover (if not already done), load, and setup all enabled plugins.

        See :meth:`discover` for the ``include_project_dir`` semantics
        (default False so opening someone else's project doesn't auto-run
        their plugin code).
        """
        if self._loaded:
            return

        if not self._discovered:
            self.discover(
                project_dir=project_dir,
                include_project_dir=include_project_dir,
            )

        state = _read_state()
        disabled = set(state.get("disabled", []))
        # Hash-based allowlist. Once any hash is recorded (i.e. the user
        # has installed at least one plugin through install_from_zip /
        # install_from_git, which auto-trusts on success), unknown hashes
        # are rejected. An empty allowlist means "legacy mode" — we don't
        # break existing users who already have ~/.esfex/plugins/
        # populated from before this allowlist existed. The migration is
        # implicit: the first deliberate install activates the allowlist.
        trusted_hashes: dict[str, str] = state.get("trusted_hashes", {})
        legacy_mode = not trusted_hashes

        plugins: list[ESFEXPlugin] = []
        for name, plugin_dir in self._discovered.items():
            if name in disabled:
                logger.info("Plugin %r is disabled — skipping", name)
                continue
            if not legacy_mode:
                try:
                    current_hash = _hash_directory(plugin_dir)
                except Exception:
                    logger.exception(
                        "Cannot hash plugin %r at %s — skipping for safety",
                        name, plugin_dir,
                    )
                    continue
                expected = trusted_hashes.get(name)
                if expected != current_hash:
                    logger.warning(
                        "Plugin %r at %s has hash %s which does not match "
                        "the trusted hash on record (%s). Skipping. "
                        "Re-install via the GUI Plugins dialog to approve.",
                        name, plugin_dir, current_hash, expected,
                    )
                    continue
            plugin = self._load_plugin(name, plugin_dir, config, gui_mode)
            if plugin is not None:
                plugins.append(plugin)

        # Sort by priority, then by name for stability
        plugins.sort(key=lambda p: (p.meta.priority, p.meta.name))

        # Topological validation (warn about missing deps, don't block)
        loaded_names = {p.meta.name for p in plugins}
        for p in plugins:
            for dep in p.meta.requires_plugins:
                if dep not in loaded_names:
                    logger.warning(
                        "Plugin %r requires %r which is not loaded", p.meta.name, dep
                    )

        self._plugins = plugins
        self._loaded = True

        # Call setup on each plugin
        for p in self._plugins:
            try:
                p.setup()
            except Exception:
                logger.exception("Plugin %r setup() failed", p.meta.name)

        logger.info("Loaded %d plugin(s)", len(self._plugins))

    def _load_plugin(
        self,
        name: str,
        plugin_dir: Path,
        config: Any,
        gui_mode: bool,
    ) -> Optional[ESFEXPlugin]:
        """Import a single plugin via importlib and call its factory."""
        if not _is_safe_name(name):
            logger.warning("Refusing to load plugin with unsafe name: %r", name)
            return None

        init_file = plugin_dir / "__init__.py"
        module_name = f"esfex_plugins.{name}"

        # Audit: log hash of plugin contents
        try:
            digest = _hash_directory(plugin_dir)
            logger.info(
                "Loading plugin %r from %s (sha256=%s)",
                name, plugin_dir, digest,
            )
        except Exception:
            logger.warning("Could not compute hash for plugin %r", name)

        try:
            # Ensure the parent namespace package exists
            if "esfex_plugins" not in sys.modules:
                import types
                ns_pkg = types.ModuleType("esfex_plugins")
                ns_pkg.__path__ = []
                ns_pkg.__package__ = "esfex_plugins"
                sys.modules["esfex_plugins"] = ns_pkg

            spec = importlib.util.spec_from_file_location(
                module_name,
                init_file,
                submodule_search_locations=[str(plugin_dir)],
            )
            if spec is None or spec.loader is None:
                logger.warning("Cannot create module spec for plugin %r", name)
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            factory = getattr(module, "create_plugin", None)
            if factory is None:
                logger.warning(
                    "Plugin %r __init__.py has no create_plugin() function", name
                )
                return None

            data_dir = _USER_DATA_DIR / name
            data_dir.mkdir(parents=True, exist_ok=True)

            ctx = PluginContext(
                config=config,
                plugin_dir=plugin_dir,
                data_dir=data_dir,
                gui_mode=gui_mode,
            )
            plugin = factory(ctx)

            if not isinstance(plugin, ESFEXPlugin):
                logger.warning(
                    "Plugin %r create_plugin() did not return a ESFEXPlugin instance",
                    name,
                )
                return None

            # Ensure meta is set (prefer plugin.json over class attribute)
            if name in self._metas:
                plugin.meta = self._metas[name]

            return plugin

        except Exception:
            logger.exception("Failed to load plugin %r from %s", name, plugin_dir)
            # Remove from sys.modules to allow retry
            sys.modules.pop(module_name, None)
            return None

    # ── Hot-load a single plugin ─────────────────────────────────────

    def load_single(
        self,
        name: str,
        config: Any = None,
        gui_mode: bool = False,
    ) -> Optional[ESFEXPlugin]:
        """Load a single plugin by name and add it to the active list.

        Used for hot-loading after install — does **not** require restart.
        Returns the loaded plugin instance, or *None* on failure.
        """
        if name not in self._discovered:
            logger.warning("Plugin %r not discovered — call discover() first", name)
            return None

        # Skip if already loaded
        for p in self._plugins:
            if p.meta.name == name:
                logger.info("Plugin %r is already loaded", name)
                return p

        plugin_dir = self._discovered[name]
        plugin = self._load_plugin(name, plugin_dir, config, gui_mode)
        if plugin is None:
            return None

        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: (p.meta.priority, p.meta.name))

        try:
            plugin.setup()
        except Exception:
            logger.exception("Plugin %r setup() failed", name)

        logger.info("Hot-loaded plugin %r", name)
        return plugin

    # ── Enable / Disable ──────────────────────────────────────────────

    def is_enabled(self, name: str) -> bool:
        """Check if a plugin is enabled (not in disabled list)."""
        state = _read_state()
        return name not in state.get("disabled", [])

    def enable(self, name: str) -> None:
        """Enable a previously disabled plugin."""
        state = _read_state()
        disabled = state.get("disabled", [])
        if name in disabled:
            disabled.remove(name)
            state["disabled"] = disabled
            _write_state(state)
            logger.info("Enabled plugin %r", name)

    def disable(self, name: str) -> None:
        """Disable a plugin (persists across sessions)."""
        state = _read_state()
        disabled = state.get("disabled", [])
        if name not in disabled:
            disabled.append(name)
            state["disabled"] = disabled
            _write_state(state)
            logger.info("Disabled plugin %r", name)

    # ── Install / Uninstall ───────────────────────────────────────────

    def install_from_zip(
        self,
        zip_path: Path,
        *,
        force: bool = False,
    ) -> str:
        """Extract a plugin ZIP into the user plugins directory.

        Validates all paths against Zip Slip before extraction.
        Raises *ValueError* if the ZIP contains unsafe paths or is not a
        valid plugin, or if the target already exists and *force* is False.

        Returns the plugin name on success.
        """
        _USER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            # Determine root directory inside ZIP
            names = zf.namelist()
            roots = {n.split("/")[0] for n in names if "/" in n}
            if len(roots) == 1:
                root_name = roots.pop()
            else:
                root_name = zip_path.stem

            if not _is_safe_name(root_name):
                raise ValueError(
                    f"ZIP root directory name {root_name!r} contains unsafe "
                    "characters. Plugin directory names must be alphanumeric "
                    "with underscores or hyphens only."
                )

            target = _USER_PLUGINS_DIR / root_name

            # Validate ALL paths before extracting anything (Zip Slip)
            _validate_zip_paths(zf, _USER_PLUGINS_DIR)

            # Check for overwrite
            if target.exists() and not force:
                raise ValueError(
                    f"Plugin directory {target.name!r} already exists. "
                    "Use force=True or uninstall the existing plugin first."
                )

            if target.exists():
                shutil.rmtree(target)

            zf.extractall(_USER_PLUGINS_DIR)

        # Verify it's a valid plugin
        meta = self._read_plugin_meta(target)
        if meta is None:
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError(
                f"Extracted directory {target} is not a valid ESFEX plugin "
                "(missing plugin.json or __init__.py)"
            )

        installed_hash = _hash_directory(target)
        # An explicit install via this method is an implicit trust grant
        # (the user picked the file). Record the hash so later load_all
        # accepts it (and rejects on tampering).
        self._trust_plugin(meta.name, installed_hash)
        logger.info(
            "Installed plugin %r from ZIP at %s (sha256=%s, trusted)",
            meta.name, target, installed_hash,
        )
        return meta.name

    def install_from_git(
        self,
        url: str,
        target_name: Optional[str] = None,
        *,
        force: bool = False,
        trust_host: bool = False,
    ) -> str:
        """Clone a git repository into the user plugins directory.

        Only ``https://`` and ``git://`` URLs are accepted. Hosts not in
        ``_TRUSTED_GIT_HOSTS`` require ``trust_host=True`` from the
        caller (which is what gives the GUI a place to ask the user
        before cloning from an unknown origin — pasting an
        attacker-supplied URL pointing at evil.example.com would
        otherwise install + hot-load arbitrary code).
        Git hooks are disabled during clone to prevent pre-checkout RCE.
        Raises *ValueError* if validation fails or if the target already
        exists and *force* is False.

        Returns the plugin name on success.
        """
        # Validate URL scheme
        if not any(url.startswith(s) for s in _ALLOWED_GIT_SCHEMES):
            raise ValueError(
                f"Unsupported git URL scheme: {url!r}. "
                f"Only {', '.join(_ALLOWED_GIT_SCHEMES)} are allowed."
            )

        # Validate host: untrusted hosts require explicit caller consent
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError(f"Could not extract host from URL {url!r}")
        if host not in _TRUSTED_GIT_HOSTS and not trust_host:
            raise ValueError(
                f"Host {host!r} is not in the default trusted git hosts "
                f"({sorted(_TRUSTED_GIT_HOSTS)}). Confirm via "
                "trust_host=True if you know the source."
            )

        _USER_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)

        if target_name is None:
            # Derive name from URL: https://github.com/user/esfex-weather.git → esfex-weather
            target_name = url.rstrip("/").rsplit("/", 1)[-1]
            if target_name.endswith(".git"):
                target_name = target_name[:-4]

        if not _is_safe_name(target_name):
            raise ValueError(
                f"Derived directory name {target_name!r} contains unsafe "
                "characters. Provide a safe target_name explicitly."
            )

        target = _USER_PLUGINS_DIR / target_name

        # Check for overwrite
        if target.exists() and not force:
            raise ValueError(
                f"Plugin directory {target.name!r} already exists. "
                "Use force=True or uninstall the existing plugin first."
            )

        if target.exists():
            shutil.rmtree(target)

        # Clone with hooks disabled to prevent RCE via post-checkout hooks.
        # Use a temporary empty dir as hooksPath so no hooks can execute.
        with tempfile.TemporaryDirectory() as empty_hooks:
            subprocess.run(
                [
                    "git",
                    "-c", f"core.hooksPath={empty_hooks}",
                    "clone",
                    "--depth", "1",
                    url,
                    str(target),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

        meta = self._read_plugin_meta(target)
        if meta is None:
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError(
                f"Cloned directory {target} is not a valid ESFEX plugin "
                "(missing plugin.json or __init__.py)"
            )

        installed_hash = _hash_directory(target)
        self._trust_plugin(meta.name, installed_hash)
        logger.info(
            "Installed plugin %r from git at %s (sha256=%s, trusted)",
            meta.name, target, installed_hash,
        )
        return meta.name

    def _trust_plugin(self, name: str, plugin_hash: str) -> None:
        """Record the (name, hash) pair in the allowlist persisted in state."""
        state = _read_state()
        trusted = state.setdefault("trusted_hashes", {})
        trusted[name] = plugin_hash
        _write_state(state)

    def trust_plugin(self, name: str) -> None:
        """Public API: explicitly mark an already-installed plugin trusted.

        For use by the GUI Plugins dialog when the user approves a
        plugin discovered in ``~/.esfex/plugins/`` that pre-dates the
        allowlist (or whose hash changed and needs re-approval).
        """
        plugin_dir = self._discovered.get(name)
        if plugin_dir is None:
            raise ValueError(f"Plugin {name!r} not discovered")
        self._trust_plugin(name, _hash_directory(plugin_dir))

    def uninstall(self, name: str) -> None:
        """Remove a plugin directory from the user plugins directory."""
        if not _is_safe_name(name):
            logger.warning("Refusing to uninstall plugin with unsafe name: %r", name)
            return

        target = _USER_PLUGINS_DIR / name
        if target.is_dir():
            shutil.rmtree(target)
            logger.info("Uninstalled plugin %r", name)
        else:
            logger.warning("Plugin %r not found at %s", name, target)

        # Also remove from disabled list and from the trusted hash
        # allowlist (a future re-install will record its new hash).
        self.enable(name)
        state = _read_state()
        trusted = state.get("trusted_hashes", {})
        if name in trusted:
            del trusted[name]
            _write_state(state)

    # ── Hook dispatch ─────────────────────────────────────────────────

    def call_hook(self, hook_name: str, **kwargs: Any) -> list[Any]:
        """Call *hook_name* on every loaded plugin, collecting return values.

        Each invocation is wrapped in ``try/except`` — a broken plugin
        logs an error but never crashes the core.
        """
        results: list[Any] = []
        for plugin in self._plugins:
            method = getattr(plugin, hook_name, None)
            if method is None:
                continue
            try:
                rv = method(**kwargs)
                if rv is not None:
                    results.append(rv)
            except Exception:
                logger.exception(
                    "Plugin %r hook %r raised an exception",
                    plugin.meta.name,
                    hook_name,
                )
        return results

    # ── Julia registration ────────────────────────────────────────────

    def register_julia_modules(self) -> None:
        """Collect Julia modules from plugins and include() them.

        These are runtime overlays — they do NOT modify ESFEX source files.
        """
        for plugin in self._plugins:
            try:
                modules = plugin.get_julia_modules()
                for jl_path in modules:
                    if jl_path.is_file():
                        logger.info(
                            "Plugin %r: registering Julia module %s",
                            plugin.meta.name,
                            jl_path,
                        )
                        # Actual inclusion happens via the Julia bridge
                        # when the Julia runtime is initialized
                    else:
                        logger.warning(
                            "Plugin %r: Julia module not found: %s",
                            plugin.meta.name,
                            jl_path,
                        )
            except Exception:
                logger.exception(
                    "Plugin %r get_julia_modules() failed", plugin.meta.name
                )

    def get_julia_module_paths(self) -> list[Path]:
        """Return all Julia module paths from loaded plugins."""
        paths: list[Path] = []
        for plugin in self._plugins:
            try:
                for jl_path in plugin.get_julia_modules():
                    if jl_path.is_file():
                        paths.append(jl_path)
            except Exception:
                logger.exception(
                    "Plugin %r get_julia_modules() failed", plugin.meta.name
                )
        return paths

    # ── CLI registration ──────────────────────────────────────────────

    def register_cli_commands(self, app: Any) -> None:
        """Register plugin CLI sub-commands on the Typer *app*."""
        for plugin in self._plugins:
            try:
                sub_apps = plugin.get_cli_commands()
                for sub_app in sub_apps:
                    app.add_typer(sub_app, name=plugin.meta.name)
            except Exception:
                logger.exception(
                    "Plugin %r get_cli_commands() failed", plugin.meta.name
                )

    # ── GUI registration ──────────────────────────────────────────────

    def register_gui_extensions(self, window: Any) -> None:
        """Register all GUI extensions from loaded plugins on *window*.

        Registers tree categories, forms, toolbar actions, menu items,
        result variables, map layers, and translations.
        """
        for plugin in self._plugins:
            pname = plugin.meta.name
            try:
                self._register_translations(plugin)
            except Exception:
                logger.exception("Plugin %r translations failed", pname)

            try:
                self._register_tree_categories(plugin, window)
            except Exception:
                logger.exception("Plugin %r tree categories failed", pname)

            try:
                self._register_forms(plugin, window)
            except Exception:
                logger.exception("Plugin %r forms failed", pname)

            try:
                self._register_toolbar(plugin, window)
            except Exception:
                logger.exception("Plugin %r toolbar failed", pname)

            try:
                self._register_menu(plugin, window)
            except Exception:
                logger.exception("Plugin %r menu failed", pname)

            try:
                self._register_results(plugin, window)
            except Exception:
                logger.exception("Plugin %r results failed", pname)

            try:
                self._register_map_layers(plugin, window)
            except Exception:
                logger.exception("Plugin %r map layers failed", pname)

    def _register_translations(self, plugin: ESFEXPlugin) -> None:
        translations = plugin.get_translations()
        if not translations:
            return
        from esfex.visualization.i18n import _strings, _current_lang, _flatten

        lang_data = translations.get(_current_lang, {})
        if lang_data:
            _strings.update(_flatten(lang_data))

    def _register_tree_categories(self, plugin: ESFEXPlugin, window: Any) -> None:
        categories = plugin.get_tree_categories()
        tree_panel = getattr(window, "_tree", None)
        if tree_panel is None or not categories:
            return
        for cat in categories:
            if hasattr(tree_panel, "register_plugin_category"):
                tree_panel.register_plugin_category(
                    cat["key"], cat["label"], cat.get("element_type", cat["key"])
                )

    def _register_forms(self, plugin: ESFEXPlugin, window: Any) -> None:
        props = getattr(window, "_props", None)
        if props is None:
            return
        model = getattr(window, "_model", None)
        forms = plugin.get_forms(model)
        for element_type, widget in forms:
            if hasattr(props, "register_form"):
                props.register_form(element_type, widget)

    def _register_toolbar(self, plugin: ESFEXPlugin, window: Any) -> None:
        toolbar = getattr(window, "_toolbar", None)
        if toolbar is None:
            return
        plugin.get_toolbar_actions(toolbar, window)

    def _register_menu(self, plugin: ESFEXPlugin, window: Any) -> None:
        menu_bar = getattr(window, "menuBar", None)
        if menu_bar is None:
            return
        menu_bar_instance = menu_bar() if callable(menu_bar) else menu_bar
        plugin.get_menu_items(menu_bar_instance, window)

    def _register_results(self, plugin: ESFEXPlugin, window: Any) -> None:
        result_vars = plugin.get_result_variables()
        if not result_vars:
            return
        results_panel = getattr(window, "_results_panel", None)
        if results_panel is not None and hasattr(results_panel, "register_result_variable"):
            for var_tuple in result_vars:
                results_panel.register_result_variable(*var_tuple)

    def _register_map_layers(self, plugin: ESFEXPlugin, window: Any) -> None:
        map_widget = getattr(window, "_map", None)
        if map_widget is None:
            return
        plugin.get_map_layers(map_widget)

    # ── Teardown ──────────────────────────────────────────────────────

    def teardown_all(self) -> None:
        """Teardown all loaded plugins in reverse order."""
        for plugin in reversed(self._plugins):
            try:
                plugin.teardown()
            except Exception:
                logger.exception("Plugin %r teardown() failed", plugin.meta.name)
        self._plugins.clear()
        self._loaded = False

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def plugins(self) -> list[ESFEXPlugin]:
        """Return the list of loaded plugins (read-only)."""
        return list(self._plugins)

    @property
    def discovered(self) -> dict[str, Path]:
        """Return the discovered plugins mapping (name → path)."""
        return dict(self._discovered)

    @property
    def metas(self) -> dict[str, PluginMeta]:
        """Return the metadata for all discovered plugins."""
        return dict(self._metas)


# ── Singleton ─────────────────────────────────────────────────────────

_instance: Optional[PluginManager] = None


def get_plugin_manager() -> PluginManager:
    """Return the global PluginManager singleton."""
    global _instance
    if _instance is None:
        _instance = PluginManager()
    return _instance


def reset_plugin_manager() -> None:
    """Reset the singleton (for testing)."""
    global _instance
    if _instance is not None:
        _instance.teardown_all()
    _instance = None
