# Testing

## Running Python Tests

### Full Test Suite

```bash
pytest
```

### With Coverage

```bash
pytest --cov=esfex --cov-report=html
open htmlcov/index.html
```

### Specific Test Files

```bash
# Configuration tests
pytest tests/test_schema.py

# Runner tests
pytest tests/test_runner.py

# CLI tests
pytest tests/test_cli.py

# Plugin tests (framework + GUI dialog)
pytest tests/test_plugins.py

# Sensitivity tests
pytest tests/test_sensitivity_engine.py
```

### Specific Test Functions

```bash
pytest tests/test_config.py::test_load_valid_config
pytest tests/test_runner.py -k "test_single_year"
```

### Verbose Output

```bash
pytest -v --tb=long
```


---


## Test Categories

### Unit Tests

Individual functions and classes tested in isolation:

```python
# tests/test_config.py
def test_load_valid_config():
    """Test loading a valid YAML configuration."""
    config = load_config("tests/fixtures/valid_config.yaml")
    assert isinstance(config, ESFEXConfig)
    assert len(config.systems) > 0

def test_penalty_defaults():
    """Test PenaltiesConfig default values."""
    penalties = PenaltiesConfig()
    assert penalties.loss_of_load == 10e6
    assert penalties.max_curtailment_ratio == 0.05
```

### Integration Tests

Cross-module interaction tests:

```python
# tests/test_adapters.py
def test_power_system_adapter_creates_input():
    """Test that PowerSystemAdapter creates valid Julia input."""
    config = load_config("tests/fixtures/single_node.yaml")
    adapter = PowerSystemAdapter(config.systems["island"], ...)
    input_data = adapter._create_input(...)
    assert input_data["demand"].shape == (48, 1)
```

### Solver Tests

Optimization model construction and solution:

```python
# tests/test_solver.py
@pytest.mark.slow
def test_single_node_solves():
    """Test that a single-node system solves successfully."""
    config = load_config("tests/fixtures/single_node.yaml")
    orchestrator = Orchestrator(config)
    results = orchestrator.run(num_years=1)
    assert results[0].feasible
```

### GUI Tests (Optional)

Require a display server (or Xvfb on headless systems):

```bash
# With display
pytest tests/test_gui.py

# Headless (Linux)
xvfb-run pytest tests/test_gui.py
```

### Plugin GUI Tests

Dialog and menu integration tests use mocked Qt widgets (no display server required):

```bash
pytest tests/test_plugins.py::TestPluginsDialog -v
```


---


## Test Fixtures

### Configuration Fixtures

Stored in `tests/fixtures/`:

| File | Description |
|------|-------------|
| `valid_config.yaml` | Minimal valid configuration |
| `single_node.yaml` | Single-node system with diesel + solar + battery |
| `multi_node.yaml` | 3-node system with DC power flow |
| `multi_system.yaml` | Two interconnected systems |
| `invalid_config.yaml` | Configuration with intentional errors |

### Data Fixtures

| File | Description |
|------|-------------|
| `demand_single.csv` | 8760-hour demand for single node |
| `demand_multi.csv` | Multi-node demand data |
| `solar_availability.csv` | Solar capacity factor profile |
| `wind_availability.csv` | Wind capacity factor profile |

### Pytest Fixtures

Defined in `tests/conftest.py`:

```python
@pytest.fixture
def valid_config():
    """Load a valid test configuration."""
    return load_config("tests/fixtures/valid_config.yaml")

@pytest.fixture
def single_node_config():
    """Load single-node test configuration."""
    return load_config("tests/fixtures/single_node.yaml")

@pytest.fixture
def mock_julia():
    """Mock Julia bridge for unit tests."""
    with patch("esfex.bridge.julia_setup.get_julia") as mock:
        yield mock
```


---


## Running Julia Tests

```bash
cd src/esfex/julia
julia --project=. -e 'using Pkg; Pkg.test()'
```

Or from the Julia REPL:

```julia
] activate src/esfex/julia
] test
```

### Julia Test Structure

```julia
# test/runtests.jl
using Test
using ESFEX

@testset "ESFEX.jl" begin
    @testset "Types" begin
        # Type construction tests
    end

    @testset "Power System" begin
        # Operational dispatch tests
    end

    @testset "Master Problem" begin
        # Capacity expansion tests
    end

    @testset "Transmission DC" begin
        # DC power flow tests
    end
end
```


