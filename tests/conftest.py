"""
pytest configuration and fixtures for ESFEX tests.
"""
import pytest
from pathlib import Path
import numpy as np
import yaml


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "julia: marks tests as requiring Julia (skip if Julia unavailable)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip Julia tests if juliacall is not available."""
    try:
        import juliacall
        julia_available = True
    except ImportError:
        julia_available = False

    if not julia_available:
        skip_julia = pytest.mark.skip(reason="Julia/juliacall not available")
        for item in items:
            if "julia" in item.keywords:
                item.add_marker(skip_julia)


@pytest.fixture
def project_root():
    """Return the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def configs_dir(project_root):
    """Return the configs directory."""
    return project_root / "configs"


@pytest.fixture
def fixtures_dir():
    """Return the test fixtures directory."""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def small_system_config(fixtures_dir):
    """Load the small 2-node test system configuration."""
    config_path = fixtures_dir / "small_system.yaml"
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


@pytest.fixture
def sample_demand():
    """Generate sample demand data (24 hours × 2 nodes)."""
    np.random.seed(42)
    hours = 24
    nodes = 2

    # Base load pattern (typical daily profile)
    base_pattern = np.array([
        0.7, 0.65, 0.6, 0.58, 0.6, 0.7,   # 0-5
        0.85, 1.0, 0.95, 0.9, 0.88, 0.9,  # 6-11
        0.92, 0.9, 0.88, 0.9, 0.95, 1.0,  # 12-17
        0.95, 0.9, 0.85, 0.8, 0.75, 0.7   # 18-23
    ])

    # Scale for different nodes
    demand = np.zeros((hours, nodes))
    demand[:, 0] = base_pattern * 100  # Node 0: 100 MW peak
    demand[:, 1] = base_pattern * 150  # Node 1: 150 MW peak

    return demand


@pytest.fixture
def sample_solar_availability():
    """Generate sample solar availability (24 hours × 2 nodes)."""
    hours = 24
    nodes = 2

    # Typical solar pattern
    solar_pattern = np.array([
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,     # 0-5
        0.05, 0.2, 0.4, 0.6, 0.8, 0.9,    # 6-11
        0.95, 0.9, 0.8, 0.6, 0.4, 0.15,   # 12-17
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0      # 18-23
    ])

    availability = np.zeros((hours, nodes))
    availability[:, 0] = solar_pattern * 0.95  # Node 0: 95% max
    availability[:, 1] = solar_pattern * 1.0   # Node 1: 100% max

    return availability


@pytest.fixture
def sample_wind_availability():
    """Generate sample wind availability (24 hours × 2 nodes)."""
    np.random.seed(123)
    hours = 24
    nodes = 2

    # Wind is more variable
    availability = np.random.uniform(0.2, 0.7, (hours, nodes))

    return availability
