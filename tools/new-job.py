import argparse
import datetime
import errno
import os
import shutil
import subprocess as sp
import sys

import config_utils as C

import utils as U

# cTOASTER configuration

if not U.read_ctoaster_config():
    sys.exit("cTOASTER not set up: run the setup-ctoaster script!")


# Command line arguments.

parser = argparse.ArgumentParser(description="Configure cTOASTER jobs")
parser.add_argument("job_name", nargs="?", help="Job name")
parser.add_argument("run_length", nargs="?", type=int, help="Run length")
parser.add_argument(
    "-O", "--overwrite", action="store_true", help="Overwrite existing job"
)
parser.add_argument("-b", "--base-config", help="Base configuration name")
parser.add_argument("-u", "--user-config", help="User configuration name")
parser.add_argument("-m", "--config-mods", help="Configuration mods filename")
parser.add_argument("-c", "--config", help="Full configuration name")
parser.add_argument("-r", "--restart", help="Restart name")
parser.add_argument(
    "--old-restart", action="store_true", help="Restart from old ctoaster job"
)
parser.add_argument("--t100", action="store_true", help='Use "T100" timestepping')
parser.add_argument("-t", "--test-job", help="Set up from test")
parser.add_argument(
    "-j", "--job-dir", help="Alternative job directory", default=U.ctoaster_jobs
)
parser.add_argument(
    "-v", "--model-version", help="Model version to use", default=U.ctoaster_version
)
parser.add_argument("-g", "--gui", action="store_true", help=argparse.SUPPRESS)

args = parser.parse_args()

# Validate the number of positional arguments based on whether test_job is specified
if (
    not args.test_job
    and not (args.job_name and args.run_length is not None)
    or args.test_job
    and (args.job_name or args.run_length is not None)
):
    parser.print_help()
    sys.exit()

# Assign values from args
job_name = args.job_name if not args.test_job else args.test_job
run_length = args.run_length
running_from_gui = args.gui
overwrite = args.overwrite
base_config = args.base_config
user_config = args.user_config
config_mods = args.config_mods
full_config = args.config
restart = args.restart
test_job = args.test_job
old_restart = args.old_restart
t100 = args.t100
job_dir_base = args.job_dir
model_version = args.model_version

# Check if the model version exists
if model_version not in U.available_versions():
    sys.exit(f'Model version "{model_version}" does not exist')


def error_exit(msg):
    if running_from_gui:
        sys.exit(f"ERR:{msg}")
    else:
        sys.exit(msg)


# If a specific model version is requested, set up a repository clone
# on the appropriate branch and run the configuration script at that
# version.

repo_version = "DEVELOPMENT"
if os.path.exists("repo-version"):
    with open("repo-version") as fp:
        repo_version = fp.readline().strip()
if model_version != repo_version:
    repodir = U.setup_version_repo(model_version)
    os.chdir(repodir)
    os.execv(
        sys.executable, [os.path.join(os.curdir, "tools", "new-job.py")] + sys.argv
    )


# Check configuration file options.

base_and_user_config = base_config and user_config
if not base_and_user_config and not full_config and not test_job:
    error_exit("Either base and user, full configuration or test must be specified")
if not base_and_user_config and config_mods:
    error_exit(
        "Configuration mods can only be specified if using base and user configuration"
    )

nset = 0
if base_and_user_config:
    nset += 1
if full_config:
    nset += 1
if test_job:
    nset += 1
if nset > 1:
    error_exit(
        "Only one of base and user, full configuration, or test may be specified"
    )


#

if test_job:
    test_dir = os.path.join(U.ctoaster_test, test_job)
    with open(os.path.join(test_dir, "test_info")) as fp:
        for line in fp:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip()
            if k == "restart_from":
                restart = v
            elif k == "run_length":
                run_length = int(v)
            elif k == "t100":
                t100 = v == "True"


# Check for existence of any restart job.

if restart:
    if old_restart:
        restart_path = os.path.join(os.path.expanduser("~/ctoaster_output"), restart)
    elif os.path.exists(restart):
        restart_path = restart
    else:
        restart_path = os.path.join(job_dir_base, restart, "output")
    if not os.path.exists(restart_path):
        error_msg = (
            f'Old ctoaster restart job "{restart}" does not exist'
            if old_restart
            else f'Restart job "{restart}" does not exist'
        )
        error_exit(error_msg)

# All set up.  Off we go...

if not running_from_gui:
    print(f'   Job name: {job_name} {" [TEST]" if test_job else ""}')
    if base_and_user_config:
        print(f"Base config: {base_config}")
        print(f"User config: {user_config}")
    if config_mods:
        print(f"Config mods: {config_mods}")
    if full_config:
        print(f"Full config: {full_config}")
    if not test_job:
        print(f" Run length: {run_length}")
    print(f"  Overwrite: {overwrite}")
    print(f"      Model: {model_version}")


