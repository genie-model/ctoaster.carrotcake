import asyncio
import datetime
import logging
import os
import shutil
import subprocess as sp
import sys
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.background import BackgroundTasks
from starlette.responses import StreamingResponse

from utils import read_ctoaster_config

# Initialize the configuration
read_ctoaster_config()

from utils import ctoaster_data, ctoaster_jobs, ctoaster_root, ctoaster_version

app = FastAPI()

# CORS configuration
origins = ["http://localhost:5001", "*"]  # React development server

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@app.get("/jobs")
def list_jobs():
    try:
        if ctoaster_jobs is None:
            raise ValueError("ctoaster_jobs is not defined")

        job_list = os.listdir(ctoaster_jobs)
        jobs = []
        for job in job_list:
            job_path = os.path.join(ctoaster_jobs, job)
            if os.path.isdir(job_path):
                jobs.append({"name": job, "path": job_path})
        return {"jobs": jobs}
    except Exception as e:
        return {"error": str(e)}


# Global variable to store the currently selected job name
selected_job_name = None

# Global variable to store the currently selected job name
selected_job_name = None


@app.get("/job/{job_name}")
def get_job_details(job_name: str):
    global selected_job_name
    selected_job_name = job_name  # Store the selected job name
    try:
        if ctoaster_jobs is None:
            raise ValueError("ctoaster_jobs is not defined")

        job_path = os.path.join(ctoaster_jobs, job_name)

        if not os.path.isdir(job_path):
            logger.info(f"Job not found: {job_path}")
            return {"error": "Job not found"}

        # Determine job status
        status = "UNCONFIGURED"
        if os.path.exists(os.path.join(job_path, "data_genie")):
            status = "RUNNABLE"
            if os.path.exists(os.path.join(job_path, "status")):
                with open(os.path.join(job_path, "status")) as f:
                    status_line = f.readline().strip()
                    status = status_line.split()[0] if status_line else "ERROR"

        # Determine run length and T100 from the config file
        run_length = "n/a"
        t100 = "n/a"
        config_path = os.path.join(job_path, "config", "config")
        if os.path.exists(config_path):
            with open(config_path) as f:
                for line in f:
                    if line.startswith("run_length:"):
                        run_length = line.split(":")[1].strip()
                    if line.startswith("t100:"):
                        t100 = line.split(":")[1].strip().lower() == "true"

        job_details = {
            "name": job_name,
            "path": job_path,
            "status": status,
            "run_length": run_length,
            "t100": "true" if t100 else "false",
        }

        logger.info(f"Job details retrieved: {job_details}")

        return {"job": job_details}
    except Exception as e:
        logger.error(f"Error retrieving job details: {str(e)}")
        return {"error": str(e)}


@app.delete("/delete-job")
def delete_job():
    global selected_job_name
    try:
        if not selected_job_name:
            raise HTTPException(status_code=400, detail="No job selected")

        if ctoaster_jobs is None:
            raise ValueError("ctoaster_jobs is not defined")

        job_path = os.path.join(ctoaster_jobs, selected_job_name)

        if not os.path.isdir(job_path):
            logger.info(f"Job not found: {job_path}")
            return {"error": "Job not found"}

        # Delete the job directory
        shutil.rmtree(job_path)

        local_job_name = selected_job_name

        # Clear the selected job name
        selected_job_name = None

        logger.info(f"Job deleted: {job_path}")
        return {"message": f"Job '{local_job_name}' deleted successfully"}

    except Exception as e:
        logger.error(f"Error deleting job: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting job: {str(e)}")
    # if not selected_job_name:
    #     raise HTTPException(status_code=400, detail="No job selected")

    # from utils import ctoaster_jobs

    # if ctoaster_jobs is None:
    #     raise ValueError("ctoaster_jobs is not defined")

    # job_path = os.path.join(ctoaster_jobs, selected_job_name)

    # if not os.path.isdir(job_path):
    #     logger.info(f"Job not found: {job_path}")
    #     return {"error": "Job not found"}

    # try:
    #     os.rmdir(job_path)
    # except Exception as e:
    #     logger.error(f"Error deleting job: {str(e)}")
    #     return {"error": f"Error deleting job: {str(e)}"}

    # return {"message": f"Job '{selected_job_name}' deleted successfully"}


