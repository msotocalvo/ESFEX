"""
Tests for esfex.bridge.julia_setup module.

All Julia-dependent code is mocked with unittest.mock so that
no real Julia runtime is required.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import esfex.bridge.julia_setup as julia_setup_mod
from esfex.bridge.julia_setup import (
    check_julia_available,
    create_julia_optimizer,
    get_julia,
    get_julia_path,
    get_julia_version,
    get_esfex_module,
    initialize_julia,
    precompile_esfex,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_globals(monkeypatch):
    """Reset module-level globals before *and* after every test."""
    monkeypatch.setattr(julia_setup_mod, "_julia_instance", None)
    monkeypatch.setattr(julia_setup_mod, "_esfex_module", None)
    yield
    monkeypatch.setattr(julia_setup_mod, "_julia_instance", None)
    monkeypatch.setattr(julia_setup_mod, "_esfex_module", None)


# ---------------------------------------------------------------------------
# get_julia_path
# ---------------------------------------------------------------------------


class TestGetJuliaPath:
    """Tests for get_julia_path()."""

    def test_returns_path_object(self):
        result = get_julia_path()
        assert isinstance(result, Path)

    def test_ends_with_julia_directory(self):
        result = get_julia_path()
        assert result.name == "julia"

    def test_relative_to_bridge_package(self):
        """The julia dir should be a sibling of the bridge package's parent."""
        bridge_dir = Path(julia_setup_mod.__file__).parent  # .../bridge/
        expected = bridge_dir.parent / "julia"
        assert get_julia_path() == expected

    def test_is_absolute_path(self):
        result = get_julia_path()
        assert result.is_absolute()


# ---------------------------------------------------------------------------
# check_julia_available
# ---------------------------------------------------------------------------


class TestCheckJuliaAvailable:
    """Tests for check_julia_available()."""

    def test_returns_true_when_juliacall_importable(self):
        mock_module = MagicMock()
        with patch.dict(sys.modules, {"juliacall": mock_module}):
            assert check_julia_available() is True

    def test_returns_false_on_import_error(self):
        with patch.dict(sys.modules, {"juliacall": None}):
            # Setting to None in sys.modules causes ImportError
            # But we need to be more explicit - remove and patch __import__
            pass

        # Safest: directly patch the import inside the function
        with patch("builtins.__import__", side_effect=ImportError("no juliacall")):
            assert check_julia_available() is False

    def test_returns_false_on_generic_exception(self):
        with patch("builtins.__import__", side_effect=RuntimeError("broken")):
            assert check_julia_available() is False

    def test_return_type_is_bool(self):
        with patch("builtins.__import__", side_effect=ImportError):
            result = check_julia_available()
            assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# initialize_julia
# ---------------------------------------------------------------------------