# Read and parse configuration files.

if base_and_user_config:
    if not os.path.exists(base_config):
        base_config_dir = os.path.join(U.ctoaster_data, "base-configs")
        base_config_path = os.path.join(base_config_dir, base_config + ".config")
    else:
        base_config_dir = os.getcwd()
        base_config_path = base_config
    base = C.read_config(base_config_path, "Base configuration")
    if not os.path.exists(user_config):
        user_config_dir = os.path.join(U.ctoaster_data, "user-configs")
        user_config_path = os.path.join(user_config_dir, user_config)
    else:
        user_config_dir = os.getcwd()
        user_config_path = user_config
    user = C.read_config(user_config_path, "User configuration")
    configs = [base, user]
    if config_mods:
        mods = C.read_config(config_mods, "Configuration modifications")
        configs.append(mods)
elif full_config:
    if not os.path.exists(full_config):
        full_config_dir = os.path.join(U.ctoaster_data, "full-configs")
        full_config_path = os.path.join(full_config_dir, full_config + ".config")
    else:
        full_config_dir = os.getcwd()
        full_config_path = full_config
    full = C.read_config(full_config_path, "Full configuration")
    configs = [full]
else:
    # Test job -- read base_config, user_config and full_config files
    # as they exist.
    if os.path.exists(os.path.join(test_dir, "full_config")):
        full = C.read_config(
            os.path.join(test_dir, "full_config"), "Full configuration"
        )
        configs = [full]
    else:
        base = C.read_config(
            os.path.join(test_dir, "base_config"), "Base configuration"
        )
        user = C.read_config(
            os.path.join(test_dir, "user_config"), "User configuration"
        )
        configs = [base, user]


# Set up source and per-module input data directories.

srcdir = os.path.join(U.ctoaster_root, "src")
datadir = "data"
C.set_dirs(srcdir, datadir)


# Determine modules used in job.


def extract_mod_opts(c):
    return [x for x in c.keys() if x.startswith("ma_flag_")]


mod_opts = map(extract_mod_opts, configs)


def extract_mod_flags(c, os):
    return {k: c[k] for k in os}


mod_flags = map(extract_mod_flags, configs, mod_opts)
merged_mod_flags = C.merge_flags(mod_flags)
mod_flags = [k for k in merged_mod_flags.keys() if merged_mod_flags[k]]
modules = list(map(C.module_from_flagname, mod_flags))


# Set up job directory and per-module sub-directories.


def safe_mkdir(p):
    os.makedirs(
        p, exist_ok=True
    )  # Ensures directory is created without raising an error if it already exists


job_dir = os.path.join(job_dir_base, job_name)
if not running_from_gui:
    if overwrite:
        shutil.rmtree(job_dir, ignore_errors=True)
    try:
        safe_mkdir(job_dir)
    except OSError as e:
        error_exit("Can't create job directory: " + job_dir)
try:
    for m in modules:
        safe_mkdir(os.path.join(job_dir, "input", m))
        safe_mkdir(os.path.join(job_dir, "output", m))
        if restart:
            safe_mkdir(os.path.join(job_dir, "restart", m))
    safe_mkdir(os.path.join(job_dir, "input", "main"))
    safe_mkdir(os.path.join(job_dir, "output", "main"))
    if restart:
        safe_mkdir(os.path.join(job_dir, "restart", "main"))
except Exception as e:
    with open("/dev/tty", "w") as fp:
        print(e, file=fp)


# Write configuration information to job directory.

cfg_dir = os.path.join(job_dir, "config")
if not running_from_gui:
    # Check if cfg_dir exists and overwrite flag is True, then remove it
    if os.path.exists(cfg_dir) and overwrite:
        shutil.rmtree(cfg_dir)
    # Now, safely create the cfg_dir as it's either new or has been cleared
    os.makedirs(
        cfg_dir, exist_ok=True
    )  # Use exist_ok to avoid error if the directory was just deleted and recreated

    if not test_job:
        with open(os.path.join(cfg_dir, "config"), "w") as fp:
            if base_config:
                print(f"base_config_dir: {base_config_dir}", file=fp)
                print(f"base_config: {base_config}", file=fp)
            if user_config:
                print(f"user_config_dir: {user_config_dir}", file=fp)
                print(f"user_config: {user_config}", file=fp)
            if full_config:
                print(f"full_config_dir: {full_config_dir}", file=fp)
                print(f"full_config: {full_config}", file=fp)
            if config_mods:
                print(f"config_mods: {config_mods}", file=fp)
            print(f"config_date: {datetime.datetime.today()}", file=fp)
            print(f"run_length: {run_length}", file=fp)
            print(f"t100: {t100}", file=fp)
            if restart:
                print(f"restart: {restart}", file=fp)