@app.post("/add-job")
async def add_job(request: Request):
    data = await request.json()
    job_name = data.get("job_name")

    if not job_name:
        raise HTTPException(status_code=400, detail="Job name is required")

    if ctoaster_jobs is None:
        raise ValueError("ctoaster_jobs is not defined")

    job_dir = os.path.join(ctoaster_jobs, job_name)
    if os.path.exists(job_dir):
        raise HTTPException(status_code=400, detail="Job already exists")

    # Create the job directory
    try:
        os.makedirs(os.path.join(job_dir, "config"))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Could not create job directory: {str(e)}"
        )

    # Create the main config file
    config_path = os.path.join(job_dir, "config", "config")
    try:
        with open(config_path, "w") as config_file:
            config_file.write(
                "base_config: ?\nuser_config: ?\nrun_length: ?\nt100: ?\n"
            )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Could not write configuration file: {str(e)}"
        )

    return {"status": "success", "message": f"Job '{job_name}' created successfully"}


@app.get("/run-segments/{job_name}")
def get_run_segments(job_name: str):
    try:
        if ctoaster_jobs is None:
            raise ValueError("ctoaster_jobs is not defined")

        job_path = os.path.join(ctoaster_jobs, job_name)

        if not os.path.isdir(job_path):
            raise HTTPException(status_code=404, detail="Job not found")

        segments_dir = os.path.join(job_path, "config", "segments")

        # Function to read the segments from the config directory
        def read_segments():
            segments = []
            if os.path.exists(segments_dir):
                for segment_id in os.listdir(segments_dir):
                    segment_path = os.path.join(segments_dir, segment_id)
                    if os.path.isdir(segment_path):
                        config_path = os.path.join(segment_path, "config")
                        if os.path.exists(config_path):
                            with open(config_path) as f:
                                for line in f:
                                    if line.startswith("run_length:"):
                                        run_length = int(line.split(":")[1].strip())
                                        segments.append((segment_id, run_length))
            return segments

        segments = read_segments()

        if not segments:
            # Default case with a single segment.
            return {"run_segments": ["1: 1-END"]}

        # Generate strings for each segment in the form "<id>: <start>-<end>"
        res = [f"{i + 1}: {start}-{end}" for i, (start, end) in enumerate(segments)]

        # Add a final segment representing the next step after the last known segment.
        final_step = segments[-1][1] + 1  # Assumes segments are sorted and non-empty
        res.append(f"{len(segments) + 1}: {final_step}-END")

        # Reverse the list to match the original behavior and convert to a tuple.
        return {"run_segments": tuple(reversed(res))}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching run segments: {str(e)}"
        )


@app.get("/base-configs")
def get_base_configs():
    try:
        base_configs_dir = os.path.join(ctoaster_data, "base-configs")
        base_configs = [
            f.rpartition(".")[0]
            for f in os.listdir(base_configs_dir)
            if f.endswith(".config")
        ]
        base_configs.sort()
        return {"base_configs": base_configs}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching base configs: {str(e)}"
        )


@app.get("/user-configs")
def get_user_configs():
    try:
        user_configs_dir = os.path.join(ctoaster_data, "user-configs")
        user_configs = []
        for root, _, files in os.walk(user_configs_dir):
            for file in files:
                user_configs.append(
                    os.path.relpath(os.path.join(root, file), user_configs_dir)
                )
        user_configs.sort()
        return {"user_configs": user_configs}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching user configs: {str(e)}"
        )


