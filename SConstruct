from __future__ import print_function
import glob
import os
import platform as P
import sys

# Assuming version.py, utils.py, and other required modules are in appropriate paths.

# Check for version.py to set up environment.
if os.path.exists("version.py"):
    exec(open("version.py").read(), globals())
else:
    srcdir = "src"
    build_type = "debug" if "debug" in ARGUMENTS else "normal"
    sys.path.append("tools")

import utils as U

# Ensure cTOASTER is set up correctly.
if not U.read_ctoaster_config():
    sys.exit("cTOASTER not set up: run the setup.py script!")

# Load platform configuration.
platform = U.discover_platform()
print(f'Using platform "{platform}"')
exec(open(os.path.join(U.ctoaster_root, "platforms", platform)).read(), globals())

# NetCDF paths - assuming netcdf dictionary is defined somewhere.
netcdfinc, netcdflib = netcdf["base"]

# Define modules and utils.
modules = Split(
    """atchem biogem embm ents gemlite goldstein goldsteinseaice rokgem sedgem"""
)
utils = Split("common utils wrappers")
subdirs = modules + utils

# F90 module search paths.
modpath = [os.path.join("#/build", d) for d in subdirs]

# Version and compilation flags.
rev = ARGUMENTS.get("rev", "UNKNOWN")
defs = [f"-DREV={rev}"]

# Environment setup.
envcopy = os.environ.copy()
baselinkflags = f90.get("baselinkflags", [])
# Default (non-debug) builds use the platform's optimised "ship" flags (-O3
# etc.). Previously build_type "normal" had no entry in the platform dict, so
# f90.get("normal", []) returned [] and the model compiled at -O0 — ~3-5x
# slower. Falling back to "ship" keeps the optimisation for production builds
# (the runner exe) while an explicit "debug" build still uses the debug flags.
extraf90flags = f90.get(build_type) or f90.get("ship", [])
extralinkflags = f90.get(f"{build_type}_link", []) or f90.get("ship_link", [])
extraf90libpaths = [f90["libpath"]] if "libpath" in f90 else []
extraf90libs = [f90["libs"]] if "libs" in f90 else []

target_vs_arch = os.environ.get("TARGET_VS_ARCH", "linux")

env = Environment(
    ENV=envcopy,
    TOOLS=["default", f90["compiler"]],
    HOST_ARCH=target_vs_arch,
    F90FLAGS=f90["baseflags"] + extraf90flags + defs,
    LINKFLAGS=baselinkflags + extralinkflags,
    F90PATH=[netcdfinc] + modpath,
    FORTRANMODDIRPREFIX=f90["module_dir"],
    FORTRANMODDIR="${TARGET.dir}",
    LIBPATH=[netcdflib] + extraf90libpaths,
    LIBS=netcdf["libs"] + extraf90libs,
)

if "ld_library_path" in f90:
    env["ENV"]["LD_LIBRARY_PATH"] = f90["ld_library_path"]

# Build configuration.
Export("env", "subdirs", "build_type")
SConscript(os.path.join(srcdir, "SConscript"), variant_dir="#build", duplicate=0)
Install(".", "build/carrotcake.exe")