if test_job:
    shutil.copyfile(
        os.path.join(test_dir, "test_info"), os.path.join(cfg_dir, "config")
    )
    if os.path.exists(os.path.join(test_dir, "base_config")):
        shutil.copyfile(
            os.path.join(test_dir, "base_config"), os.path.join(cfg_dir, "base_config")
        )
    if os.path.exists(os.path.join(test_dir, "user_config")):
        shutil.copyfile(
            os.path.join(test_dir, "user_config"), os.path.join(cfg_dir, "user_config")
        )
    if os.path.exists(os.path.join(test_dir, "full_config")):
        shutil.copyfile(
            os.path.join(test_dir, "full_config"), os.path.join(cfg_dir, "full_config")
        )
else:
    if base_config:
        shutil.copyfile(base_config_path, os.path.join(cfg_dir, "base_config"))
    if user_config:
        shutil.copyfile(user_config_path, os.path.join(cfg_dir, "user_config"))
    if full_config:
        shutil.copyfile(full_config_path, os.path.join(cfg_dir, "full_config"))
    if config_mods and not running_from_gui:
        shutil.copyfile(config_mods, os.path.join(cfg_dir, "config_mods"))


# Extract coordinate definitions from configuration.

defines = C.extract_defines(configs)
maxdeflen = max(map(len, defines.keys()))
deflines = [
    ("'" + d + "':").ljust(maxdeflen + 4) + str(defines[d]) for d in defines.keys()
]
deflines[0] = "coordvars = { " + deflines[0]
for i in range(1, len(deflines)):
    deflines[i] = "              " + deflines[i]
for i in range(len(deflines) - 1):
    deflines[i] += ","
deflines[-1] += " }"


# Set up timestepping and restart options: this is only done if we
# have a base+user configuration (i.e. the normal case); some test
# configurations already include timestepping options.

if len(configs) > 1:
    tsopts = C.timestepping_options(
        run_length, defines, t100=t100, quiet=running_from_gui
    )
    rstopts = C.restart_options(restart)
    configs = [configs[0], tsopts, rstopts] + configs[1:]


# Create model version file for build.

with open(os.path.join(cfg_dir, "model-version"), "w") as fp:
    if model_version == "DEVELOPMENT":
        try:
            result = sp.run(
                ["git", "describe", "--tags", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            )
            rev = result.stdout.strip()
            print(f"DEVELOPMENT:{rev}", file=fp)
        except:
            print("DEVELOPMENT:UNKNOWN", file=fp)
    else:
        print(model_version, file=fp)


# Create "go" script for job.

shutil.copy(os.path.join(U.ctoaster_root, "tools", "go"), job_dir)
shutil.copy(os.path.join(U.ctoaster_root, "tools", "go.bat"), job_dir)


# Set up per-module extra data files (these are files that don't
# appear in any configuration information...).

extra_data_files = {}
if "embm" in modules and "ents" in modules:
    extra_data_files["embm"] = [
        "inv_linterp_matrix.dat",
        "NCEP_airt_monthly.dat",
        "NCEP_pptn_monthly.dat",
        "NCEP_RH_monthly.dat",
        "atm_albedo_monthly.dat",
        "uvic_windx.silo",
        "uvic_windy.silo",
        "monthly_windspd.silo",
    ]
if "ents" in modules:
    extra_data_files["ents"] = ["ents_config.par", "sealevel_config.par"]

if "sedgem" in modules:
    extra_data_files["sedgem"] = ["lookup_calcite_4.dat", "lookup_opal_5.dat"]


# Construct namelists and copy data files.

configs.append(C.make_coordinates(defines))
for m in modules + ["main", "gem"]:
    minfo = C.lookup_module(m)
    if minfo["flag_name"] == "NONE":
        nmlin = os.path.join(srcdir, m + "-defaults.nml")
    else:
        nmlin = os.path.join(srcdir, m, m + "-defaults.nml")
    nmlout = os.path.join(job_dir, "data_" + minfo["nml_file"])
    with open(nmlin) as fp:
        nml = C.Namelist(fp)
        nml.merge(minfo["prefix"], configs)
        with open(nmlout, "w") as ofp:
            nml.write(ofp)
        C.copy_data_files(
            m, nml, os.path.join(job_dir, "input", m), extra_data_files.get(m)
        )
        if restart:
            C.copy_restart_files(
                m, nml, os.path.join(job_dir, "restart", m), restart_path
            )


# Extra data files for main program.

jobmaindatadir = os.path.join(job_dir, "input", "main")
srcmaindatadir = os.path.join(U.ctoaster_root, "data", "main")
for s in ["atm", "ocn", "sed"]:
    shutil.copy(os.path.join(srcmaindatadir, "tracer_define." + s), jobmaindatadir)

if running_from_gui:
    print("OK")

