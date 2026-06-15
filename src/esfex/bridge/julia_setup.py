"""
Julia setup and initialization for ESFEX.

Handles Julia environment initialization, package loading,
module compilation, and sysimage management for the ESFEX
optimization models.
"""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Global Julia instance
_julia_instance = None
_esfex_module = None


def get_julia_path() -> Path:
    """Get the path to the Julia source directory."""
    return Path(__file__).parent.parent / "julia"


# ── Sysimage utilities ────────────────────────────────────────────


def _find_sysimage() -> Optional[Path]:
    """Find the ESFEX sysimage if it exists.

    Checks the Julia project directory for ESFEX.so / .dll / .dylib.
    """
    julia_dir = get_julia_path()
    for ext in (".so", ".dll", ".dylib"):
        p = julia_dir / f"ESFEX{ext}"
        if p.exists():
            return p
    return None


def _sysimage_is_stale(sysimage: Path) -> bool:
    """Check if any .jl source file is newer than the sysimage."""
    if not sysimage.exists():
        return True
    mtime = sysimage.stat().st_mtime
    src_dir = get_julia_path() / "src"
    if not src_dir.exists():
        return False
    for jl_file in src_dir.glob("*.jl"):
        if jl_file.stat().st_mtime > mtime:
            return True
    return False


# ── Julia initialization ──────────────────────────────────────────


def initialize_julia(
    threads: int = 4,
    compile: bool = True,
    verbose: bool = False,
) -> Any:
    """
    Initialize the Julia runtime and load the ESFEX module.

    Automatically detects and uses a sysimage if one exists.
    Subsequent calls return the cached Julia instance.

    Args:
        threads: Number of threads for Julia
        compile: Whether to precompile the ESFEX module
        verbose: Enable verbose output

    Returns:
        The juliacall Main module with ESFEX loaded

    Raises:
        ImportError: If juliacall is not installed
        RuntimeError: If Julia initialization fails
    """
    global _julia_instance, _esfex_module

    if _julia_instance is not None:
        return _julia_instance

    # Set Julia thread count
    os.environ.setdefault("JULIA_NUM_THREADS", str(threads))

    # Auto-detect sysimage BEFORE importing juliacall
    # (PYTHON_JULIACALL_SYSIMAGE must be set before first import)
    sysimage = _find_sysimage()
    if sysimage:
        if _sysimage_is_stale(sysimage):
            logger.info(
                "Julia sysimage is stale (run `esfex precompile` to rebuild)"
            )
            # Still use it — dependency precompilation is still valid
            os.environ.setdefault("PYTHON_JULIACALL_SYSIMAGE", str(sysimage))
        else:
            os.environ.setdefault("PYTHON_JULIACALL_SYSIMAGE", str(sysimage))
            logger.info(f"Using Julia sysimage: {sysimage}")
    else:
        logger.debug(
            "No Julia sysimage found. "
            "Run `esfex precompile` for faster startup."
        )

    try:
        from juliacall import Main as jl
    except ImportError:
        raise ImportError(
            "juliacall is required for Julia integration. "
            "Install with: pip install juliacall"
        )

    logger.info("Initializing Julia runtime...")

    # Get Julia source path
    julia_path = get_julia_path()
    project_toml = julia_path / "Project.toml"
    src_path = julia_path / "src"

    if not project_toml.exists():
        raise RuntimeError(f"Julia Project.toml not found at {project_toml}")

    try:
        # Activate the Julia project
        # Use forward slashes so Julia doesn't interpret backslashes as escapes
        jl_project = str(julia_path).replace("\\", "/")
        jl_src = str(src_path / "ESFEX.jl").replace("\\", "/")
        jl.seval(f'using Pkg; Pkg.activate("{jl_project}")')

        # Try to load the ESFEX module directly first
        # Only run instantiate if dependencies are missing
        logger.info("Loading ESFEX Julia module...")
        try:
            jl.seval(f'include("{jl_src}")')
            jl.seval("using .ESFEX")
        except Exception as load_error:
            err_str = str(load_error)
            if "invalid redefinition of constant" in err_str:
                # Module already loaded in this Julia session — just use it
                logger.info("ESFEX module already loaded, reusing existing.")
                jl.seval("using .ESFEX")
            elif "LoadError" in err_str or "ArgumentError" in err_str:
                # Dependencies might be missing, try to instantiate
                logger.info("Installing missing Julia dependencies...")
                try:
                    jl.seval("Pkg.instantiate()")
                except Exception as pkg_err:
                    logger.warning(
                        "Pkg.instantiate() failed: %s. "
                        "Deleting Manifest.toml and retrying...", pkg_err,
                    )
                    manifest = julia_path / "Manifest.toml"
                    if manifest.exists():
                        manifest.unlink()
                    jl.seval("Pkg.resolve(); Pkg.instantiate()")
                # Retry loading
                jl.seval(f'include("{jl_src}")')
                jl.seval("using .ESFEX")
            else:
                raise

        # Pre-import optional solvers so their MOI methods live in the current
        # world age (avoids Julia 1.12 world-age errors when ESFEX lazy-loads
        # them via @eval import inside a function).
        for _pkg in ("Clarabel", "Ipopt", "SCS", "GLPK", "Cbc",
                     "SCIP", "Gurobi", "CPLEX", "Xpress"):
            try:
                jl.seval(f"import {_pkg}")
                logger.debug("Pre-imported Julia solver: %s", _pkg)
            except Exception:
                logger.debug("Optional solver %s not available", _pkg)

        _julia_instance = jl
        _esfex_module = jl.ESFEX

        logger.info("Julia initialization complete")

        return jl

    except Exception as e:
        raise RuntimeError(f"Julia initialization failed: {e}")


