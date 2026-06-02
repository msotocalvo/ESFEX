"""
Tests for esfex.cli module.

Uses typer.testing.CliRunner to invoke CLI commands, with mocks
to avoid actually running Julia or loading real config files.

Note: The CLI commands import ``load_config`` and ``Orchestrator`` locally
inside the command functions, so we must patch them at their origin modules:
  - ``esfex.config.loader.load_config``
  - ``esfex.runner.Orchestrator``
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import h5py
import numpy as np
import pytest
import yaml
from typer.testing import CliRunner

from esfex.cli import app

runner = CliRunner()

# Path strings for patching (imports happen inside command functions)
_PATCH_LOAD_CONFIG = "esfex.config.loader.load_config"
_PATCH_ORCHESTRATOR = "esfex.runner.Orchestrator"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def minimal_config_path(tmp_path):
    """Create a minimal YAML config file that can pass typer path validation.

    The actual config loading is mocked in most tests, so this file just
    needs to exist and be readable.
    """
    config = {
        "simulation_mode": "development",
        "date_start": "01/01/2025 00:00",
        "temporal": {
            "resolution_hours": 1,
            "rolling_horizon_hours": 48,
            "overlap_hours": 6,
            "use_rolling_horizon": True,
        },
        "solver": {"name": "highs", "threads": 1, "verbose": False},
        "n1_security": {"enabled": False},
        "master_problem": {"stochastic": False, "representative_days": 5},
        "enable_primary_energy": False,
        "meta_network": {"systems": ["test_system"]},
        "systems": {
            "test_system": {
                "name": "test_system",
                "demand_path": None,
                "demand_scale": 1.0,
                "nodes": {
                    "nodes_connections": [0, 100, 100, 0],
                    "reserve_static": [10, 10],
                    "reserve_dynamic": [20, 20],
                    "reserve_duration": [2, 2],
                    "losses": [0.001, 0.001],
                    "transference_invest_cost": [13000, 13000],
                    "transference_invest_max": [100, 100],
                },
                "generators": {
                    "gen_0": {
                        "name": "TestGen",
                        "type": "Non-renewable",
                        "fuel": "Gas",
                        "life_time": [25, 25],
                        "initial_age": [5, 5],
                        "degradation_rate": [0.01, 0.01],
                        "decommissioning_cost": [1000, 1000],
                        "rated_power": [100, 50],
                        "min_power": [0.3, 0.3],
                        "min_up": [4, 4],
                        "min_down": [2, 2],
                        "ramp_up": [0.04, 0.04],
                        "ramp_down": [0.04, 0.04],
                        "eff_at_rated": [0.45, 0.45],
                        "eff_at_min": [0.40, 0.40],
                        "inertia": [6.0, 6.0],
                        "start_up_cost": [500, 500],
                        "fuel_cost": [94, 94],
                        "fixed_cost": [6.6, 6.6],
                        "maintenance_cost": [28.8, 28.8],
                        "invest_cost": [3900000, 3900000],
                        "invest_max_power": [0, 0],
                    },
                },
                "batteries": {},
            },
        },
    }
    config_path = tmp_path / "test_config.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False))
    return config_path


@pytest.fixture
def mock_esfex_config():
    """Create a mock ESFEXConfig object."""
    cfg = MagicMock()
    cfg.simulation_mode = "development"
    cfg.solver = MagicMock()
    cfg.solver.name = "highs"
    cfg.solver.verbose = False
    cfg.meta_network = MagicMock()
    cfg.meta_network.systems = ["test_system"]
    cfg.temporal = MagicMock()
    cfg.temporal.use_rolling_horizon = True
    cfg.n1_security = MagicMock()
    cfg.n1_security.enabled = False
    cfg.enable_primary_energy = False

    sys_cfg = MagicMock()
    sys_cfg.num_nodes = 2
    sys_cfg.generators = {"gen_0": MagicMock()}
    sys_cfg.batteries = {}
    cfg.systems = {"test_system": sys_cfg}
    return cfg


@pytest.fixture
def hdf5_results_path(tmp_path):
    """Create a minimal HDF5 results file for export tests."""
    h5_path = tmp_path / "results.h5"
    with h5py.File(h5_path, "w") as f:
        f.attrs["creation_date"] = "2026-02-21T00:00:00"
        f.attrs["hours"] = 24
        f.attrs["num_nodes"] = 2
        grp = f.create_group("summary_results")
        grp.create_dataset("total_cost", data=np.array([1e6, 2e6]))
    return h5_path


# ---------------------------------------------------------------------------
# Tests: --help
# ---------------------------------------------------------------------------

class TestHelp:
    def test_main_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "esfex" in result.output.lower()

    def test_run_help(self):
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output
        assert "--dry-run" in result.output

    def test_validate_help(self):
        result = runner.invoke(app, ["validate", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output

    def test_export_help(self):
        result = runner.invoke(app, ["export", "--help"])
        assert result.exit_code == 0
        assert "--results" in result.output
        assert "--format" in result.output

    def test_info_help(self):
        result = runner.invoke(app, ["info", "--help"])
        assert result.exit_code == 0

    def test_editor_help(self):
        result = runner.invoke(app, ["editor", "--help"])
        assert result.exit_code == 0
        assert "--config" in result.output


# ---------------------------------------------------------------------------
# Tests: validate command
# ---------------------------------------------------------------------------

class TestValidateCommand:
    def test_valid_config(self, minimal_config_path, mock_esfex_config):
        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config):
            result = runner.invoke(app, ["validate", "--config", str(minimal_config_path)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_missing_config_file(self, tmp_path):
        missing = tmp_path / "nonexistent.yaml"
        result = runner.invoke(app, ["validate", "--config", str(missing)])
        assert result.exit_code != 0

    def test_invalid_config_exits_with_error(self, minimal_config_path):
        from esfex.config.loader import ConfigLoadError
        with patch(_PATCH_LOAD_CONFIG, side_effect=ConfigLoadError("bad config")):
            result = runner.invoke(app, ["validate", "--config", str(minimal_config_path)])
        assert result.exit_code != 0
        assert "bad config" in result.output

    def test_shows_config_summary(self, minimal_config_path, mock_esfex_config):
        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config):
            result = runner.invoke(app, ["validate", "--config", str(minimal_config_path)])
        assert result.exit_code == 0
        # The summary table should include system name
        assert "test_system" in result.output


# ---------------------------------------------------------------------------
# Tests: run command
# ---------------------------------------------------------------------------

class TestRunCommand:
    def test_dry_run_no_optimization(self, minimal_config_path, mock_esfex_config):
        """--dry-run should validate config but not invoke Orchestrator."""
        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config):
            result = runner.invoke(
                app,
                ["run", "--config", str(minimal_config_path), "--dry-run"],
            )
        assert result.exit_code == 0
        assert "dry run" in result.output.lower()

    def test_missing_config_file(self, tmp_path):
        result = runner.invoke(app, ["run", "--config", str(tmp_path / "nope.yaml")])
        assert result.exit_code != 0

    def test_verbose_flag_accepted(self, minimal_config_path, mock_esfex_config):
        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config):
            result = runner.invoke(
                app,
                ["run", "--config", str(minimal_config_path), "--dry-run", "--verbose"],
            )
        assert result.exit_code == 0

    def test_mode_override(self, minimal_config_path, mock_esfex_config):
        """The --mode flag should override the config's simulation_mode."""
        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config):
            result = runner.invoke(
                app,
                [
                    "run", "--config", str(minimal_config_path),
                    "--mode", "unit_commitment",
                    "--dry-run",
                ],
            )
        assert result.exit_code == 0
        # Config mode should have been overridden
        assert mock_esfex_config.simulation_mode == "unit_commitment"

    def test_solver_override(self, minimal_config_path, mock_esfex_config):
        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config):
            result = runner.invoke(
                app,
                [
                    "run", "--config", str(minimal_config_path),
                    "--solver", "cbc",
                    "--dry-run",
                ],
            )
        assert result.exit_code == 0
        assert mock_esfex_config.solver.name == "cbc"

    def test_config_load_error_handled(self, minimal_config_path):
        from esfex.config.loader import ConfigLoadError
        with patch(_PATCH_LOAD_CONFIG, side_effect=ConfigLoadError("parse error")):
            result = runner.invoke(
                app,
                ["run", "--config", str(minimal_config_path)],
            )
        assert result.exit_code != 0
        assert "parse error" in result.output

    def test_optimization_failure_handled(self, minimal_config_path, mock_esfex_config):
        """When Orchestrator.run() raises, the CLI should exit with code 1."""
        mock_orch_instance = MagicMock()
        mock_orch_instance.run.side_effect = RuntimeError("solver crashed")

        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config), \
             patch(_PATCH_ORCHESTRATOR, return_value=mock_orch_instance):
            result = runner.invoke(
                app,
                ["run", "--config", str(minimal_config_path)],
            )
        assert result.exit_code != 0
        assert "solver crashed" in result.output

    def test_successful_run(self, minimal_config_path, mock_esfex_config, tmp_path):
        """Successful Orchestrator.run() should exit with code 0."""
        mock_orch_instance = MagicMock()
        mock_orch_instance.run.return_value = []

        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config), \
             patch(_PATCH_ORCHESTRATOR, return_value=mock_orch_instance):
            result = runner.invoke(
                app,
                [
                    "run", "--config", str(minimal_config_path),
                    "--output", str(tmp_path / "results"),
                ],
            )
        assert result.exit_code == 0
        assert "completed" in result.output.lower()

    def test_years_flag_passed(self, minimal_config_path, mock_esfex_config, tmp_path):
        """The --years flag should be forwarded to Orchestrator.run()."""
        mock_orch_instance = MagicMock()
        mock_orch_instance.run.return_value = []

        with patch(_PATCH_LOAD_CONFIG, return_value=mock_esfex_config), \
             patch(_PATCH_ORCHESTRATOR, return_value=mock_orch_instance):
            result = runner.invoke(
                app,
                [
                    "run", "--config", str(minimal_config_path),
                    "--years", "5",
                    "--output", str(tmp_path / "results"),
                ],
            )
        assert result.exit_code == 0
        mock_orch_instance.run.assert_called_once_with(years=5)


