# Config Loader

Module: `esfex.config.loader`

## Functions

### load_config

```python
def load_config(path: Union[str, Path]) -> ESFEXConfig
```

Load and validate a complete ESFEX configuration file. Primary entry point for loading configuration.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `Union[str, Path]` | Path to the main YAML configuration file |

**Returns:** Validated `ESFEXConfig` instance with all systems, generators, batteries, and other equipment fully parsed and validated.

**Raises:** `ConfigLoadError` on validation failure, file not found, or YAML parsing errors.

**Processing Steps:**

1. Read YAML file via `load_yaml()`
2. Check if systems are embedded dictionaries or file path references
3. For file references, resolve paths relative to the config file location
4. Convert each system dictionary via `_convert_system()`:
   - Parse `unit_*` and `bat_*` keys (legacy format) or `generators`/`batteries` dicts
   - Convert fuels, nodes, penalties, CO2 budget, demand sectors
   - Convert EV categories, rooftop solar config, stochastic scenarios
   - Convert technologies and battery technologies for investment
   - Build DC power flow config from system-level parameters
5. Convert temporal, solver, N-1 security, master problem, and meta-network configs
6. Validate with Pydantic (`ESFEXConfig(**raw_config)`)

**Example:**

```python
from esfex.config.loader import load_config

# Load from single file with embedded systems
config = load_config("isla_juventud.yaml")
print(config.simulation_mode)         # "development"
print(config.systems.keys())          # dict_keys(['isla_juventud'])
print(config.primary_system.name)     # "isla_juventud"

# Access system equipment
sys = config.primary_system
print(f"Generators: {len(sys.generators)}")
print(f"Batteries: {len(sys.batteries)}")
print(f"Technologies: {len(sys.technologies)}")
print(f"Nodes: {sys.nodes.num_nodes}")
```

**Multi-file configuration:**

```yaml
# main.yaml
simulation_mode: development
temporal:
  resolution_hours: 1
systems:
  cuba: "systems/cuba.yaml"      # File reference
  jamaica: "systems/jamaica.yaml"
meta_network:
  systems: [cuba, jamaica]
```

```python
# File references are resolved relative to main.yaml
config = load_config("main.yaml")
cuba = config.get_system("cuba")
jamaica = config.get_system("jamaica")
```

### load_yaml

```python
def load_yaml(path: Union[str, Path]) -> dict[str, Any]
```

Low-level YAML file loader. Returns raw dictionary without Pydantic validation.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `Union[str, Path]` | Path to YAML file |

**Returns:** Raw dictionary with YAML contents. Returns empty dict `{}` for empty files.

**Raises:** `ConfigLoadError` if file not found, unreadable, or contains invalid YAML.

**Example:**

```python
from esfex.config.loader import load_yaml

raw = load_yaml("my_system.yaml")
print(raw.keys())
# Can inspect raw config before validation
print(raw.get("simulation_mode"))
```

### load_system_config

```python
def load_system_config(path: Union[str, Path]) -> SystemConfig
```

Load a single system configuration file without the full `ESFEXConfig` wrapper.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | `Union[str, Path]` | Path to system YAML file |

**Returns:** Validated `SystemConfig` instance.

**Raises:** `ConfigLoadError` on validation failure.

**Example:**

```python
from esfex.config.loader import load_system_config

sys = load_system_config("systems/isla_juventud.yaml")
print(f"System: {sys.name}")
print(f"Generators: {list(sys.generators.keys())}")
print(f"Target RE: {sys.target_re_penetration:.0%}")
```

---

## Internal Conversion Functions

Internal functions used by `load_config()` to convert raw YAML dictionaries into typed Pydantic models:

| Function | Description |
|----------|-------------|
| `_convert_system(data)` | Convert a full system dictionary to `SystemConfig` |
| `_convert_generator(key, data)` | Convert generator dict to `GeneratorConfig` |
| `_convert_battery(key, data)` | Convert battery/storage dict to `BatteryConfig` |
| `_convert_fuels(data)` | Convert fuel definitions to `dict[str, FuelConfig]` |
| `_convert_dc_power_flow(data)` | Extract DC power flow config from system data |
| `_convert_primary_energy_source(name, data)` | Convert primary energy source definition |

### YAML Key Conventions

The loader supports both legacy and modern YAML key formats:

| Legacy Format | Modern Format | Description |
|--------------|---------------|-------------|
| `unit_0`, `unit_1` | `generators: { unit_0: ... }` | Generator definitions |
| `bat_0`, `bat_1` | `batteries: { bat_0: ... }` | Battery definitions |
| `DC_BASE_IMPEDANCE` | `dc_power_flow.base_impedance` | DC power flow parameters |
| `LOSS_DEMAND_TRHESHOLD` | `loss_demand_threshold` | System parameters |
| `TARGET_RE_PENETRATION` | `target_re_penetration` | RE target |

The loader automatically handles case-insensitive penalty keys and sanitizes unknown generator types (e.g., `"Thermal"` becomes `"Non-renewable"`).

---

## Exceptions

### ConfigLoadError

```python
class ConfigLoadError(Exception):
    """Configuration loading or validation failed."""
```

Raised when:

- Configuration file not found at the specified path
- YAML syntax error (malformed YAML)
- Pydantic validation failure (type mismatch, missing required field, value out of range)
- Referenced system files not found (for multi-file configurations)

**Example:**

```python
from esfex.config.loader import load_config, ConfigLoadError

try:
    config = load_config("nonexistent.yaml")
except ConfigLoadError as e:
    print(f"Failed to load config: {e}")
    # "Configuration file not found: nonexistent.yaml"

try:
    config = load_config("invalid.yaml")
except ConfigLoadError as e:
    # Pydantic validation errors are included in the message
    print(f"Validation failed:\n{e}")
```

---

## Environment and Path Resolution

- All file paths in the configuration (demand files, availability profiles) are resolved relative to the YAML file location when loaded via `load_config()`
- The `config_path` parameter passed to `Orchestrator` is used for runtime path resolution
- System file references in multi-file configurations are resolved relative to the main config file
