import logging
import os
import shutil

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from utils import read_ctoaster_config

# Initialize the configuration
read_ctoaster_config()

from utils import ctoaster_root, ctoaster_jobs, ctoaster_data

app = FastAPI()

# CORS configuration
origins = [  
    "http://cupcake.ctoaster.org"   # React development server
]

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


# Run the server with: uvicorn REST:app --reload