---


## Writing Tests

### Test Naming Convention

```python
def test_<what>_<condition>_<expected>():
    """<One-line description of what is being tested>."""
```

Examples:

```python
def test_load_config_missing_file_raises_error():
def test_battery_soc_respects_cyclic_constraint():
def test_ev_fleet_grows_with_scurve():
```

### Testing Optimization Results

Use tolerance-based assertions for solver results:

```python
def test_power_balance():
    """Total generation equals total demand (within tolerance)."""
    results = run_simulation(config)
    total_gen = results.generation.sum()
    total_demand = results.demand.sum()
    assert abs(total_gen - total_demand) / total_demand < 0.01  # 1% tolerance
```

### Testing Configuration Validation

```python
def test_negative_capacity_rejected():
    """Negative rated power should fail validation."""
    with pytest.raises(ValidationError):
        GeneratorConfig(
            name="test",
            type="Renewable",
            fuel="Solar",
            rated_power=[-100.0],  # Invalid
            # ... other fields
        )
```


---


## Test Patterns for New Features

### Pattern: New Configuration Field

```python
# 1. Test the default value
def test_new_field_default():
    """New field should have a sensible default."""
    config = MyConfig()
    assert config.new_field == expected_default

# 2. Test explicit values from YAML
def test_new_field_from_yaml(tmp_path):
    """New field should load from YAML."""
    yaml_content = """
    my_section:
      new_field: 42.0
    """
    config_file = tmp_path / "test.yaml"
    config_file.write_text(yaml_content)
    config = load_config(config_file)
    assert config.my_section.new_field == 42.0

# 3. Test validation rejects invalid values
def test_new_field_rejects_negative():
    """Negative values should raise ValidationError."""
    with pytest.raises(ValidationError, match="new_field"):
        MyConfig(new_field=-1.0)
```

### Pattern: New Constraint in Julia

```python
@pytest.mark.julia
def test_new_constraint_bounds_output():
    """New constraint should limit generator output."""
    config = load_config("tests/fixtures/single_node.yaml")
    # Modify config to trigger the new constraint
    config.systems["island"].my_new_limit = 50.0

    adapter = PowerSystemAdapter(config.systems["island"], ...)
    result = adapter.solve(...)

    # Check that the constraint is respected
    max_output = result.generation.max()
    assert max_output <= 50.0 + 1e-6  # Solver tolerance
```

### Pattern: New Plugin Hook

```python
def test_plugin_hook_called_during_simulation(tmp_path, mock_julia):
    """Custom hook should be invoked at the correct simulation stage."""
    from esfex.plugins import reset_plugin_manager, get_plugin_manager

    reset_plugin_manager()

    call_log = []

    class TestPlugin(ESFEXPlugin):
        def pre_year(self, *, year, year_idx, units_config, config):
            call_log.append(("pre_year", year, year_idx))

    # Register and run
    pm = get_plugin_manager()
    ctx = PluginContext(config=None, plugin_dir=tmp_path, data_dir=tmp_path)
    plugin = TestPlugin(ctx)
    plugin.meta = PluginMeta(name="test_hook", version="0.1.0")
    pm._plugins.append(plugin)
    pm._loaded = True

    # ... run simulation ...

    assert len(call_log) > 0
    assert call_log[0][0] == "pre_year"
```

### Pattern: Adapter Bridge Serialization

```python
def test_new_field_reaches_julia(mock_julia):
    """New config field should be serialized to Julia input."""
    config = load_config("tests/fixtures/single_node.yaml")
    adapter = PowerSystemAdapter(config.systems["island"], ...)

    # Capture the Julia call
    input_data = adapter._create_input(...)

    # Verify the field was passed through
    assert hasattr(input_data, "new_field") or "new_field" in input_data
```


---


## Coverage Reports

### Generating Coverage

```bash
# HTML report (detailed, browsable)
pytest --cov=esfex --cov-report=html --cov-report=term-missing
open htmlcov/index.html

# Terminal summary
pytest --cov=esfex --cov-report=term-missing

# XML report (for CI integration)
pytest --cov=esfex --cov-report=xml
```

