import asyncio
import datetime
import logging
import os
import shutil
import subprocess as sp
import sys

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
origins = ["http://localhost:5001"]  # React development server

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

    try:
        os.makedirs(os.path.join(job_dir, "config"))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Could not create job directory: {str(e)}"
        )

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
            "run_segment": "1:1-END",
            "base_config": "",
            "user_config": "",
            "modifications": "",
            "run_length": "n/a",
            "t100": False,
            "restart_from": "",
        }

        # Read from the main config file
        with open(config_path) as f:
            for line in f:
                if line.startswith("base_config:"):
                    setup_details["base_config"] = line.split(":")[1].strip()
                if line.startswith("user_config:"):
                    setup_details["user_config"] = line.split(":")[1].strip()
                if line.startswith("run_length:"):
                    setup_details["run_length"] = line.split(":")[1].strip()
                if line.startswith("t100:"):
                    setup_details["t100"] = line.split(":")[1].strip().lower() == "true"

        # Read modifications
        mods_path = os.path.join(job_path, "config", "config_mods")
        if os.path.exists(mods_path):
            with open(mods_path) as f:
                setup_details["modifications"] = f.read().strip()

        # Get the segments for the job
        segments = get_run_segments(job_name)
        if segments and "run_segments" in segments:
            setup_details["run_segment"] = segments["run_segments"][
                -1
            ]  # Use the last segment

        # Get the restart_from options
        restart_jobs = []
        restart_dir = os.path.join(ctoaster_data, "restart-jobs")
        if os.path.exists(restart_dir):
            for d in os.listdir(restart_dir):
                restart_jobs.append(d)
            setup_details["restart_from"] = restart_jobs

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
        full_config = data.get("full_config", "")
        modifications = data.get("modifications", "")
        run_length = data.get("run_length", "n/a")
        t100 = "true" if data.get("t100", False) else "false"
        restart = data.get("restart_from", "")

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
            if full_config:
                f.write(
                    f"full_config_dir: {os.path.join(ctoaster_data, 'full-configs')}\n"
                )
                f.write(f"full_config: {full_config}\n")
            if restart:
                f.write(f"restart: {restart}\n")

            today = datetime.datetime.today().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"config_date: {today}\n")
            f.write(f"run_length: {run_length}\n")
            f.write(f"t100: {t100}\n")

        # Update the modifications file
        mods_path = os.path.join(job_path, "config", "config_mods")
        if modifications:
            with open(mods_path, "w") as f:
                f.write(modifications)
        elif os.path.exists(mods_path):
            os.remove(mods_path)

        # Set the status of the job before updating the setup details
        status_file = os.path.join(job_path, "status")
        if os.path.exists(status_file):
            job_status = read_status_file(job_path)
            if job_status and job_status[0] == "COMPLETE":
                job_status[0] = "PAUSED"
                with open(status_file, "w") as fp:
                    fp.write(" ".join(job_status) + "\n")

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
        if t100:
            cmd.append("--t100")
        if modifications:
            cmd.extend(["-m", mods_path])

        try:
            with open(os.devnull, "w") as sink:
                res = sp.check_output(cmd, stderr=sp.STDOUT, text=True).strip()
        except sp.CalledProcessError as e:
            res = f"ERR:Failed to run new-job script with error {e.output}"
        except Exception as e:
            res = f"ERR:Unexpected error {e}"

        if not res.startswith("OK"):
            raise ValueError(res[4:])

        return {"message": "Setup updated successfully"}
    except Exception as e:
        logger.error(f"Error updating setup details: {str(e)}")
        return {"error": str(e)}


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
                with open(os.path.join(job_path, "status")) as f:
                    status_line = f.readline().strip()
                    status = status_line.split()[0] if status_line else "ERROR"

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
            sys.platform,  # Dynamically get platform information
            "ship",
            "carrotcake.exe",
        )

        # Check if executable exists
        if not os.path.exists(exe):
            raise HTTPException(
                status_code=500, detail=f"Executable not found at {exe}"
            )

        # Handle resuming a paused job
        command_file_path = os.path.join(job_path, "command")
        if status == "PAUSED":
            # Remove any existing command file
            if os.path.exists(command_file_path):
                os.remove(command_file_path)

            # Read the necessary parameters from the status file
            status_file_path = os.path.join(job_path, "status")
            with open(status_file_path, "r") as status_file:
                status_line = status_file.readline().strip()
                if status_line.startswith("PAUSED"):
                    status_parts = status_line.split()
                    if len(status_parts) >= 3:
                        _, koverall, genie_clock = status_parts[
                            :3
                        ]  # Extract the first three values
                    else:
                        raise HTTPException(
                            status_code=500,
                            detail="Status line does not contain the required parameters to resume the job.",
                        )

                    # Write the GUI_RESTART command to the command file
                    with open(command_file_path, "w") as command_file:
                        command_file.write(f"GUI_RESTART {koverall} {genie_clock}\n")

        # Start executable and direct stdout and stderr to run.log in job directory
        log_file_path = os.path.join(job_path, "run.log")
        with open(log_file_path, "a") as log_file:
            process = sp.Popen([exe], cwd=job_path, stdout=log_file, stderr=sp.STDOUT)

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
