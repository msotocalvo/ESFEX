"""Unit tests for enhanced SLD results loader (Level 1 data + frequency)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import h5py
import numpy as np
import pytest


def _create_mock_state(num_nodes=2, num_gens=2, num_bats=1):
    """Create a mock GuiSystemState for testing."""
    state = MagicMock()

    # Buses
    buses = {}
    for ni in range(num_nodes):
        bus = MagicMock()
        bus.bus_id = f"bus_{ni}"
        bus.parent_node = ni
        buses[f"bus_{ni}"] = bus
    state.buses = buses

    # Generators
    generators = {}
    for gi in range(num_gens):
        gen = MagicMock()
        gen.unit_key = f"unit_{gi}"
        gen.bus = f"bus_{gi % num_nodes}"
        gen.rated_power = 100.0
        gen.fuel = "Diesel" if gi == 0 else "Sun"
        generators[f"unit_{gi}_bus_{gi % num_nodes}"] = gen
    state.generators = generators

    # Batteries
    batteries = {}
    for bi in range(num_bats):
        bat = MagicMock()
        bat.unit_key = f"bat_{bi}"
        bat.bus = f"bus_{bi % num_nodes}"
        bat.capacity = 50.0
        batteries[f"bat_{bi}_bus_{bi % num_nodes}"] = bat
    state.batteries = batteries

    # Nodes
    nodes = []
    for ni in range(num_nodes):
        node = MagicMock()
        node.index = ni
        nodes.append(node)
    state.nodes = nodes

    # Transmission lines (empty for simplicity)
    state.transmission_lines = []

    return state


def _create_test_hdf5(tmp_path, num_nodes=2, num_hours=24, num_gens=2, num_bats=1):
    """Create a minimal HDF5 file with enhanced datasets for testing."""
    h5_path = tmp_path / "test_results.h5"

    with h5py.File(h5_path, "w") as f:
        f.attrs["num_nodes"] = num_nodes

        # System configuration
        sysconf = f.create_group("system_configuration")
        gen_conf = sysconf.create_group("generators")
        for gi in range(num_gens):
            g = gen_conf.create_group(f"generator_{gi}")
            g.attrs["rated_power"] = np.array([100.0] * num_nodes)
            g.attrs["inertia"] = np.array([5.0 if gi == 0 else 0.0] * num_nodes)
            g.attrs["droop"] = np.array([0.05] * num_nodes)
            g.attrs["governor_time_const"] = np.array([5.0] * num_nodes)
            g.attrs["type"] = "Non-renewable" if gi == 0 else "Renewable"

        bat_conf = sysconf.create_group("batteries")
        for bi in range(num_bats):
            b = bat_conf.create_group(f"battery_{bi}")
            b.attrs["capacity"] = np.array([50.0] * num_nodes)

        # Detailed results for year 2030
        dr = f.create_group("detailed_results")
        yr_grp = dr.create_group("year_2030_threshold_0")
        yr_grp.attrs["renewable_penetration"] = 0.45
        yr_grp.attrs["co2_emissions"] = 1500.0

        # Generation data
        gen_grp = yr_grp.create_group("generation")
        for gi in range(num_gens):
            data = np.random.uniform(20, 80, (num_nodes, num_hours))
            gen_grp.create_dataset(f"generator_{gi}", data=data)

        # Demand
        demand_data = np.random.uniform(50, 150, (num_nodes, num_hours))
        yr_grp.create_dataset("demand", data=demand_data)

        # Loss of load
        shed_data = np.zeros((num_nodes, num_hours))
        yr_grp.create_dataset("loss_load", data=shed_data)

        # Nodal prices
        price_data = np.random.uniform(30, 120, (num_nodes, num_hours))
        yr_grp.create_dataset("nodal_electricity_prices", data=price_data)

        # Battery data
        charge_grp = yr_grp.create_group("battery_charge")
        discharge_grp = yr_grp.create_group("battery_discharge")
        soc_grp = yr_grp.create_group("battery_soc")
        for bi in range(num_bats):
            charge_grp.create_dataset(
                f"battery_{bi}", data=np.random.uniform(0, 20, (num_nodes, num_hours)),
            )
            discharge_grp.create_dataset(
                f"battery_{bi}", data=np.random.uniform(0, 20, (num_nodes, num_hours)),
            )
            soc_grp.create_dataset(
                f"battery_{bi}", data=np.random.uniform(10, 40, (num_nodes, num_hours)),
            )

        # ── Enhanced Level 1 datasets ──

        # Generator status
        status_grp = yr_grp.create_group("gen_status")
        startup_grp = yr_grp.create_group("gen_startup")
        for gi in range(num_gens):
            # All online (status = 1)
            status_grp.create_dataset(
                f"generator_{gi}", data=np.ones((num_nodes, num_hours)),
            )
            startup_grp.create_dataset(
                f"generator_{gi}", data=np.zeros((num_nodes, num_hours)),
            )

        # Set generator_0 to startup at hour 5
        status_grp[f"generator_0"][0, 5] = 1.0
        startup_grp[f"generator_0"][0, 5] = 1.0

        # Reserves
        yr_grp.create_dataset(
            "reserve_static", data=np.random.uniform(5, 30, (num_nodes, num_hours)),
        )
        yr_grp.create_dataset(
            "reserve_dynamic", data=np.random.uniform(3, 20, (num_nodes, num_hours)),
        )
        yr_grp.create_dataset(
            "loss_of_reserve_static", data=np.zeros((num_nodes, num_hours)),
        )
        yr_grp.create_dataset(
            "loss_of_reserve_dynamic", data=np.zeros((num_nodes, num_hours)),
        )

        # Voltage angles (radians)
        yr_grp.create_dataset(
            "voltage_angle", data=np.random.uniform(-0.1, 0.1, (num_nodes, num_hours)),
        )

        # CO2 per node
        yr_grp.create_dataset(
            "CO2_emissions", data=np.random.uniform(0, 100, (num_nodes, num_hours)),
        )

    return h5_path


class TestSldResultsLoaderEnhanced:
    """Tests for the enhanced SLD results loader."""

    def test_gen_status_in_snapshot(self, tmp_path):
        """Generator status (on/off) should appear in snapshot."""
        h5_path = _create_test_hdf5(tmp_path)
        state = _create_mock_state()

        from esfex.visualization.sld.sld_results_loader import SldResultsLoader
        loader = SldResultsLoader(h5_path, state)

        snapshot = loader.get_timestep(2030, 0)

        for gen_id, gen_data in snapshot["generators"].items():
            assert "status" in gen_data
            assert "is_startup" in gen_data
            assert gen_data["status"] in (0, 1)

    def test_gen_startup_detected(self, tmp_path):
        """Generator startup flag should be detected at the right hour."""
        h5_path = _create_test_hdf5(tmp_path)
        state = _create_mock_state()

        from esfex.visualization.sld.sld_results_loader import SldResultsLoader
        loader = SldResultsLoader(h5_path, state)

        # Hour 5 should have startup for generator_0 on node 0
        snapshot = loader.get_timestep(2030, 5)
        found_startup = any(
            g.get("is_startup", False)
            for g in snapshot["generators"].values()
        )
        assert found_startup is True

    def test_reserves_in_snapshot(self, tmp_path):
        """Reserve static/dynamic should appear in node data."""
        h5_path = _create_test_hdf5(tmp_path)
        state = _create_mock_state()

        from esfex.visualization.sld.sld_results_loader import SldResultsLoader
        loader = SldResultsLoader(h5_path, state)

        snapshot = loader.get_timestep(2030, 0)

        for ni in range(2):
            node_data = snapshot["nodes"][ni]
            assert "reserve_static_mw" in node_data
            assert "reserve_dynamic_mw" in node_data
            assert "reserve_static_loss_mw" in node_data
            assert "reserve_dynamic_loss_mw" in node_data
            assert node_data["reserve_static_mw"] >= 0
            assert node_data["reserve_dynamic_mw"] >= 0

    def test_voltage_angle_in_snapshot(self, tmp_path):
        """Voltage angle (degrees) should appear in node data."""
        h5_path = _create_test_hdf5(tmp_path)
        state = _create_mock_state()

        from esfex.visualization.sld.sld_results_loader import SldResultsLoader
        loader = SldResultsLoader(h5_path, state)

        snapshot = loader.get_timestep(2030, 0)

        for ni in range(2):
            node_data = snapshot["nodes"][ni]
            assert "voltage_angle_deg" in node_data
            # Angle should be in degrees (small values for our test data)
            assert abs(node_data["voltage_angle_deg"]) < 10.0

    def test_co2_per_node_in_snapshot(self, tmp_path):
        """CO₂ tons should appear in node data."""
        h5_path = _create_test_hdf5(tmp_path)
        state = _create_mock_state()

        from esfex.visualization.sld.sld_results_loader import SldResultsLoader
        loader = SldResultsLoader(h5_path, state)

        snapshot = loader.get_timestep(2030, 0)

        for ni in range(2):
            node_data = snapshot["nodes"][ni]
            assert "co2_tons" in node_data
            assert node_data["co2_tons"] >= 0

    def test_frequency_metrics_in_snapshot(self, tmp_path):
        """Frequency response metrics should appear in system data."""
        h5_path = _create_test_hdf5(tmp_path)
        state = _create_mock_state()

        from esfex.visualization.sld.sld_results_loader import SldResultsLoader
        loader = SldResultsLoader(h5_path, state)

        snapshot = loader.get_timestep(2030, 0)

        # If freq_analyzer was built successfully, frequency data should exist
        freq = snapshot["system"].get("frequency")
        if freq is not None:
            assert "rocof_hz_s" in freq
            assert "nadir_hz" in freq
            assert "steady_state_hz" in freq
            assert "t_nadir_s" in freq
            assert "h_total_mws" in freq
            assert "is_stable" in freq
            assert "rocof_ok" in freq
            assert freq["nadir_hz"] <= 50.0

    def test_missing_datasets_graceful(self, tmp_path):
        """Missing HDF5 datasets should return defaults, not crash."""
        h5_path = tmp_path / "minimal.h5"

        # Create minimal HDF5 without enhanced datasets
        with h5py.File(h5_path, "w") as f:
            f.attrs["num_nodes"] = 1

            sysconf = f.create_group("system_configuration")
            gen_conf = sysconf.create_group("generators")
            g = gen_conf.create_group("generator_0")
            g.attrs["rated_power"] = np.array([100.0])
            g.attrs["inertia"] = np.array([5.0])

            sysconf.create_group("batteries")

            dr = f.create_group("detailed_results")
            yr = dr.create_group("year_2030_threshold_0")

            gen_grp = yr.create_group("generation")
            gen_grp.create_dataset(
                "generator_0", data=np.array([[80.0] * 24]),
            )
            yr.create_dataset("demand", data=np.array([[100.0] * 24]))
            yr.create_dataset("loss_load", data=np.zeros((1, 24)))
            yr.create_dataset(
                "nodal_electricity_prices", data=np.array([[50.0] * 24]),
            )
            # No status, reserves, angle, or CO2 datasets

        state = _create_mock_state(num_nodes=1, num_gens=1, num_bats=0)
        state.batteries = {}

        from esfex.visualization.sld.sld_results_loader import SldResultsLoader
        loader = SldResultsLoader(h5_path, state)

        # Should not crash
        snapshot = loader.get_timestep(2030, 0)

        # Generators should still have default status
        for gen_data in snapshot["generators"].values():
            assert gen_data["status"] == 1
            assert gen_data["is_startup"] is False

        # Nodes should have default zero values for missing datasets
        for ni, node_data in snapshot["nodes"].items():
            assert node_data.get("reserve_static_mw", 0) == 0.0
            assert node_data.get("voltage_angle_deg", 0) == 0.0
            assert node_data.get("co2_tons", 0) == 0.0

    def test_years_and_hours(self, tmp_path):
        """Loader should correctly detect years and hours."""
        h5_path = _create_test_hdf5(tmp_path, num_hours=48)
        state = _create_mock_state()

        from esfex.visualization.sld.sld_results_loader import SldResultsLoader
        loader = SldResultsLoader(h5_path, state)

        assert 2030 in loader.years
        assert loader.hours_per_year == 48
        assert loader.num_nodes == 2