@app.get("/completed-jobs")
async def get_completed_jobs():
    try:
        if ctoaster_jobs is None:
            raise ValueError("ctoaster_jobs is not defined")

        completed_jobs = []
        # Iterate over all jobs in the jobs directory
        for job_name in os.listdir(ctoaster_jobs):
            job_path = os.path.join(ctoaster_jobs, job_name)
            if os.path.isdir(job_path):
                status_file = os.path.join(job_path, "status")
                if os.path.exists(status_file):
                    status_parts = read_status_file(job_path)
                    if status_parts and status_parts[0] == "COMPLETE":
                        completed_jobs.append(job_name)

        return {"completed_jobs": completed_jobs}
    except Exception as e:
        logger.error(f"Error fetching completed jobs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/setup/{job_name}")
def get_setup(job_name: str):
    try:
        if ctoaster_jobs is None or ctoaster_data is None:
            raise ValueError("ctoaster_jobs or ctoaster_data is not defined")

        job_path = os.path.join(ctoaster_jobs, job_name)

        if not os.path.isdir(job_path):
            logger.info(f"Job not found: {job_path}")
            return {"error": "Job not found"}

        # Read the setup details from the config file
        config_path = os.path.join(job_path, "config", "config")
        if not os.path.exists(config_path):
            raise ValueError("Config file not found")

        setup_details = {
            "base_config": "",
            "user_config": "",
            "modifications": "",
            "run_length": "n/a",
            "restart_from": "",
        }

        # Read from the main config file
        with open(config_path) as f:
            for line in f:
                if line.startswith("base_config:"):
                    setup_details["base_config"] = line.split(":", 1)[1].strip()
                elif line.startswith("user_config:"):
                    setup_details["user_config"] = line.split(":", 1)[1].strip()
                elif line.startswith("run_length:"):
                    setup_details["run_length"] = line.split(":", 1)[1].strip()
                elif line.startswith("restart:"):
                    setup_details["restart_from"] = line.split(":", 1)[1].strip()

        # Read modifications
        mods_path = os.path.join(job_path, "config", "config_mods")
        if os.path.exists(mods_path):
            with open(mods_path) as f:
                setup_details["modifications"] = f.read().strip()

        return {"setup": setup_details}
    except Exception as e:
        logger.error(f"Error retrieving setup details: {str(e)}")
        return {"error": str(e)}


@app.post("/setup/{job_name}")
async def update_setup(job_name: str, request: Request):
    try:
        data = await request.json()
        if ctoaster_jobs is None or ctoaster_data is None:
            raise ValueError("ctoaster_jobs or ctoaster_data is not defined")

        job_path = os.path.join(ctoaster_jobs, job_name)

        if not os.path.isdir(job_path):
            logger.info(f"Job not found: {job_path}")
            return {"error": "Job not found"}

        # Update the main config file
        config_path = os.path.join(job_path, "config", "config")
        if not os.path.exists(config_path):
            raise ValueError("Config file not found")

        # Prepare the updated configuration data
        base_config = data.get("base_config", "")
        user_config = data.get("user_config", "")
        modifications = data.get("modifications", "")
        run_length = data.get("run_length", "n/a")
        restart = data.get("restart_from", "")
        if restart == "":
            restart = None  # Handle empty string as None

        with open(config_path, "w") as f:
            if base_config:
                f.write(
                    f"base_config_dir: {os.path.join(ctoaster_data, 'base-configs')}\n"
                )
                f.write(f"base_config: {base_config}\n")
            if user_config:
                f.write(
                    f"user_config_dir: {os.path.join(ctoaster_data, 'user-configs')}\n"
                )
                f.write(f"user_config: {user_config}\n")
            if restart is not None:
                f.write(f"restart: {restart}\n")
            else:
                f.write("restart: \n")
            today = datetime.datetime.today().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"config_date: {today}\n")
            f.write(f"run_length: {run_length}\n")

        # Update the modifications file
        mods_path = os.path.join(job_path, "config", "config_mods")
        if modifications:
            with open(mods_path, "w") as f:
                f.write(modifications)
        elif os.path.exists(mods_path):
            os.remove(mods_path)

        # Regenerate the namelists
        new_job_script = os.path.join(ctoaster_root, "tools", "new-job.py")
        cmd = [
            sys.executable,
            new_job_script,
            "--gui",
            "-b",
            base_config,
            "-u",
            user_config,
            "-j",
            ctoaster_jobs,
            job_name,
            str(run_length),
        ]
        if modifications:
            cmd.extend(["-m", mods_path])
        if restart:
            cmd.extend(["--restart", restart])

        try:
            res = sp.check_output(cmd, stderr=sp.STDOUT, text=True).strip()
        except sp.CalledProcessError as e:
            res = f"ERR:Failed to run new-job script with error {e.output}"
            raise ValueError(res)
        except Exception as e:
            res = f"ERR:Unexpected error {e}"
            raise ValueError(res)

        if not res.startswith("OK"):
            raise ValueError(res[4:])

        return {"message": "Setup updated successfully"}
    except Exception as e:
        logger.error(f"Error updating setup details: {str(e)}")
        return {"error": str(e)}


