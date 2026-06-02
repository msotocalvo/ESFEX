# Plugin Management

Extend the editor with plugins that add data sources, analysis tools, or visualization features. Plugins are plain directories with metadata and Python code -- no pip packaging or PyPI publication required.


---


## Installing Plugins

Two methods: GUI dialog or command line.

### Installing from the GUI

Open **Plugins > Manage Plugins...**. The dialog shows all discovered plugins with name, version, category, author, and enable/disable checkbox.

- **From ZIP** -- Click **Install from ZIP...**, select the archive file. The plugin is extracted into your user plugins directory and loaded immediately. If a plugin with the same directory name already exists, the dialog asks whether to overwrite it.
- **From Git** -- Click **Install from Git...**, paste the repository URL. The repo is cloned (shallow, depth 1) and the plugin loads immediately. Only `https://` and `git://` URLs are accepted for security reasons.

No restart required. The plugin manager hot-loads new plugins, registers GUI extensions (tree categories, forms, toolbar actions, menu items, result variables, map layers, translations), and makes them available immediately.

### Installing Manually

Place a plugin directory into any scan location (see [Where Plugins Live](#where-plugins-live)). Discovered on next application start or when opening the Manage Plugins dialog.

For example, to install a plugin called `weather_forecast` manually:

```bash
# Copy the plugin directory into the user plugins folder
cp -r weather_forecast/ ~/.esfex/plugins/weather_forecast

# Or clone directly
git clone --depth 1 https://github.com/user/esfex-weather ~/.esfex/plugins/esfex-weather
```

After placing files manually, restart the editor or open **Plugins > Manage Plugins...** to trigger discovery.


---


## Plugin Manager Dialog

Modal window (**Plugins > Manage Plugins...**) with three sections:

### Plugin Table

One row per discovered plugin:

| Column | Description |
|--------|-------------|
| **Enabled** | Checkbox to toggle the plugin on or off |
| **Name** | Unique plugin identifier (slug) |
| **Version** | Semantic version string from `plugin.json` |
| **Category** | Plugin category: `data`, `analysis`, `visualization`, `model`, or `general` |
| **Author** | Plugin author name |
| **Description** | Short description of what the plugin does |

If no plugins are discovered, the table shows "No plugins found."

### Action Buttons

| Button | Action |
|--------|--------|
| **Install from ZIP...** | Opens a file chooser filtered to `.zip` files. Extracts and validates the plugin. |
| **Install from Git...** | Opens a text input dialog for the Git repository URL. Clones and validates the plugin. |
| **Uninstall** | Removes the selected plugin directory after confirmation. Only enabled when a row is selected. |
| **Open Folder** | Opens the user plugins directory (`~/.esfex/plugins/`) in your system file manager. |

### OK / Cancel

- **OK** -- Applies all enable/disable changes. Newly enabled plugins are hot-loaded. Newly disabled plugins require a restart (a notification dialog appears).
- **Cancel** -- Discards all checkbox changes without modifying anything.

---


## Enabling and Disabling

Toggle the checkbox next to any plugin, then click **OK**.

- **Enabling**: Hot-loaded immediately. GUI extensions (menu items, toolbar buttons, tree categories, forms, map layers) appear without restart.
- **Disabling**: Marked as disabled in the state file. Because loaded Python modules cannot be fully unloaded at runtime, a restart is required. The dialog displays a notification.

State persisted in `~/.esfex/plugins.json`:

```json
{
  "disabled": ["weather_forecast", "example_plugin"]
}
```

Plugins not listed in `disabled` are enabled by default. Deleting this file re-enables all plugins.


---


## Uninstalling

Select a plugin and click **Uninstall**. Upon confirmation:

1. The plugin directory is permanently deleted from `~/.esfex/plugins/`.
2. The plugin is removed from the disabled list (if it was disabled).
3. The table refreshes to reflect the removal.

Plugin data in `~/.esfex/plugin_data/{name}/` is not removed. Delete manually to remove all traces.


---


## Where Plugins Live

Three scan locations in priority order:

| Priority | Location | Purpose |
|----------|----------|---------|
| 1 | `~/.esfex/plugins/` | Per-user plugins. This is where GUI-installed plugins are placed. |
| 2 | `.esfex/plugins/` | Project-local plugins in the current working directory. Useful for plugins that are specific to a single project and should be versioned alongside the project files. |
| 3 | `$ESFEX_PLUGIN_PATH` | Colon-separated (`:` on Linux/macOS, `;` on Windows) list of additional directories to scan. Useful for shared network drives or CI environments. |

If two plugins share the same name, higher-priority location takes precedence. Duplicates are logged.


---


## Plugin Directory Structure

Minimal plugin -- two files:

```
my_plugin/
    plugin.json      # Metadata (required)
    __init__.py       # Entry point with create_plugin() factory (required)
```

Complete plugin example:

```
weather_forecast/
    plugin.json
    __init__.py
    fetcher.py            # Additional Python modules
    analysis.py
    julia/
        weather_model.jl  # Julia runtime overlay modules
    i18n/
        en.json           # Translation strings
        es.json
    icons/
        toolbar.png
    data/
        default_config.json
```

### plugin.json

Metadata file. Only `name` and `version` are required.

```json
{
    "name": "weather_forecast",
    "version": "1.0.0",
    "description": "Fetches weather forecasts and integrates them as availability profiles",
    "author": "Jane Doe",
    "url": "https://github.com/user/esfex-weather",
    "category": "data",
    "priority": 0,
    "requires_plugins": [],
    "python_dependencies": ["requests>=2.28", "xarray"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier. Must match `^[A-Za-z0-9][A-Za-z0-9_-]*$` (alphanumeric, underscores, hyphens). |
| `version` | string | Yes | Semantic version string (e.g., `"1.0.0"`). |
| `description` | string | No | Short human-readable description. |
| `author` | string | No | Author name or organization. |
| `url` | string | No | Homepage or repository URL. |
| `category` | string | No | One of: `data`, `analysis`, `visualization`, `model`, `general`. Default: `general`. |
| `priority` | integer | No | Load order. Lower values load first. Default: `0`. |
| `requires_plugins` | array | No | List of plugin names that must be loaded before this one. Missing dependencies produce a warning but do not block loading. |
| `python_dependencies` | array | No | Pip requirement strings for informational purposes. The plugin manager does not install these automatically -- they serve as documentation for users. |

### __init__.py

Must define a `create_plugin(context)` factory function returning a `ESFEXPlugin` instance:

```python
from esfex.plugins.protocol import PluginContext, PluginMeta, ESFEXPlugin


class WeatherForecastPlugin(ESFEXPlugin):
    meta = PluginMeta(name="weather_forecast", version="1.0.0")

    def setup(self):
        """One-time initialization after the plugin is loaded."""
        self.api_key = self._load_api_key()

    def teardown(self):
        """Cleanup when the application shuts down."""
        pass

    def get_menu_items(self, menu_bar, main_window):
        """Add a Fetch Weather menu item under the Plugins menu."""
        plugins_menu = menu_bar.findChild(type(menu_bar), "plugins_menu")
        # ... add actions ...


def create_plugin(context: PluginContext) -> WeatherForecastPlugin:
    return WeatherForecastPlugin(context)
```

---


## Plugin Configuration

Plugins define a Pydantic schema validated against the `plugins.{name}` YAML section. Override `get_config_schema()`:

```python
from pydantic import BaseModel

class WeatherConfig(BaseModel):
    api_key: str = ""
    cache_hours: int = 24
    source: str = "era5"

class WeatherForecastPlugin(ESFEXPlugin):
    def get_config_schema(self):
        return WeatherConfig

    def on_config_loaded(self, config):
        plugin_cfg = config.plugins.get("weather_forecast", {})
        self.settings = WeatherConfig(**plugin_cfg)
```

In the YAML configuration file:

```yaml
plugins:
  weather_forecast:
    api_key: "your-api-key-here"
    cache_hours: 48
    source: "era5"
```

Persistent data directory at `~/.esfex/plugin_data/{name}/` (available as `self.context.data_dir`), auto-created and surviving plugin updates. Use for caches, downloaded data, or preferences.


---


## Plugin Lifecycle and Hooks

Override only needed hooks -- all have no-op defaults.

### Lifecycle Hooks

| Hook | When Called | Purpose |
|------|------------|---------|
| `setup()` | After instantiation | One-time initialization |
| `teardown()` | Application shutdown | Release resources, close connections |

### Simulation Hooks

| Hook | When Called | Parameters |
|------|------------|------------|
| `pre_simulation()` | Before simulation starts | `config`, `output_dir` |
| `post_demand_loaded()` | After demand data is loaded | `base_demand`, `ev_demand`, `total_demand`, `config`. Return modified `total_demand` or `None`. |
| `pre_master_problem()` | Before strategic planning | `config`, `years` |
| `post_master_problem()` | After investment decisions | `investments`, `retirements`, `config` |
| `pre_year()` | Before each year's dispatch | `year`, `year_idx`, `units_config`, `config` |
| `post_year()` | After each year's results | `year`, `result`, `hdf5_file`, `output_dir`, `config`. The HDF5 file is open in append mode; write to `plugins/{name}/`. |
| `post_simulation()` | After all years complete | `results`, `hdf5_path`, `output_dir`, `config` |

### GUI Extension Hooks

| Hook | Returns | Purpose |
|------|---------|---------|
| `get_tree_categories()` | `list[dict]` | Add categories to the element tree panel |
| `get_forms(model)` | `list[tuple[str, QWidget]]` | Register property forms for custom element types |
| `get_toolbar_actions(toolbar, window)` | `list[QAction]` | Add buttons to the main toolbar |
| `get_menu_items(menu_bar, window)` | `None` | Add items to the menu bar |
| `get_result_variables()` | `list[tuple[str, str, str, str]]` | Register result variables: `(display_name, hdf5_key, aggregation, viz_type)` |
| `get_map_layers(map_widget)` | `None` | Add custom overlay layers to the Leaflet map |
| `get_translations()` | `dict[str, dict]` | Provide `{lang: {key: value}}` translation strings |
| `get_julia_modules()` | `list[Path]` | Return `.jl` files to `include()` at runtime as overlays |
| `get_cli_commands()` | `list[typer.Typer]` | Register CLI sub-commands under `esfex {name} ...` |
| `get_config_schema()` | `type[BaseModel]` | Pydantic model for plugin configuration validation |

## Plugin Menu Items

Active plugins register menu items under the **Plugins** menu (below "Manage Plugins...") via the `get_menu_items()` hook.


---


## CLI Alternative

```bash
esfex plugin list
esfex plugin install --git https://github.com/user/esfex-weather
esfex plugin install --zip weather_plugin.zip
esfex plugin enable weather_forecast
esfex plugin disable weather_forecast
esfex plugin uninstall weather_forecast
```

Mirrors GUI dialog functionality. Useful for headless servers, CI pipelines, or scripted deployments.


---


## Troubleshooting Plugin Issues

### Plugin Not Discovered

- Verify the directory contains both `plugin.json` and `__init__.py`.
- Check that `name` in `plugin.json` uses only alphanumeric characters, underscores, and hyphens.
- Confirm the plugin is in one of the three scan locations. Run `esfex plugin list` to verify.
- Check the log for warnings (`"missing plugin.json or __init__.py"`, `"unsafe characters"`).

### Plugin Fails to Load

- Check the Python console or log for exception tracebacks (each load is wrapped in try/except).
- Verify all `python_dependencies` are installed (not auto-installed by the plugin manager).
- Ensure `create_plugin(context)` exists in `__init__.py` and returns a `ESFEXPlugin` instance.

### Plugin Loaded but GUI Extensions Missing

- Confirm the plugin is enabled (checkbox checked, or not in `disabled` list).
- Check that `gui_mode` is `True` in the plugin context (set automatically in GUI mode).
- Verify hook methods return correct types (e.g., `get_forms()` must return `list[(str, QWidget)]`).

### Disabling a Plugin Does Not Take Effect

Requires restarting the editor. Python modules already imported cannot be fully unloaded at runtime.

### Overwrite Conflicts During Installation

If a plugin already exists, the dialog asks whether to overwrite. **Yes** replaces the old version; **No** cancels. From CLI, uninstall first, then reinstall.

### Corrupted State File

If `~/.esfex/plugins.json` becomes corrupted, the plugin manager logs a warning and treats all plugins as enabled. Delete to reset:

```bash
rm ~/.esfex/plugins.json
```


---


## Security Considerations

- **ZIP Slip protection**: Before extracting any ZIP archive, every file path inside the archive is validated against the target directory. Paths that attempt to escape the target (e.g., using `../`) are rejected with a `ValueError` (CWE-22 mitigation).
- **Name sanitization**: Plugin directory names must match `^[A-Za-z0-9][A-Za-z0-9_-]*$`. Names containing path separators, dots, or other special characters are rejected.
- **Git hook prevention**: When cloning a plugin from Git, hooks are disabled by pointing `core.hooksPath` to an empty temporary directory. This prevents malicious pre-checkout or post-checkout hooks from executing arbitrary code.
- **URL scheme restriction**: Only `https://` and `git://` Git URLs are accepted. Local file paths, `ssh://`, and other schemes are blocked to prevent unintended local file access.
- **Overwrite confirmation**: Installing a plugin over an existing one requires explicit confirmation (a dialog in the GUI, or the `force` parameter in the API).
- **Audit logging**: Every plugin installation and load operation logs a SHA-256 hash of the plugin directory contents. This provides an audit trail for verifying plugin integrity.
- **Isolated execution**: Each plugin hook invocation is wrapped in a try/except block. A broken or malicious plugin that raises an exception will log an error but never crash the core application or affect other plugins.

Plugins execute arbitrary Python code within the same process. Only install plugins from trusted sources. Review source code before installation.


---


## Creating Plugins

A plugin requires two files:

- `plugin.json` — metadata (name, version, author, category, description)
- `__init__.py` — entry point with a `create_plugin(context)` factory function

See [Contributing > Development Setup](../contributing/development-setup.md) for details.
