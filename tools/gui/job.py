import datetime
import glob
import os  # Grouped imports as requested
import os.path
import shutil
import subprocess as sp
import sys
import time
import tkinter as tk
import tkinter.messagebox as tkMB
from typing import Dict, Optional, Tuple

import utils as U

# ----------------------------------------------------------------------
#
#  UTILITIES
#


def read_status_file(jd: str) -> list:
    """
    Attempts to read the status file for a job, handling potential issues on Windows where
    the file might be locked by another process.

    :param jd: The job directory containing the 'status' file.
    :return: A list containing the status information or None if the file could not be read.
    """
    status = None
    safety = 0
    while not status and safety < 1000:
        try:
            if safety != 0:
                time.sleep(0.001)
            safety += 1
            with open(os.path.join(jd, "status")) as fp:
                status = fp.readline().strip().split()
        except IOError:
            pass  # Consider logging the error in a real application
    if safety == 1000:
        print(
            "BORKED!"
        )  # Consider raising an exception or logging for a real application
    return status


def job_status(jd: str) -> str:
    """
    Determines the current status of a job given the path to its job directory.

    :param jd: Path to the job directory.
    :return: The job status as a string, or None if the status cannot be determined.
    """
    # Check if the job directory exists
    if not os.path.exists(jd):
        return None

    # Check if the job has not been configured (no 'data_genie' directory)
    if not os.path.exists(os.path.join(jd, "data_genie")):
        return "UNCONFIGURED"

    # Check if the job is configured but not yet run ('status' file doesn't exist)
    if not os.path.exists(os.path.join(jd, "status")):
        return "RUNNABLE"

    # Read and return the first word from the status file as the job status
    status = read_status_file(jd)
    if status:
        return status[0]
    else:
        return "ERROR"  # Consider adding a status to handle read errors or empty status files


status_images: Dict[str, tk.PhotoImage] = (
    {}
)  # Define the global variable if not already defined


def job_status_img(s: str) -> tk.PhotoImage:
    """
    Retrieves or creates an icon image for a given job status.

    :param s: The job status as a string.
    :return: A tkinter.PhotoImage object representing the status icon.
    """
    global status_images  # Explicit declaration to modify the global variable within this function
    if s not in status_images:
        p = os.path.join(U.ctoaster_root, "tools", "images", f"status-{s}.gif")
        status_images[s] = tk.PhotoImage(file=p)
    return status_images[s]


# ----------------------------------------------------------------------
#
#  MAIN JOB CLASS
#

# Class to record information about individual jobs.