## Utility function used in run_job
def read_status_file(job_dir):
    """
    Attempts to read the status file for a job, handling potential issues on Windows where
    the file might be locked by another process.

    :param job_dir: The job directory containing the 'status' file.
    :return: A list containing the status information or None if the file could not be read.
    """
    status = None
    safety = 0
    while not status and safety < 1000:
        try:
            if safety != 0:
                time.sleep(0.001)
            safety += 1
            with open(os.path.join(job_dir, "status")) as fp:
                status = fp.readline().strip().split()
        except IOError:
            pass  # You may log the error here if needed
    if safety == 1000:
        print("Failed to read the status file after multiple attempts.")
    return status


@app.post("/run-job")
async def run_job():
    global selected_job_name
    try:
        if not selected_job_name:
            raise HTTPException(status_code=400, detail="No job selected")

        if ctoaster_jobs is None:
            raise ValueError("ctoaster_jobs is not defined")

        job_path = os.path.join(ctoaster_jobs, selected_job_name)

        if not os.path.isdir(job_path):
            raise HTTPException(status_code=404, detail="Job not found")

        # Check if the job is in a runnable state
        status = "UNCONFIGURED"
        if os.path.exists(os.path.join(job_path, "data_genie")):
            status = "RUNNABLE"
            if os.path.exists(os.path.join(job_path, "status")):
                # Use the read_status_file function to read the status
                status_parts = read_status_file(job_path)
                if status_parts:
                    status = status_parts[0]  # The first element is the status
                else:
                    status = "ERROR"

        if status not in ["RUNNABLE", "PAUSED"]:
            raise HTTPException(
                status_code=400,
                detail=f"Job '{selected_job_name}' is not configured or runnable.",
            )

        # Correct path to check for the executable
        exe = os.path.join(
            ctoaster_jobs,
            "MODELS",
            ctoaster_version,  # Replace with actual version variable or string
            sys.platform.upper(),  # Dynamically get platform information
            "ship",
            "carrotcake.exe",
        )

        # Check if executable exists
        if not os.path.exists(exe):
            raise HTTPException(
                status_code=500, detail=f"Executable not found at {exe}"
            )

        # Copy the executable to the job directory
        runexe = os.path.join(job_path, "carrotcake-ship.exe")
        if os.path.exists(runexe):
            os.remove(runexe)
        shutil.copy(exe, runexe)

        # Handle resuming a paused job
        command_file_path = os.path.join(job_path, "command")
        if os.path.exists(command_file_path):
            os.remove(command_file_path)

        if status == "PAUSED":
            status_parts = read_status_file(job_path)
            if status_parts and len(status_parts) >= 4:
                _, koverall, _, genie_clock = status_parts[:4]
                # Write the GUI_RESTART command to the command file
                with open(command_file_path, "w") as command_file:
                    command_file.write(f"GUI_RESTART {koverall} {genie_clock}\n")
            else:
                raise HTTPException(
                    status_code=500,
                    detail="Status file does not contain the required parameters to resume the job.",
                )

        # Start executable and direct stdout and stderr to run.log in job directory
        log_file_path = os.path.join(job_path, "run.log")
        with open(log_file_path, "a") as log_file:
            process = sp.Popen(
                [runexe], cwd=job_path, stdout=log_file, stderr=sp.STDOUT
            )

        return {"message": f"Job '{selected_job_name}' is now running"}
    except FileNotFoundError as fnfe:
        error_message = f"File not found error: {str(fnfe)}"
        logger.error(error_message)
        raise HTTPException(status_code=500, detail=error_message)
    except Exception as e:
        error_message = f"Unexpected error running job '{selected_job_name}': {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=500, detail=error_message)