class TestInitializeJulia:
    """Tests for initialize_julia()."""

    def _make_mock_jl(self, tmp_path):
        """Create a mock juliacall.Main with a valid Project.toml."""
        # Create a fake Project.toml so the path check passes
        julia_dir = get_julia_path()
        project_toml = julia_dir / "Project.toml"

        mock_jl = MagicMock()
        mock_jl.seval = MagicMock(return_value=None)
        mock_jl.ESFEX = MagicMock()
        return mock_jl, project_toml

    def test_raises_import_error_when_no_juliacall(self):
        with patch("builtins.__import__", side_effect=ImportError("no juliacall")):
            with pytest.raises(ImportError, match="juliacall is required"):
                initialize_julia()

    def test_returns_julia_main_on_success(self, tmp_path):
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        # Need Project.toml to exist
        julia_path = get_julia_path()
        project_toml = julia_path / "Project.toml"

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            (tmp_path / "Project.toml").touch()
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "ESFEX.jl").touch()

            # Patch the juliacall import inside initialize_julia
            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                result = initialize_julia()

        assert result is mock_jl

    def test_calls_pkg_activate(self, tmp_path):
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            (tmp_path / "Project.toml").touch()
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "ESFEX.jl").touch()

            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                initialize_julia()

        # Check that seval was called with Pkg.activate
        seval_calls = [str(c) for c in mock_jl.seval.call_args_list]
        activate_calls = [c for c in seval_calls if "Pkg.activate" in c]
        assert len(activate_calls) >= 1

    def test_calls_include_esfex_jl(self, tmp_path):
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            (tmp_path / "Project.toml").touch()
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "ESFEX.jl").touch()

            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                initialize_julia()

        seval_calls = [str(c) for c in mock_jl.seval.call_args_list]
        include_calls = [c for c in seval_calls if "include" in c]
        assert len(include_calls) >= 1

    def test_calls_using_esfex(self, tmp_path):
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            (tmp_path / "Project.toml").touch()
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "ESFEX.jl").touch()

            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                initialize_julia()

        seval_calls = [str(c) for c in mock_jl.seval.call_args_list]
        using_calls = [c for c in seval_calls if "using .ESFEX" in c]
        assert len(using_calls) >= 1

    def test_cached_instance_returned_on_second_call(self, monkeypatch, tmp_path):
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        # Pre-set the global so initialize_julia returns immediately
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        result = initialize_julia()
        assert result is mock_jl

    def test_sets_julia_num_threads_env(self, tmp_path, monkeypatch):
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        # Remove env var if present
        monkeypatch.delenv("JULIA_NUM_THREADS", raising=False)

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            (tmp_path / "Project.toml").touch()
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "ESFEX.jl").touch()

            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                import os
                initialize_julia(threads=8)
                # setdefault should have set it
                assert os.environ.get("JULIA_NUM_THREADS") == "8"

    def test_raises_runtime_error_when_project_toml_missing(self, tmp_path):
        mock_jl = MagicMock()

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            # No Project.toml created
            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                with pytest.raises(RuntimeError, match="Project.toml not found"):
                    initialize_julia()

    def test_raises_runtime_error_on_julia_failure(self, tmp_path):
        mock_jl = MagicMock()
        mock_jl.seval.side_effect = Exception("Julia crashed")

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            (tmp_path / "Project.toml").touch()
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "ESFEX.jl").touch()

            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                with pytest.raises(RuntimeError, match="Julia initialization failed"):
                    initialize_julia()

    def test_handles_invalid_redefinition_error(self, tmp_path):
        """When 'invalid redefinition of constant' occurs, should reuse module."""
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        call_count = [0]

        def seval_side_effect(code):
            call_count[0] += 1
            if "include(" in code:
                raise Exception("invalid redefinition of constant ESFEX")
            return None

        mock_jl.seval.side_effect = seval_side_effect

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            (tmp_path / "Project.toml").touch()
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "ESFEX.jl").touch()

            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                result = initialize_julia()

        assert result is mock_jl

    def test_handles_load_error_with_instantiate(self, tmp_path):
        """When a LoadError occurs, should try Pkg.instantiate then retry."""
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        call_count = [0]

        def seval_side_effect(code):
            call_count[0] += 1
            if "include(" in code and call_count[0] <= 2:
                raise Exception("LoadError: package not found")
            return None

        mock_jl.seval.side_effect = seval_side_effect

        with patch(
            "esfex.bridge.julia_setup.get_julia_path",
            return_value=tmp_path,
        ):
            (tmp_path / "Project.toml").touch()
            (tmp_path / "src").mkdir(exist_ok=True)
            (tmp_path / "src" / "ESFEX.jl").touch()

            fake_juliacall = MagicMock()
            fake_juliacall.Main = mock_jl
            with patch.dict(sys.modules, {"juliacall": fake_juliacall}):
                result = initialize_julia()

        assert result is mock_jl


# ---------------------------------------------------------------------------
# get_julia
# ---------------------------------------------------------------------------