def get_julia() -> Any:
    """
    Get the Julia runtime instance.

    Initializes Julia if not already initialized.

    Returns:
        The juliacall Main module with ESFEX loaded
    """
    if _julia_instance is None:
        return initialize_julia()
    return _julia_instance


_included_overlays: set[str] = set()


def include_plugin_overlays(paths) -> None:
    """``include()`` plugin Julia overlay files into the running session (once).

    Overlays run after ``ESFEX`` is loaded, so they can call
    ``ESFEX.register_constraint_hook!(...)`` to add custom constraint types.
    Errors in one overlay are logged and skipped (they never abort a run).
    """
    jl = get_julia()
    for path in paths:
        key = str(path).replace("\\", "/")
        if key in _included_overlays:
            continue
        try:
            jl.seval(f'include("{key}")')
            _included_overlays.add(key)
            logger.info("Included plugin Julia overlay: %s", key)
        except Exception:
            logger.exception("Failed to include plugin Julia overlay: %s", key)


def get_esfex_module() -> Any:
    """
    Get the ESFEX Julia module.

    Returns:
        The ESFEX Julia module

    Raises:
        RuntimeError: If Julia is not initialized
    """
    if _esfex_module is None:
        get_julia()  # Initialize if needed
    if _esfex_module is None:
        raise RuntimeError("ESFEX Julia module not loaded")
    return _esfex_module


def create_julia_optimizer(
    solver: str = "highs",
    threads: int = 4,
    time_limit: float = 3600.0,
    gap: float = 0.01,
    verbose: bool = False,
) -> Any:
    """
    Create a configured JuMP optimizer in Julia.

    Uses ESFEX.create_optimizer() which handles all solver-specific
    parameter mapping and on-demand loading of solver packages.

    Args:
        solver: Solver name ('highs', 'gurobi', 'cplex', 'scip', 'xpress', 'cbc', 'glpk')
        threads: Number of threads
        time_limit: Time limit in seconds
        gap: MIP optimality gap
        verbose: Enable verbose output

    Returns:
        Julia optimizer object
    """
    jl = get_julia()
    solver = solver.lower()

    return jl.seval(f"""
    ESFEX.create_optimizer(
        solver_name="{solver}",
        threads={threads},
        time_limit={time_limit},
        gap={gap},
        verbose={str(verbose).lower()}
    )
    """)


def check_julia_available() -> bool:
    """
    Check if Julia is available.

    Returns:
        True if Julia can be initialized, False otherwise
    """
    try:
        from juliacall import Main as jl
        return True
    except ImportError:
        return False
    except Exception:
        return False


def get_julia_version() -> Optional[str]:
    """
    Get the Julia version string.

    Returns:
        Julia version string or None if not available
    """
    try:
        jl = get_julia()
        return str(jl.seval("VERSION"))
    except Exception:
        return None


# ── Sysimage build ────────────────────────────────────────────────


def precompile_esfex(force: bool = False) -> Optional[Path]:
    """Build the Julia sysimage for faster startup.

    Uses PackageCompiler.jl to create a native sysimage containing
    JuMP, HiGHS, and the ESFEX optimization pipeline pre-compiled.

    Args:
        force: Rebuild even if a fresh sysimage already exists.

    Returns:
        Path to the built sysimage, or None on failure.
    """
    julia_dir = get_julia_path()
    sysimage = _find_sysimage()

    if sysimage and not force and not _sysimage_is_stale(sysimage):
        logger.info("Sysimage is up to date: %s", sysimage)
        return sysimage

    build_script = julia_dir / "build_sysimage.jl"
    if not build_script.exists():
        logger.error("build_sysimage.jl not found at %s", build_script)
        return None

    # Find Julia executable
    julia_exe = shutil.which("julia")
    if julia_exe is None:
        logger.error("Julia executable not found in PATH")
        return None

    logger.info("Building Julia sysimage (this may take 5-10 minutes)...")

    try:
        result = subprocess.run(
            [julia_exe, f"--project={julia_dir}", str(build_script)],
            capture_output=False,
            text=True,
        )
        if result.returncode != 0:
            logger.error("Sysimage build failed (exit code %d)", result.returncode)
            return None
    except Exception as e:
        logger.error("Sysimage build failed: %s", e)
        return None

    built = _find_sysimage()
    if built:
        logger.info("Sysimage built successfully: %s", built)
    return built