@app.post("/pause-job")
async def pause_job():
    global selected_job_name
    try:
        if not selected_job_name:
            raise HTTPException(status_code=400, detail="No job selected")

        if ctoaster_jobs is None:
            raise ValueError("ctoaster_jobs is not defined")

        job_path = os.path.join(ctoaster_jobs, selected_job_name)

        if not os.path.isdir(job_path):
            raise HTTPException(status_code=404, detail="Job not found")

        # Check if the job is currently running or paused
        status_file_path = os.path.join(job_path, "status")
        if not os.path.exists(status_file_path):
            raise HTTPException(status_code=400, detail="Job status file not found")

        with open(status_file_path, "r") as status_file:
            status_line = status_file.readline().strip()
            if "PAUSED" in status_line:
                raise HTTPException(status_code=400, detail="Job is already paused")

        # Write the PAUSE command to the command file
        command_file_path = os.path.join(job_path, "command")
        with open(command_file_path, "w") as command_file:
            command_file.write("PAUSE\n")

        return {"message": f"Job '{selected_job_name}' has been paused"}
    except Exception as e:
        error_message = f"Unexpected error pausing job '{selected_job_name}': {str(e)}"
        logger.error(error_message)
        raise HTTPException(status_code=500, detail=error_message)


@app.get("/get-log/{job_name}")
async def get_log(job_name: str):
    if not job_name:
        raise HTTPException(status_code=400, detail="No job specified")

    if ctoaster_jobs is None:
        raise ValueError("ctoaster_jobs is not defined")

    job_path = os.path.join(ctoaster_jobs, job_name)
    log_file_path = os.path.join(job_path, "run.log")

    if not os.path.exists(log_file_path):
        logger.info(f"Log file not found at: {log_file_path}")
        # Return empty content instead of raising 404
        return {"content": ""}

    # Read the entire log file content
    with open(log_file_path, "r") as log_file:
        content = log_file.read()

    return {"content": content}


# SSE endpoint to stream job output
@app.get("/stream-output/{job_name}")
async def stream_output(job_name: str, background_tasks: BackgroundTasks):
    """
    Stream the output of the specified job using Server-Sent Events (SSE).
    """
    if not job_name:
        raise HTTPException(status_code=400, detail="No job specified")

    if ctoaster_jobs is None:
        raise ValueError("ctoaster_jobs is not defined")

    job_path = os.path.join(ctoaster_jobs, job_name)
    log_file_path = os.path.join(job_path, "run.log")

    # Wait for the log file to be created (retry mechanism)
    max_retries = 30  # Maximum number of retries
    retry_interval = 1  # Time in seconds between retries
    retry_count = 0

    while not os.path.exists(log_file_path) and retry_count < max_retries:
        logger.info(f"Waiting for log file to be created at: {log_file_path}")
        await asyncio.sleep(retry_interval)
        retry_count += 1

    # If the log file is still not found, raise a 404 error
    if not os.path.exists(log_file_path):
        logger.error(f"Log file not found at: {log_file_path}")
        raise HTTPException(
            status_code=404, detail=f"Log file not found at: {log_file_path}"
        )

    # Function to read the log file line by line
    async def log_file_reader():
        with open(log_file_path, "r") as log_file:
            log_file.seek(0, os.SEEK_END)  # Start at the end of the file
            while True:
                line = log_file.readline()
                if line:
                    yield f"data: {line.strip()}\n\n"
                else:
                    await asyncio.sleep(1)  # Wait for new data

    # Start streaming the log file to the client
    return StreamingResponse(log_file_reader(), media_type="text/event-stream")