class TestGetJulia:
    """Tests for get_julia()."""

    def test_calls_initialize_when_none(self, tmp_path):
        mock_jl = MagicMock()
        mock_jl.ESFEX = MagicMock()

        with patch(
            "esfex.bridge.julia_setup.initialize_julia",
            return_value=mock_jl,
        ) as mock_init:
            result = get_julia()

        mock_init.assert_called_once()
        assert result is mock_jl

    def test_returns_cached_when_set(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        result = get_julia()
        assert result is mock_jl

    def test_does_not_reinitialize_when_cached(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        with patch(
            "esfex.bridge.julia_setup.initialize_julia",
        ) as mock_init:
            get_julia()

        mock_init.assert_not_called()

    def test_propagates_initialization_error(self):
        with patch(
            "esfex.bridge.julia_setup.initialize_julia",
            side_effect=RuntimeError("init failed"),
        ):
            with pytest.raises(RuntimeError, match="init failed"):
                get_julia()


# ---------------------------------------------------------------------------
# get_esfex_module
# ---------------------------------------------------------------------------


class TestGetEsfexModule:
    """Tests for get_esfex_module()."""

    def test_returns_cached_module(self, monkeypatch):
        mock_module = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_esfex_module", mock_module)

        result = get_esfex_module()
        assert result is mock_module

    def test_initializes_julia_when_module_is_none(self, monkeypatch):
        mock_jl = MagicMock()
        mock_module = MagicMock()

        def fake_init():
            monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)
            monkeypatch.setattr(julia_setup_mod, "_esfex_module", mock_module)
            return mock_jl

        with patch(
            "esfex.bridge.julia_setup.initialize_julia",
            side_effect=fake_init,
        ):
            # get_julia will call initialize_julia
            with patch(
                "esfex.bridge.julia_setup.get_julia",
                side_effect=fake_init,
            ):
                result = get_esfex_module()

        assert result is mock_module

    def test_raises_runtime_error_when_still_none_after_init(self, monkeypatch):
        """If get_julia() succeeds but _esfex_module is still None, raise."""
        mock_jl = MagicMock()

        with patch(
            "esfex.bridge.julia_setup.get_julia",
            return_value=mock_jl,
        ):
            # _esfex_module stays None
            with pytest.raises(RuntimeError, match="ESFEX Julia module not loaded"):
                get_esfex_module()

    def test_does_not_call_get_julia_when_cached(self, monkeypatch):
        mock_module = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_esfex_module", mock_module)

        with patch("esfex.bridge.julia_setup.get_julia") as mock_get:
            get_esfex_module()
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# create_julia_optimizer
# ---------------------------------------------------------------------------


class TestCreateJuliaOptimizer:
    """Tests for create_julia_optimizer()."""

    def test_calls_seval_with_solver_name(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        create_julia_optimizer(solver="highs")

        call_args = mock_jl.seval.call_args[0][0]
        assert "highs" in call_args
        assert "ESFEX.create_optimizer" in call_args

    def test_solver_name_lowered(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        create_julia_optimizer(solver="GUROBI")

        call_args = mock_jl.seval.call_args[0][0]
        assert "gurobi" in call_args
        assert "GUROBI" not in call_args

    def test_passes_threads_parameter(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        create_julia_optimizer(threads=16)

        call_args = mock_jl.seval.call_args[0][0]
        assert "threads=16" in call_args

    def test_passes_time_limit(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        create_julia_optimizer(time_limit=7200.0)

        call_args = mock_jl.seval.call_args[0][0]
        assert "time_limit=7200.0" in call_args

    def test_passes_gap(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        create_julia_optimizer(gap=0.001)

        call_args = mock_jl.seval.call_args[0][0]
        assert "gap=0.001" in call_args

    def test_verbose_false(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        create_julia_optimizer(verbose=False)

        call_args = mock_jl.seval.call_args[0][0]
        assert "verbose=false" in call_args

    def test_verbose_true(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        create_julia_optimizer(verbose=True)

        call_args = mock_jl.seval.call_args[0][0]
        assert "verbose=true" in call_args

    def test_returns_seval_result(self, monkeypatch):
        mock_jl = MagicMock()
        mock_optimizer = MagicMock()
        mock_jl.seval.return_value = mock_optimizer
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        result = create_julia_optimizer()
        assert result is mock_optimizer

    def test_default_parameters(self, monkeypatch):
        mock_jl = MagicMock()
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        create_julia_optimizer()

        call_args = mock_jl.seval.call_args[0][0]
        assert 'solver_name="highs"' in call_args
        assert "threads=4" in call_args
        assert "time_limit=3600.0" in call_args
        assert "gap=0.01" in call_args
        assert "verbose=false" in call_args


# ---------------------------------------------------------------------------
# get_julia_version
# ---------------------------------------------------------------------------


class TestGetJuliaVersion:
    """Tests for get_julia_version()."""

    def test_returns_version_string(self, monkeypatch):
        mock_jl = MagicMock()
        mock_jl.seval.return_value = "1.10.4"
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        result = get_julia_version()
        assert result == "1.10.4"
        assert isinstance(result, str)

    def test_calls_seval_with_version(self, monkeypatch):
        mock_jl = MagicMock()
        mock_jl.seval.return_value = "1.10.0"
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        get_julia_version()

        mock_jl.seval.assert_called_with("VERSION")

    def test_returns_none_on_exception(self):
        with patch(
            "esfex.bridge.julia_setup.get_julia",
            side_effect=RuntimeError("no julia"),
        ):
            result = get_julia_version()
            assert result is None

    def test_returns_none_on_seval_exception(self, monkeypatch):
        mock_jl = MagicMock()
        mock_jl.seval.side_effect = Exception("seval failed")
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        result = get_julia_version()
        assert result is None

    def test_return_type_string_or_none(self, monkeypatch):
        mock_jl = MagicMock()
        mock_jl.seval.return_value = "1.9.3"
        monkeypatch.setattr(julia_setup_mod, "_julia_instance", mock_jl)

        result = get_julia_version()
        assert isinstance(result, (str, type(None)))


# ---------------------------------------------------------------------------
# precompile_esfex
# ---------------------------------------------------------------------------


class TestPrecompileEsfex:
    """Tests for precompile_esfex() — now a PackageCompiler-based sysimage
    build, so it returns an existing Path when the cached sysimage is fresh
    and skips the (slow) subprocess invocation."""

    def test_returns_existing_sysimage_when_fresh(self, monkeypatch, tmp_path):
        fake = tmp_path / "sysimage.so"
        fake.touch()
        monkeypatch.setattr(
            julia_setup_mod, "_find_sysimage", lambda: fake,
        )
        monkeypatch.setattr(
            julia_setup_mod, "_sysimage_is_stale", lambda p: False,
        )
        result = precompile_esfex(force=False)
        assert result == fake

    def test_returns_none_when_no_build_script(self, monkeypatch, tmp_path):
        monkeypatch.setattr(julia_setup_mod, "_find_sysimage", lambda: None)
        # Point get_julia_path at an empty dir so build_sysimage.jl is missing.
        monkeypatch.setattr(
            julia_setup_mod, "get_julia_path", lambda: tmp_path,
        )
        result = precompile_esfex(force=True)
        assert result is None