### Reading the Coverage Report

`htmlcov/index.html` highlights:

- **Green lines**: Covered by at least one test
- **Red lines**: Not covered --- these are candidates for new tests
- **Yellow lines**: Partially covered (e.g., only one branch of an `if/else`)

Focus on red lines in critical modules (`runner.py`, `adapters.py`, `schema.py`). `visualization/` has lower coverage targets due to GUI automation difficulty.

### Excluding Lines from Coverage

Intentionally excluded lines (e.g., `if TYPE_CHECKING:` blocks, `__main__` guards):

```python
# In pyproject.toml or .coveragerc
[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if TYPE_CHECKING:",
    "if __name__ == .__main__.:",
    "raise NotImplementedError",
]
```


---


## Markers

Custom markers for test categorization:

```python
@pytest.mark.slow       # Long-running solver tests
@pytest.mark.gui        # GUI tests (require display)
@pytest.mark.julia      # Tests requiring Julia runtime
@pytest.mark.solver     # Tests requiring a specific solver
```

Run specific categories:

```bash
# Skip slow tests
pytest -m "not slow"

# Only Julia tests
pytest -m julia

# Only fast unit tests
pytest -m "not slow and not gui and not julia"
```

Register markers in `pyproject.toml` to suppress warnings:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: long-running solver tests",
    "gui: GUI tests requiring a display server",
    "julia: tests that require a running Julia runtime",
    "solver: tests that require a specific solver backend",
]
```


---


## Continuous Integration

### Pipeline Stages

1. **Lint** (`ruff check src/ tests/`) --- Style violations and common errors. Under 10 seconds.
2. **Type check** (`mypy src/esfex/`) --- Static type analysis.
3. **Unit tests** (`pytest -m "not slow and not gui"`) --- No Julia or display server required. Target: under 60 seconds.
4. **Integration tests** (`pytest -m "slow"`) --- Full Python-Julia pipeline. Runs on merge to `main` only.
5. **Coverage report** (`pytest --cov=esfex`) --- Uploaded as CI artifact. Coverage regressions below module targets block merge.

### Test Matrix

| Python | Julia | OS |
|--------|-------|----|
| 3.10 | 1.9 | Ubuntu 22.04 |
| 3.11 | 1.10 | Ubuntu 22.04 |
| 3.12 | 1.11 | Ubuntu 22.04 |
| 3.12 | 1.11 | macOS 14 |
| 3.12 | 1.11 | Windows 2022 |

### CI Workflow Summary

```yaml
# .github/workflows/ci.yml (simplified)
name: CI
on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install ruff mypy
      - run: ruff check src/ tests/
      - run: mypy src/esfex/

  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-22.04, macos-14, windows-2022]
        python: ["3.10", "3.11", "3.12"]
        julia: ["1.9", "1.10", "1.11"]
        exclude:
          - os: macos-14
            python: "3.10"
          - os: windows-2022
            python: "3.10"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: ${{ matrix.python }} }
      - uses: julia-actions/setup-julia@v2
        with: { version: ${{ matrix.julia }} }
      - run: pip install -e ".[dev]"
      - run: pytest -m "not gui" --cov=esfex --cov-report=xml
      - uses: codecov/codecov-action@v4

  integration:
    if: github.ref == 'refs/heads/main'
    needs: test
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - uses: julia-actions/setup-julia@v2
        with: { version: "1.11" }
      - run: pip install -e ".[dev]"
      - run: pytest -m "slow" --tb=long
```


---


### Troubleshooting CI Failures

- **Flaky solver tests**: Use tolerance-based assertions (`abs(a - b) < tol`) instead of exact equality across platforms.
- **Julia precompilation timeout**: Cache `~/.julia/` between runs if precompilation exceeds the job timeout.
- **GUI tests on CI**: Excluded by default (`@pytest.mark.gui`). Require `xvfb-run` on Linux.


---


## Coverage Goals

| Module | Target |
|--------|--------|
| `config/` | 90% |
| `io/` | 85% |
| `models/` | 85% |
| `bridge/` | 70% |
| `plugins/` | 80% |
| `runner.py` | 75% |
| `visualization/` | 50% |
| Overall | 75% |

Enforced in CI. Pull requests that drop coverage below module targets receive a review warning.