# ---------------------------------------------------------------------------
# Tests: export command
# ---------------------------------------------------------------------------

class TestExportCommand:
    def test_csv_export(self, hdf5_results_path, tmp_path):
        output_dir = tmp_path / "export_csv"
        result = runner.invoke(
            app,
            [
                "export",
                "--results", str(hdf5_results_path),
                "--format", "csv",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0
        assert "Export completed" in result.output

    def test_json_export(self, hdf5_results_path, tmp_path):
        output_dir = tmp_path / "export_json"
        result = runner.invoke(
            app,
            [
                "export",
                "--results", str(hdf5_results_path),
                "--format", "json",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0

    def test_excel_export(self, hdf5_results_path, tmp_path):
        output_dir = tmp_path / "export_xlsx"
        result = runner.invoke(
            app,
            [
                "export",
                "--results", str(hdf5_results_path),
                "--format", "excel",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0

    def test_unknown_format_fails(self, hdf5_results_path, tmp_path):
        result = runner.invoke(
            app,
            [
                "export",
                "--results", str(hdf5_results_path),
                "--format", "parquet",
                "--output", str(tmp_path / "out"),
            ],
        )
        assert result.exit_code != 0

    def test_missing_results_file(self, tmp_path):
        result = runner.invoke(
            app,
            [
                "export",
                "--results", str(tmp_path / "missing.h5"),
                "--format", "csv",
            ],
        )
        assert result.exit_code != 0

    def test_csv_creates_output_files(self, hdf5_results_path, tmp_path):
        """Verify that CSV export actually produces summary CSV files."""
        output_dir = tmp_path / "csv_verify"
        result = runner.invoke(
            app,
            [
                "export",
                "--results", str(hdf5_results_path),
                "--format", "csv",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0
        assert (output_dir / "summary" / "total_cost.csv").exists()

    def test_json_creates_output_file(self, hdf5_results_path, tmp_path):
        output_dir = tmp_path / "json_verify"
        result = runner.invoke(
            app,
            [
                "export",
                "--results", str(hdf5_results_path),
                "--format", "json",
                "--output", str(output_dir),
            ],
        )
        assert result.exit_code == 0
        json_file = output_dir / f"{hdf5_results_path.stem}.json"
        assert json_file.exists()
        data = json.loads(json_file.read_text())
        assert "metadata" in data


# ---------------------------------------------------------------------------
# Tests: info command
# ---------------------------------------------------------------------------

class TestInfoCommand:
    def test_info_shows_version(self):
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        assert "esfex" in result.output.lower()

    def test_info_shows_python_version(self):
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        assert "Python" in result.output

    def test_info_checks_julia(self):
        """info should report julia availability without crashing."""
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        # Should mention Julia one way or another
        output_lower = result.output.lower()
        assert "julia" in output_lower


# ---------------------------------------------------------------------------
# Tests: editor command
# ---------------------------------------------------------------------------

class TestEditorCommand:
    def test_editor_help_works(self):
        result = runner.invoke(app, ["editor", "--help"])
        assert result.exit_code == 0
        assert "editor" in result.output.lower() or "GIS" in result.output


# ---------------------------------------------------------------------------
# Tests: plugin sub-commands
# ---------------------------------------------------------------------------

class TestPluginCommands:
    def test_plugin_help(self):
        result = runner.invoke(app, ["plugin", "--help"])
        assert result.exit_code == 0
        assert "plugin" in result.output.lower()

    def test_plugin_list_help(self):
        result = runner.invoke(app, ["plugin", "list", "--help"])
        assert result.exit_code == 0

    def test_plugin_list_empty(self, tmp_path, monkeypatch):
        """When no plugins exist, 'plugin list' reports none found."""
        from esfex.plugins import manager as pm_mod
        monkeypatch.setattr(pm_mod, "_USER_PLUGINS_DIR", tmp_path / "empty_plugins")
        # Reset singleton so it rescans
        from esfex.plugins import reset_plugin_manager
        reset_plugin_manager()
        result = runner.invoke(app, ["plugin", "list"])
        assert result.exit_code == 0
        assert "no plugins" in result.output.lower()
        reset_plugin_manager()

    def test_plugin_list_with_plugin(self, tmp_path, monkeypatch):
        """When a valid plugin exists, 'plugin list' shows it."""
        from esfex.plugins import manager as pm_mod
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        p = plugins_dir / "demo"
        p.mkdir()
        (p / "plugin.json").write_text(json.dumps({
            "name": "demo", "version": "0.1.0", "description": "A demo"
        }))
        (p / "__init__.py").write_text(
            "def create_plugin(ctx):\n"
            "    from esfex.plugins.protocol import ESFEXPlugin, PluginMeta\n"
            "    class P(ESFEXPlugin):\n"
            "        meta = PluginMeta(name='demo', version='0.1.0')\n"
            "    return P()\n"
        )
        monkeypatch.setattr(pm_mod, "_USER_PLUGINS_DIR", plugins_dir)
        from esfex.plugins import reset_plugin_manager
        reset_plugin_manager()
        result = runner.invoke(app, ["plugin", "list"])
        assert result.exit_code == 0
        assert "demo" in result.output
        reset_plugin_manager()

    def test_plugin_enable_help(self):
        result = runner.invoke(app, ["plugin", "enable", "--help"])
        assert result.exit_code == 0

    def test_plugin_disable_help(self):
        result = runner.invoke(app, ["plugin", "disable", "--help"])
        assert result.exit_code == 0

    def test_plugin_install_help(self):
        result = runner.invoke(app, ["plugin", "install", "--help"])
        assert result.exit_code == 0
        assert "--git" in result.output or "--zip" in result.output

    def test_plugin_uninstall_help(self):
        result = runner.invoke(app, ["plugin", "uninstall", "--help"])
        assert result.exit_code == 0
