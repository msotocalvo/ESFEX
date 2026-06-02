"""
Build a Julia system image for faster ESFEX startup.

Usage:
    julia --project=. build_sysimage.jl

This creates a precompiled sysimage that includes JuMP, HiGHS, and the ESFEX
module with a realistic optimization workload pre-compiled into native code.

Expected speedup: Julia startup from ~30s to <3s.

Requirements:
    - PackageCompiler.jl must be installed: `] add PackageCompiler`
"""

using Pkg

# Activate the ESFEX project
project_dir = @__DIR__
Pkg.activate(project_dir)
Pkg.instantiate()

# Install PackageCompiler if not present
if !haskey(Pkg.project().dependencies, "PackageCompiler")
    @info "Installing PackageCompiler..."
    Pkg.add("PackageCompiler")
end

using PackageCompiler

# Define packages to include in the sysimage
packages = [
    :JuMP,
    :HiGHS,
    :Graphs,
    :LinearAlgebra,
    :SparseArrays,
    :Statistics,
]

# Output path for sysimage (platform-specific extension)
sysimage_path = joinpath(project_dir, "ESFEX.so")
if Sys.iswindows()
    sysimage_path = joinpath(project_dir, "ESFEX.dll")
elseif Sys.isapple()
    sysimage_path = joinpath(project_dir, "ESFEX.dylib")
end

# Workload script that exercises the full ESFEX optimization pipeline
workload_file = joinpath(project_dir, "precompile_workload.jl")
if !isfile(workload_file)
    error("Precompile workload not found: $workload_file")
end

@info "Building sysimage with realistic workload..." packages sysimage_path

# Build the sysimage using the workload script
# precompile_execution_file actually runs the script and captures all
# native code generated during execution into the sysimage.
create_sysimage(
    packages,
    sysimage_path = sysimage_path,
    precompile_execution_file = workload_file,
    cpu_target = "generic",  # For portability
)

@info "Sysimage built successfully!" sysimage_path

# Print usage instructions
println("""

================================================================================
ESFEX Sysimage Built Successfully!
================================================================================

The sysimage has been created at:
  $sysimage_path

It will be detected automatically by ESFEX on subsequent runs.
You can also set the environment variable explicitly:

  export PYTHON_JULIACALL_SYSIMAGE="$sysimage_path"

Expected speedup: Julia startup from ~30s to <3s
To rebuild after code changes: esfex precompile --force
================================================================================
""")
