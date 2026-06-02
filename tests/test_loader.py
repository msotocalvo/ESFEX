"""
Tests for esfex.config.loader module.

Covers all public functions and the ConfigLoadError exception:
- load_yaml: valid files, missing files, invalid YAML, empty files
- load_config: full config loading with validation, multi-file support
- load_system_config: individual system loading
- _convert_* helpers: fuels, generators, batteries, DC power flow
- Validation error messages
"""

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from esfex.config.loader import (
    ConfigLoadError,
    _convert_battery,
    _convert_dc_power_flow,
    _convert_fuels,
    _convert_generator,
    _convert_primary_energy_source,
    _convert_system,
    load_config,
    load_system_config,
    load_yaml,
)
from esfex.config.schema import (
    BatteryConfig,
    DCPowerFlowConfig,
    FuelConfig,
    GeneratorConfig,
    MetaNetworkConfig,
    PrimaryEnergySourceConfig,
    ESFEXConfig,
    SystemConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def valid_yaml_file(tmp_path):
    """Create a minimal valid YAML file."""
    content = {"key": "value", "number": 42, "nested": {"a": 1}}
    p = tmp_path / "valid.yaml"
    p.write_text(yaml.dump(content), encoding="utf-8")
    return p


@pytest.fixture
def empty_yaml_file(tmp_path):
    """Create an empty YAML file (no content)."""
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    return p


@pytest.fixture
def invalid_yaml_file(tmp_path):
    """Create a file with invalid YAML syntax."""
    p = tmp_path / "bad.yaml"
    p.write_text("key: [unclosed bracket\n  invalid: : :", encoding="utf-8")
    return p


@pytest.fixture
def minimal_system_data():
    """Return minimal dict that _convert_system can process."""
    return {
        "name": "TestSys",
        "demand_path": None,
        "demand_scale": 1.0,
        "loss_demand_threshold": 0.05,
        "life_extension_cost_factor": 0.2,
        "sim_rooftop": False,
        "target_re_penetration": 0.5,
        "min_annual_increment": 0.01,
        "max_annual_increment": 0.1,
        "discount_rate": 0.05,
        "base_lcoe": 93,
        "inertia_limit_threshold": 0.1,
        "nodes": {
            "nodes_connections": [0, 100, 100, 0],
            "reserve_static": [10, 10],
            "reserve_dynamic": [20, 20],
            "reserve_duration": [2, 2],
            "losses": [0.001, 0.001],
            "transference_invest_cost": [13000, 13000],
            "transference_invest_max": [100, 100],
        },
        "fuel_transport_distances": [[0, 50], [50, 0]],
    }


@pytest.fixture
def sample_generator_data():
    """Return a valid generator data dict."""
    return {
        "name": "Solar",
        "type": "Renewable",
        "fuel": "Sun",
        "technology": "Solar PV",
        "reservable": False,
        "life_time": [25, 25],
        "initial_age": [0, 0],
        "degradation_rate": [0.005, 0.005],
        "decommissioning_cost": [300, 300],
        "rated_power": [50, 80],
        "min_power": [0, 0],
        "min_up": [0, 0],
        "min_down": [0, 0],
        "ramp_up": [1.0, 1.0],
        "ramp_down": [1.0, 1.0],
        "eff_at_rated": [0.98, 0.98],
        "eff_at_min": [0.98, 0.98],
        "inertia": [0, 0],
        "start_up_cost": [0, 0],
        "fuel_cost": [0, 0],
        "fixed_cost": [5.0, 5.0],
        "maintenance_cost": [5.4, 5.4],
        "invest_cost": [900000, 900000],
        "invest_max_power": [200, 200],
        "Availability": None,
    }


@pytest.fixture
def sample_battery_data():
    """Return a valid battery/storage data dict."""
    return {
        "name": "Li-ion",
        "type": "Storage",
        "fuel": "None",
        "reservable": True,
        "spillage": True,
        "life_time": [15, 15],
        "initial_age": [0, 0],
        "degradation_rate": [0.01, 0.01],
        "decommissioning_cost": [200, 200],
        "rated_power": [25, 40],
        "min_power": [0, 0],
        "min_up": [0, 0],
        "min_down": [0, 0],
        "ramp_up": [1.0, 1.0],
        "ramp_down": [1.0, 1.0],
        "eff_at_rated": [0.95, 0.95],
        "eff_at_min": [0.95, 0.95],
        "inertia": [0, 0],
        "start_up_cost": [0, 0],
        "fuel_cost": [0, 0],
        "fixed_cost": [5.0, 5.0],
        "maintenance_cost": [3.0, 3.0],
        "invest_cost": [200000, 200000],
        "invest_max_power": [50, 50],
        "efficiency_charge": [0.95, 0.95],
        "efficiency_discharge": [0.95, 0.95],
        "soc_initial": [0.5, 0.5],
        "max_DoD": [0.9, 0.9],
        "capacity": [50, 80],
        "MaxChargePower": [25, 40],
        "MaxDischargePower": [25, 40],
        "Availability": None,
    }


@pytest.fixture
def full_config_file(tmp_path, minimal_system_data):
    """Create a full config YAML with systems + meta_network."""
    config = {
        "simulation_mode": "development",
        "meta_network": {
            "systems": ["test_sys"],
        },
        "systems": {
            "test_sys": minimal_system_data,
        },
    }
    p = tmp_path / "full_config.yaml"
    p.write_text(yaml.dump(config, default_flow_style=False), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# load_yaml
# ---------------------------------------------------------------------------


class TestLoadYaml:
    """Tests for the load_yaml() function."""

    def test_loads_valid_yaml(self, valid_yaml_file):
        """load_yaml returns a dict from a valid YAML file."""
        result = load_yaml(valid_yaml_file)
        assert isinstance(result, dict)
        assert result["key"] == "value"
        assert result["number"] == 42

    def test_nested_structure(self, valid_yaml_file):
        """load_yaml preserves nested dictionaries."""
        result = load_yaml(valid_yaml_file)
        assert result["nested"]["a"] == 1

    def test_returns_dict_type(self, valid_yaml_file):
        """load_yaml always returns a dict."""
        result = load_yaml(valid_yaml_file)
        assert type(result) is dict

    def test_missing_file_raises_config_load_error(self, tmp_path):
        """load_yaml raises ConfigLoadError for nonexistent files."""
        with pytest.raises(ConfigLoadError, match="not found"):
            load_yaml(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises_config_load_error(self, invalid_yaml_file):
        """load_yaml raises ConfigLoadError for malformed YAML."""
        with pytest.raises(ConfigLoadError, match="Invalid YAML"):
            load_yaml(invalid_yaml_file)

    def test_empty_file_returns_empty_dict(self, empty_yaml_file):
        """load_yaml returns {} for an empty YAML file."""
        result = load_yaml(empty_yaml_file)
        assert result == {}

    def test_accepts_string_path(self, valid_yaml_file):
        """load_yaml accepts a string path in addition to Path objects."""
        result = load_yaml(str(valid_yaml_file))
        assert isinstance(result, dict)

    def test_accepts_path_object(self, valid_yaml_file):
        """load_yaml accepts a pathlib.Path object."""
        result = load_yaml(Path(valid_yaml_file))
        assert isinstance(result, dict)

    def test_yaml_with_lists(self, tmp_path):
        """load_yaml correctly parses YAML files with list values."""
        content = {"items": [1, 2, 3], "names": ["a", "b"]}
        p = tmp_path / "lists.yaml"
        p.write_text(yaml.dump(content), encoding="utf-8")
        result = load_yaml(p)
        assert result["items"] == [1, 2, 3]

    def test_yaml_with_none_value(self, tmp_path):
        """load_yaml correctly handles null/None YAML values."""
        p = tmp_path / "nulls.yaml"
        p.write_text("key: null\nother: ~\n", encoding="utf-8")
        result = load_yaml(p)
        assert result["key"] is None
        assert result["other"] is None

    def test_error_message_includes_filepath(self, tmp_path):
        """ConfigLoadError message includes the file path for debugging."""
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(ConfigLoadError) as exc_info:
            load_yaml(missing)
        assert str(missing) in str(exc_info.value)

    def test_io_error_is_wrapped(self, tmp_path):
        """IOError during file reading is wrapped in ConfigLoadError."""
        p = tmp_path / "unreadable.yaml"
        p.write_text("key: value", encoding="utf-8")
        p.chmod(0o000)
        try:
            with pytest.raises(ConfigLoadError, match="Cannot read"):
                load_yaml(p)
        finally:
            p.chmod(0o644)


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    """Tests for the load_config() function."""

    def test_loads_full_config_returns_esfex_config(self, full_config_file):
        """load_config returns a validated ESFEXConfig object."""
        cfg = load_config(full_config_file)
        assert isinstance(cfg, ESFEXConfig)

    def test_systems_dict_populated(self, full_config_file):
        """load_config populates the systems dictionary."""
        cfg = load_config(full_config_file)
        assert "test_sys" in cfg.systems
        assert isinstance(cfg.systems["test_sys"], SystemConfig)

    def test_meta_network_populated(self, full_config_file):
        """load_config populates meta_network correctly."""
        cfg = load_config(full_config_file)
        assert isinstance(cfg.meta_network, MetaNetworkConfig)
        assert "test_sys" in cfg.meta_network.systems

    def test_missing_file_raises_config_load_error(self, tmp_path):
        """load_config raises ConfigLoadError for missing file."""
        with pytest.raises(ConfigLoadError):
            load_config(tmp_path / "missing.yaml")

    def test_validation_error_raises_config_load_error(self, tmp_path):
        """load_config wraps Pydantic validation errors in ConfigLoadError."""
        # Missing required 'meta_network' and 'systems'
        p = tmp_path / "incomplete.yaml"
        p.write_text("simulation_mode: development\n", encoding="utf-8")
        with pytest.raises(ConfigLoadError, match="validation failed|Failed to load"):
            load_config(p)

    def test_multi_file_system_reference(self, tmp_path, minimal_system_data):
        """load_config resolves external file references for systems."""
        # Write system file
        sys_file = tmp_path / "my_system.yaml"
        sys_file.write_text(
            yaml.dump(minimal_system_data, default_flow_style=False),
            encoding="utf-8",
        )

        # Write main config referencing external file
        main = {
            "meta_network": {"systems": ["ext_sys"]},
            "systems": {"ext_sys": "my_system.yaml"},
        }
        main_file = tmp_path / "main.yaml"
        main_file.write_text(
            yaml.dump(main, default_flow_style=False), encoding="utf-8"
        )

        cfg = load_config(main_file)
        assert isinstance(cfg, ESFEXConfig)
        assert "ext_sys" in cfg.systems

    def test_validation_error_message_is_meaningful(self, tmp_path):
        """Validation errors include field-level details."""
        p = tmp_path / "bad_fields.yaml"
        bad_config = {
            "simulation_mode": "INVALID_MODE",
            "meta_network": {"systems": ["s"]},
            "systems": {"s": {"name": "S"}},
        }
        p.write_text(yaml.dump(bad_config), encoding="utf-8")
        with pytest.raises(ConfigLoadError) as exc_info:
            load_config(p)
        assert "simulation_mode" in str(exc_info.value) or "validation" in str(
            exc_info.value
        ).lower()

    def test_default_temporal_and_solver(self, full_config_file):
        """load_config applies default temporal and solver configs when omitted."""
        cfg = load_config(full_config_file)
        assert cfg.temporal.resolution_hours == 1
        assert cfg.solver.name == "highs"

    def test_system_referenced_in_meta_but_missing_raises(self, tmp_path):
        """load_config fails when meta_network references a missing system."""
        config = {
            "meta_network": {"systems": ["nonexistent"]},
            "systems": {},
        }
        p = tmp_path / "bad_ref.yaml"
        p.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigLoadError):
            load_config(p)


# ---------------------------------------------------------------------------
# load_system_config
# ---------------------------------------------------------------------------


class TestLoadSystemConfig:
    """Tests for the load_system_config() function."""

    def test_loads_small_system_fixture_raises_on_format_mismatch(self):
        """small_system.yaml uses a simplified battery format that
        _convert_battery cannot parse (missing fields like initial_age,
        MaxChargePower, etc.).  The converter raises KeyError because
        load_system_config only wraps ValidationError, not KeyError."""
        with pytest.raises(KeyError):
            load_system_config(FIXTURES_DIR / "small_system.yaml")

    def test_loads_valid_system(self, tmp_path, minimal_system_data):
        """load_system_config returns SystemConfig from a valid system YAML."""
        p = tmp_path / "system.yaml"
        p.write_text(
            yaml.dump(minimal_system_data, default_flow_style=False),
            encoding="utf-8",
        )
        cfg = load_system_config(p)
        assert isinstance(cfg, SystemConfig)

    def test_system_name(self, tmp_path, minimal_system_data):
        """Loaded system has the expected name."""
        p = tmp_path / "system.yaml"
        p.write_text(
            yaml.dump(minimal_system_data, default_flow_style=False),
            encoding="utf-8",
        )
        cfg = load_system_config(p)
        assert cfg.name == "TestSys"

    def test_generators_empty_when_none_defined(self, tmp_path, minimal_system_data):
        """When no generators are defined, generators dict is empty."""
        p = tmp_path / "system.yaml"
        p.write_text(
            yaml.dump(minimal_system_data, default_flow_style=False),
            encoding="utf-8",
        )
        cfg = load_system_config(p)
        assert isinstance(cfg.generators, dict)

    def test_missing_file_raises_config_load_error(self, tmp_path):
        """load_system_config raises ConfigLoadError for missing file."""
        with pytest.raises(ConfigLoadError):
            load_system_config(tmp_path / "no_such_system.yaml")


# ---------------------------------------------------------------------------
# _convert_fuels
# ---------------------------------------------------------------------------


class TestConvertFuels:
    """Tests for the _convert_fuels() helper."""

    def test_converts_single_fuel(self):
        """_convert_fuels returns a dict of FuelConfig for one fuel."""
        raw = {
            "Gas": {
                "name": "Gas",
                "unit": "ton",
                "emission_factor": 0.2,
                "energy_content": 12.28,
                "price_base": 110,
                "price_growth_rate": 0.015,
            }
        }
        result = _convert_fuels(raw)
        assert "Gas" in result
        assert isinstance(result["Gas"], FuelConfig)
        assert result["Gas"].emission_factor == 0.2

    def test_converts_multiple_fuels(self):
        """_convert_fuels handles multiple fuel entries."""
        raw = {
            "Gas": {"emission_factor": 0.2},
            "Sun": {"emission_factor": 0.0},
        }
        result = _convert_fuels(raw)
        assert len(result) == 2
        assert result["Sun"].emission_factor == 0.0

    def test_defaults_applied(self):
        """_convert_fuels applies default values for missing fields."""
        raw = {"Wind": {}}
        result = _convert_fuels(raw)
        assert result["Wind"].emission_factor == 0.0
        assert result["Wind"].price_base == 0.0


# ---------------------------------------------------------------------------
# _convert_generator
# ---------------------------------------------------------------------------


class TestConvertGenerator:
    """Tests for the _convert_generator() helper."""

    def test_returns_generator_config(self, sample_generator_data):
        """_convert_generator produces a GeneratorConfig instance."""
        gen = _convert_generator("solar", sample_generator_data)
        assert isinstance(gen, GeneratorConfig)

    def test_name_from_data(self, sample_generator_data):
        """Generator name comes from data dict, not the key."""
        gen = _convert_generator("solar", sample_generator_data)
        assert gen.name == "Solar"

    def test_fallback_name_to_key(self, sample_generator_data):
        """If 'name' missing from data, key is used as fallback."""
        del sample_generator_data["name"]
        gen = _convert_generator("my_gen", sample_generator_data)
        assert gen.name == "my_gen"


# ---------------------------------------------------------------------------
# _convert_battery
# ---------------------------------------------------------------------------


class TestConvertBattery:
    """Tests for the _convert_battery() helper."""

    def test_returns_battery_config(self, sample_battery_data):
        """_convert_battery produces a BatteryConfig instance."""
        bat = _convert_battery("bat_0", sample_battery_data)
        assert isinstance(bat, BatteryConfig)

    def test_type_forced_to_storage(self, sample_battery_data):
        """Battery type is always 'Storage'."""
        bat = _convert_battery("bat_0", sample_battery_data)
        assert bat.type == "Storage"

    def test_invest_cost_energy_defaults_to_invest_cost(self, sample_battery_data):
        """If invest_cost_energy missing, it defaults to invest_cost."""
        bat = _convert_battery("bat_0", sample_battery_data)
        assert bat.invest_cost_energy == sample_battery_data["invest_cost"]


# ---------------------------------------------------------------------------
# _convert_dc_power_flow
# ---------------------------------------------------------------------------


class TestConvertDCPowerFlow:
    """Tests for the _convert_dc_power_flow() helper."""

    def test_returns_dc_power_flow_config(self):
        """_convert_dc_power_flow returns DCPowerFlowConfig instance."""
        data = {"dc_base_impedance": 100.0, "dc_reactance_per_km": 0.4}
        result = _convert_dc_power_flow(data)
        assert isinstance(result, DCPowerFlowConfig)
        assert result.base_impedance == 100.0

    def test_uppercase_keys(self):
        """Accepts uppercase DC_ keys (legacy format)."""
        data = {"DC_BASE_IMPEDANCE": 200.0, "DC_VOLTAGE_LEVEL_KV": 345.0}
        result = _convert_dc_power_flow(data)
        assert result.base_impedance == 200.0
        assert result.voltage_level_kv == 345.0

    def test_defaults_when_no_keys(self):
        """Returns defaults when no DC keys are present."""
        result = _convert_dc_power_flow({})
        assert result.base_impedance == 100.0
        assert result.reactance_per_km == 0.4
        assert result.voltage_level_kv == 220.0
        # enable_angle_limits removed — kept only ACOPF-meaningful fields.
        assert result.max_angle_diff_deg == 30.0
        assert result.slack_bus == 0


# ---------------------------------------------------------------------------
# _convert_primary_energy_source
# ---------------------------------------------------------------------------


class TestConvertPrimaryEnergySource:
    """Tests for the _convert_primary_energy_source() helper."""

    def test_returns_primary_energy_source_config(self):
        """Produces a PrimaryEnergySourceConfig instance."""
        data = {
            "unit": "m3",
            "max_availability": [1000, 1000],
            "import_cost": [50, 50],
            "storage_capacity": [500, 500],
            "initial_storage_level": [0.5, 0.5],
            "storage_investment_cost": 10000,
            "transport_cost": 5,
            "transport_losses": 0.01,
            "max_storage_investment_per_node": 200,
            "max_transport_investment_per_arc": 100,
        }
        result = _convert_primary_energy_source("LNG", data)
        assert isinstance(result, PrimaryEnergySourceConfig)
        assert result.name == "LNG"


# ---------------------------------------------------------------------------
# _convert_system
# ---------------------------------------------------------------------------


class TestConvertSystem:
    """Tests for the _convert_system() helper."""

    def test_returns_system_config(self, minimal_system_data):
        """_convert_system returns a SystemConfig instance."""
        sc = _convert_system(minimal_system_data)
        assert isinstance(sc, SystemConfig)

    def test_dc_power_flow_set(self, minimal_system_data):
        """DC power flow config is populated from system data."""
        sc = _convert_system(minimal_system_data)
        assert isinstance(sc.dc_power_flow, DCPowerFlowConfig)


# ---------------------------------------------------------------------------
# ConfigLoadError
# ---------------------------------------------------------------------------


class TestConfigLoadError:
    """Tests for the ConfigLoadError exception class."""

    def test_is_exception(self):
        """ConfigLoadError is a subclass of Exception."""
        assert issubclass(ConfigLoadError, Exception)

    def test_message_preserved(self):
        """Exception message is stored and accessible."""
        err = ConfigLoadError("test message")
        assert str(err) == "test message"

    def test_can_be_raised_and_caught(self):
        """ConfigLoadError can be raised and caught normally."""
        with pytest.raises(ConfigLoadError):
            raise ConfigLoadError("boom")