# Namelist Apis
@app.get("/jobs/{job_id}/namelists")
def get_namelists(job_id: str):
    if ctoaster_jobs is None:
        raise ValueError("ctoaster_jobs is not defined")

    job_dir = os.path.join(ctoaster_jobs, job_id)

    if not os.path.isdir(job_dir):
        raise HTTPException(status_code=404, detail="Job not found")

    # List files in job_dir that start with 'data_' and are files
    namelists = []
    for filename in os.listdir(job_dir):
        file_path = os.path.join(job_dir, filename)
        if filename.startswith("data_") and os.path.isfile(file_path):
            namelist_name = filename[len("data_") :]  # Remove 'data_' prefix
            namelists.append(namelist_name)

    return {"namelists": namelists}


@app.get("/jobs/{job_id}/namelists/{namelist_name}")
def get_namelist_content(job_id: str, namelist_name: str):
    if ctoaster_jobs is None:
        raise ValueError("ctoaster_jobs is not defined")

    job_dir = os.path.join(ctoaster_jobs, job_id)

    if not os.path.isdir(job_dir):
        raise HTTPException(status_code=404, detail="Job not found")

    # Sanitize namelist_name to prevent directory traversal
    safe_namelist_name = os.path.basename(namelist_name)

    # Construct the filename by adding 'data_' prefix
    namelist_filename = f"data_{safe_namelist_name}"
    namelist_file_path = os.path.join(job_dir, namelist_filename)

    if not os.path.isfile(namelist_file_path):
        raise HTTPException(status_code=404, detail="Namelist not found")

    try:
        with open(namelist_file_path, "r") as file:
            content = file.read()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error reading namelist file: {str(e)}"
        )

    return {"namelist_name": safe_namelist_name, "content": content}


@app.get("/get_data_files_list/{job_name}")
async def get_data_files_list(job_name: str):
    if not job_name:
        raise HTTPException(status_code=400, detail="No job specified")

    if ctoaster_jobs is None:
        raise ValueError("ctoaster_jobs is not defined")

    job_path = os.path.join(ctoaster_jobs, job_name)

    if not os.path.isdir(job_path):
        raise HTTPException(status_code=404, detail="Job not found")

    # Search for 'output/biogem' folder specifically
    plot_data_path = None
    for root, dirs, files in os.walk(job_path):
        if "output/biogem" in root:
            plot_data_path = root
            break

    if not plot_data_path:
        raise HTTPException(
            status_code=404, detail="Output/biogem path not found"
        )

    print(":: Resolved plot_data_path ::", plot_data_path)

    try:
        # Log all files in the directory for debugging purposes
        all_files = os.listdir(plot_data_path)
        print(":: All files in resolved path ::", all_files)

        # Match files starting with 'biogem_series'
        data_file_name_prefix = "biogem_series"
        data_file_list = [f for f in all_files if f.startswith(data_file_name_prefix)]

        print(":: List of matching files ::", data_file_list)

        if not data_file_list:
            raise HTTPException(
                status_code=404,
                detail=f"No files found with prefix '{data_file_name_prefix}' in {plot_data_path}",
            )

        return data_file_list

    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Error fetching data files: {str(e)}"
        )