class Job:
    def __init__(self, jobdir: Optional[str] = None, folder=None) -> None:
        """
        Initialize a new Job instance.

        :param jobdir: The full path to the job directory, or None for a new job.
        :param folder: The folder object containing base_path attribute for base job directory.
        """
        self.base_config: Optional[str] = None
        self.user_config: Optional[str] = None
        self.full_config: Optional[str] = None
        self.mods: str = ""
        self.runlen: Optional[int] = None
        self.t100: Optional[bool] = None
        self.restart: Optional[str] = None
        self.segments: list = []

        self.jobid: Optional[str] = None
        self.base_jobdir: Optional[str] = None
        self.jobdir: Optional[str] = None
        self.status: Optional[str] = None

        if jobdir:
            assert folder is not None, "Folder must be provided if jobdir is specified."
            self.base_jobdir = folder.base_path
            self.jobid = os.path.relpath(jobdir, folder.base_path)
            self.jobdir = jobdir
            self.set_status()  # Make sure this method is properly defined elsewhere
            try:
                self._read_job_config()
            except Exception as e:
                pass

    def _read_job_config(self) -> None:
        """
        Reads the job's configuration from disk. Sets various attributes based on the config.
        """
        config_path = os.path.join(self.jobdir, "config", "config")
        modfile_path = os.path.join(self.jobdir, "config", "config_mods")

        if os.path.exists(config_path):
            with open(config_path) as fp:
                for line in fp:
                    k, _, v = line.strip().partition(":")
                    setattr(self, k, self._parse_config_value(k, v))

        if os.path.exists(modfile_path):
            with open(modfile_path) as fp:
                self.mods = fp.read()

        self.read_segments()  # Ensure this method is defined to read segments if necessary

    def _parse_config_value(self, key: str, value: str):
        """
        Parses a configuration value from string to the correct type based on the key.

        :param key: The configuration key.
        :param value: The string representation of the value.
        :return: The parsed value in its correct type.
        """
        if key == "t100":
            return value.lower() == "true"
        elif key == "run_length":
            return None if value == "?" else int(value)
        return value  # For other keys, return the value as-is

    def read_segments(self) -> None:
        """
        Reads run segment limits information for a job from the 'seglist' file.
        Each line in the 'seglist' file represents a run segment with start and end steps.
        """
        self.segments = []
        cfgdir = os.path.join(self.jobdir, "config")
        segfile = os.path.join(cfgdir, "seglist")
        if os.path.exists(segfile):
            with open(segfile) as fp:
                for line in fp:
                    parts = line.split()
                    if (
                        len(parts) >= 3
                    ):  # Ensure the line has enough parts to avoid IndexError
                        start_step, end_step = int(parts[1]), int(parts[2])
                        self.segments.append((start_step, end_step))

    def jobdir_str(self) -> str:
        """
        Returns a string representation of the job directory.
        """
        return self.jobdir if self.jobdir else "n/a"

    def status_str(self) -> str:
        """
        Returns a string representation of the job's status.
        """
        return self.status if self.status else "n/a"

    def runlen_str(self) -> str:
        """
        Returns a string representation of the job's run length.
        """
        return str(self.runlen) if self.runlen is not None else "n/a"

    def t100_str(self) -> str:
        """
        Returns a string representation of whether the job has t100 enabled.
        """
        return str(self.t100) if self.t100 is not None else "n/a"

    def config_type(self) -> str:
        """
        Determines the configuration type for the job based on available configurations.
        """
        return "full" if self.full_config else "base+user"

    # Make a printable representation of a job, mostly for debugging.

    def __str__(self) -> str:
        """
        Returns a string representation of the Job instance, including
        all relevant attributes.
        """
        return (
            f"{{ jobid:{self.jobid} "
            f"jobdir:{self.base_jobdir} "
            f"dir:{self.jobdir} "
            f"base_config:{self.base_config} "
            f"user_config:{self.user_config} "
            f"full_config:{self.full_config} "
            f"restart:{self.restart} "
            f"mods:{self.mods} "
            f"runlen:{self.runlen} "
            f"t100:{self.t100} "
            f"status:{self.status} }}"
        )

    def write_config(self) -> None:
        """
        Writes the job's configuration information, handling the creation of new run segments for paused or completed jobs.
        """
        self.set_status()  # Ensure this method properly updates `self.status`

        if self.status in ("PAUSED", "COMPLETE"):
            cfgdir = os.path.join(self.jobdir, "config")
            segdir = os.path.join(cfgdir, "segments")
            segfile = os.path.join(cfgdir, "seglist")
            os.makedirs(segdir, exist_ok=True)

            save_seg, startk, endk = 1, 1, int(self.status_params()[1])
            if os.path.exists(segfile):
                with open(segfile) as fp:
                    last_line = fp.readlines()[-1].split()
                    save_seg, startk = int(last_line[0]) + 1, int(last_line[2])

            if startk != endk:
                with open(segfile, "a") as fp:
                    fp.write(f"{save_seg} {startk} {endk}\n")
                self.segments.append((startk, endk))
                new_segdir = os.path.join(segdir, str(save_seg))
                os.makedirs(new_segdir, exist_ok=True)
                for f in (
                    "config",
                    "base_config",
                    "user_config",
                    "full_config",
                    "config_mods",
                ):
                    src = os.path.join(cfgdir, f)
                    if os.path.exists(src):
                        shutil.copy(src, new_segdir)

        try:
            with open(os.path.join(self.jobdir, "config", "config"), "w") as fp:
                for attr in ("base_config", "user_config", "full_config"):
                    if getattr(self, attr):
                        fp.write(
                            f"{attr}_dir: {os.path.join(U.ctoaster_data, attr+'s')}\n"
                        )
                        fp.write(f"{attr}: {getattr(self, attr)}\n")
                if self.restart:
                    fp.write(f"restart: {self.restart}\n")

                modfile = os.path.join(self.jobdir, "config", "config_mods")
                if self.mods:
                    with open(modfile, "w") as mfp:
                        mfp.write(self.mods)
                elif os.path.exists(modfile):
                    os.remove(modfile)

                today = datetime.datetime.today().strftime("%Y-%m-%d %H:%M:%S")
                fp.write(f"config_date: {today}\n")
                fp.write(f"run_length: {self.runlen}\n")
                fp.write(f"t100: {self.t100}\n")
        except Exception as e:
            print(f"Exception 2: {e}")

    def gen_namelists(self) -> None:
        """
        Generates cTOASTER namelists by running the new-job script.
        """
        new_job_script = os.path.join(U.ctoaster_root, "tools", "new-job.py")

        # Create command line for running new-job.
        cmd = [sys.executable, new_job_script, "--gui"]
        if self.base_config:
            cmd.extend(["-b", self.base_config])
        if self.user_config:
            cmd.extend(["-u", self.user_config])
        if self.full_config:
            cmd.extend(["-c", self.full_config])
        if self.restart:
            cmd.extend(["-r", self.restart])
        if self.mods:
            modfile = os.path.join(self.jobdir, "config", "config_mods")
            with open(modfile, "w") as fp:
                fp.write(self.mods)
            cmd.extend(["-m", modfile])
        cmd.extend(["-j", self.base_jobdir])
        if self.t100:
            cmd.append("--t100")
        cmd.extend([self.jobid, str(self.runlen)])

        try:
            with open(os.devnull, "w") as sink:
                res = sp.check_output(cmd, stderr=sink, text=True).strip()
        except sp.CalledProcessError as e:
            res = f"ERR:Failed to run new-job script with error {e}"
        except Exception as e:
            res = f"ERR:Unexpected error {e}"

        if not res.startswith("OK"):
            tkMB.showerror("Error", res[4:])

    def check_output_files(self) -> Dict[str, str]:
        """
        Checks for output files that could be plotted, focusing on BIOGEM time series files.

        :return: A dictionary mapping file names to their full paths.
        """
        output_dir = os.path.join(self.jobdir, "output")
        biogem_series_pattern = os.path.join(
            output_dir, "biogem", "biogem_series_*.res"
        )

        # Prepare a dictionary to hold the results.
        results: Dict[str, str] = {}

        # Search for matching files and populate the dictionary.
        for file_path in glob.glob(biogem_series_pattern):
            results[os.path.basename(file_path)] = file_path

        return results

    def set_status(self, runlen_increased: bool = False) -> None:
        """
        Determines the current status of a job, adjusting it if necessary based on changes in run length.

        :param runlen_increased: Indicates whether the run length of the job has been increased.
        """
        # Determine the job's status using the job_status function.
        self.status = job_status(self.jobdir)

        # If the job's run length has been increased and its status is complete,
        # it should be marked as paused.
        if runlen_increased and self.status == "COMPLETE":
            status_params = self.status_params()
            if status_params:
                sout = f"PAUSED {' '.join(status_params[1:])}"
                status_file = os.path.join(self.jobdir, "status")
                with open(status_file, "w") as fp:
                    fp.write(sout + "\n")
                self.status = "PAUSED"

    def status_img(self) -> str:
        """
        Returns the icon image filename for the job's current status.

        :return: A string representing the filename of the icon image for the current status.
        """
        return job_status_img(self.status)

    def pct_done(self) -> Optional[float]:
        """
        Calculates the percentage of completion for running or paused jobs based on the cTOASTER status file.

        :return: A float representing the percentage of completion, or None if not applicable.
        """
        status_file = os.path.join(self.jobdir, "status")
        if not os.path.exists(status_file):
            return None

        ss = read_status_file(self.jobdir)
        if not ss or ss[0] not in ("RUNNING", "PAUSED"):
            return None

        try:
            current_step = float(ss[1])
            total_steps = float(ss[2])
            return 100 * current_step / total_steps
        except (IndexError, ValueError, ZeroDivisionError) as e:
            # Handle cases where the status file is malformed or total_steps is zero.
            print(f"Error calculating percentage done: {e}")
            return None

    def status_params(self):
        # Return all status parameters written in cTOASTER status file.
        # For running, paused and complete jobs, the current and total
        # timesteps as well as the "cTOASTER clock" value are written
        # into the status file to assist in restarting the model after
        # pauses.
        if not os.path.exists(os.path.join(self.jobdir, "status")):
            return None
        return read_status_file(self.jobdir)

    def segment_strs(self) -> Tuple[str, ...]:
        """
        Generates string representations of the job run segments for
        selection in the setup panel GUI.

        :return: A tuple of strings representing the job's run segments.
        """
        # Ensure the latest segment information is loaded.
        self.read_segments()

        if not self.segments:
            # Default case with a single segment.
            return ("1: 1-END",)

        # Generate strings for each segment in the form "<id>: <start>-<end>"
        res = [
            f"{i + 1}: {start}-{end}" for i, (start, end) in enumerate(self.segments)
        ]

        # Add a final segment representing the next step after the last known segment.
        final_step = (
            self.segments[-1][1] + 1
        )  # Assumes segments are sorted and non-empty
        res.append(f"{len(self.segments) + 1}: {final_step}-END")

        # Reverse the list to match the original behavior and convert to a tuple.
        return tuple(reversed(res))

    def read_segment(self, iseg: int) -> Optional[Dict[str, str]]:
        """
        Reads the configuration information for a specified run segment.

        :param iseg: The segment number to read configuration for.
        :return: A dictionary with configuration keys and values, or None if the segment directory does not exist.
        """
        segdir = os.path.join(self.jobdir, "config", "segments", str(iseg))
        if not os.path.exists(segdir):
            return None

        config = {}
        config_path = os.path.join(segdir, "config")
        if os.path.exists(config_path):
            with open(config_path) as fp:
                for line in fp:
                    key, _, value = line.partition(":")
                    config[key.strip()] = self._parse_config_value(
                        key.strip(), value.strip()
                    )

        modfile_path = os.path.join(segdir, "config_mods")
        if os.path.exists(modfile_path):
            with open(modfile_path) as fp:
                config["mods"] = fp.read().strip()

        return config

    def _parse_config_value(self, key: str, value: str) -> str:
        """
        Parses a configuration value from a string based on the key.

        :param key: The configuration key.
        :param value: The string value to be parsed.
        :return: The parsed value.
        """
        if key == "t100":
            return value == "True"
        elif key == "run_length":
            return None if value == "?" else int(value)
        return value