@app.get("/get-variables/{job_name}/{data_file_name}")
async def get_variables(job_name: str, data_file_name: str):
    if not job_name or not data_file_name:
        raise HTTPException(status_code=400, detail="Job name or data file name is missing")

    if ctoaster_jobs is None:
        raise ValueError("ctoaster_jobs is not defined")

    # Construct the job path
    job_path = os.path.join(ctoaster_jobs, job_name)

    if not os.path.isdir(job_path):
        raise HTTPException(status_code=404, detail="Job not found")

    # Search for 'output/biogem' folder specifically
    plot_data_path = None
    for root, dirs, files in os.walk(job_path):
        if "output/biogem" in root:
            plot_data_path = root
            break

    if not plot_data_path:
        raise HTTPException(
            status_code=404, detail="Output/biogem path not found"
        )

    # Construct the full path to the data file
    data_file_path = os.path.join(plot_data_path, data_file_name)

    # Check if the data file exists
    if not os.path.isfile(data_file_path):
        raise HTTPException(status_code=404, detail="Data file not found")

    # Read the file and extract variables from the header line
    try:
        with open(data_file_path, 'r') as file:
            header_line = file.readline().strip()
            # Extract variables based on everything after '/' in the header
            variables = [var.strip() for var in header_line.split('/')[1:]]  # Skip the first column
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading the data file: {str(e)}")

    if not variables:
        raise HTTPException(status_code=404, detail="No variables found in the data file")

    # Return the list of variables directly
    return variables


from pydantic import BaseModel

# Request body model for the POST API
class PlotDataRequest(BaseModel):
    job_name: str
    data_file_name: str
    variable: str

@app.post("/get-plot-data")
async def get_plot_data(request: PlotDataRequest):
    job_name = request.job_name
    data_file_name = request.data_file_name
    variable = request.variable

    if not job_name or not data_file_name or not variable:
        raise HTTPException(status_code=400, detail="Job name, data file, or variable is missing")

    if ctoaster_jobs is None:
        raise ValueError("ctoaster_jobs is not defined")

    # Construct the job path
    job_path = os.path.join(ctoaster_jobs, job_name)

    if not os.path.isdir(job_path):
        raise HTTPException(status_code=404, detail="Job not found")

    # Search for 'output/biogem' folder specifically
    plot_data_path = None
    for root, dirs, files in os.walk(job_path):
        if "output/biogem" in root:
            plot_data_path = root
            break

    if not plot_data_path:
        raise HTTPException(
            status_code=404, detail="Output/biogem path not found"
        )

    # Construct the full path to the data file
    data_file_path = os.path.join(plot_data_path, data_file_name)

    # Check if the data file exists
    if not os.path.isfile(data_file_path):
        raise HTTPException(status_code=404, detail="Data file not found")

    # Read the file and extract data for the selected variable
    try:
        with open(data_file_path, 'r') as file:
            header_line = file.readline().strip()
            columns = header_line.split('/')  # Split by '/' to match the column names
            columns = [col.strip() for col in columns]

            # Get the first column name and the index of the selected variable
            first_column_name = columns[0]  # Use the original first column name
            if variable not in columns:
                raise HTTPException(status_code=404, detail="Variable not found in the data file")

            variable_index = columns.index(variable)

            # Read the data lines and extract the values for the first column and selected variable
            data = []
            for line in file:
                parts = line.strip().split()
                if len(parts) > variable_index:
                    try:
                        first_column_value = float(parts[0])  # Value of the first column
                        data_value = float(parts[variable_index])
                        data.append([first_column_value, data_value])  # Store as [first_column_value, data_value] pair
                    except ValueError:
                        continue  # Skip lines with invalid data

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading the data file: {str(e)}")

    if not data:
        raise HTTPException(status_code=404, detail="No data found for the selected variable")

    # Return the original column names and data
    return {
        "columns": [first_column_name, variable],
        "data": data
    }

from typing import Generator
from fastapi.responses import StreamingResponse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def trim_variable(variable: str) -> str:
    """Trim and normalize the variable by removing leading/trailing spaces."""
    return variable.strip()

async def read_data_file(file_path: str, variable: str) -> Generator[str, None, None]:
    """Generator function to yield existing and new data as it is written to the file."""
    try:
        with open(file_path, 'r') as file:
            # Read header and split by '/'
            header_line = file.readline().strip()
            columns = header_line.split('/')  # Adjust delimiter if needed
            logger.info(f"Raw header columns: {columns}")

            # Trim both the variable and header columns
            trimmed_columns = [trim_variable(col) for col in columns]
            trimmed_variable = trim_variable(variable)
            logger.info(f"Trimmed header columns: {trimmed_columns}")
            logger.info(f"Trimmed variable to match: '{trimmed_variable}'")

            if trimmed_variable not in trimmed_columns:
                logger.error(f"Variable '{trimmed_variable}' not found in columns: {trimmed_columns}")
                raise HTTPException(status_code=404, detail=f"Variable '{variable}' not found in the file")

            variable_index = trimmed_columns.index(trimmed_variable)
            logger.info(f"Variable '{trimmed_variable}' found at index {variable_index}")

            # Step 1: Read all existing data in the file
            while True:
                line = file.readline()
                if not line:
                    break  # Stop when we reach the end of existing data
                
                parts = line.strip().split('/')
                
                if len(parts) > variable_index:
                    try:
                        first_column_value = float(parts[0].strip())  # Handle potential spaces
                        data_value = float(parts[variable_index].strip())
                        logger.info(f"Streaming existing data: {first_column_value}, {data_value}")
                        
                        # Yield existing data in SSE format
                        yield f"data: {first_column_value},{data_value}\n\n"
                    except ValueError:
                        logger.warning(f"Skipping invalid data line: {line}")
                        continue  # Skip lines with invalid data

            # Step 2: Tail the file for new data
            file.seek(0, os.SEEK_END)  # Move to the end of the file to start tailing
            while True:
                line = file.readline()
                if not line:
                    await asyncio.sleep(0.5)  # Sleep briefly to wait for new data
                    continue
                
                parts = line.strip().split('/')
                
                if len(parts) > variable_index:
                    try:
                        first_column_value = float(parts[0].strip())
                        data_value = float(parts[variable_index].strip())
                        logger.info(f"Streaming new data: {first_column_value}, {data_value}")
                        
                        # Yield new data in SSE format
                        yield f"data: {first_column_value},{data_value}\n\n"
                    except ValueError:
                        logger.warning(f"Skipping invalid data line: {line}")
                        continue  # Skip lines with invalid data
    except HTTPException as http_exc:
        logger.error(f"HTTP Exception: {http_exc.detail}")
        raise http_exc
    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error reading the data file: {str(e)}")


@app.post("/get-plot-data-stream")
async def post_plot_data(request: PlotDataRequest):
    """POST API to stream data for plotting."""
    job_name = request.job_name
    data_file_name = request.data_file_name
    variable = request.variable
    
    # Assuming 'ctoaster_jobs' is a global variable or pre-configured path
    logger.info(f"Job name: {job_name}, Data file name: {data_file_name}, Variable: {variable}")
    logger.info(f"Base path for jobs: {ctoaster_jobs}")
    
    job_path = os.path.join(ctoaster_jobs, job_name)

    if not os.path.isdir(job_path):
        logger.error(f"Job directory not found: {job_path}")
        raise HTTPException(status_code=404, detail="Job not found")

    plot_data_path = None
    for root, dirs, files in os.walk(job_path):
        logger.info(f"Searching in directory: {root}")
        if "output/biogem" in root:
            plot_data_path = root
            logger.info(f"Found plot data path: {plot_data_path}")
            break

    if not plot_data_path:
        logger.error(f"Output/biogem path not found in: {job_path}")
        raise HTTPException(status_code=404, detail="Output/biogem path not found")

    data_file_path = os.path.join(plot_data_path, data_file_name)

    if not os.path.isfile(data_file_path):
        logger.error(f"Data file not found: {data_file_path}")
        raise HTTPException(status_code=404, detail="Data file not found")

    logger.info(f"Streaming data from file: {data_file_path}")
    
    # Use StreamingResponse to return the generator with the right media type
    return StreamingResponse(read_data_file(data_file_path, variable), media_type="text/event-stream")