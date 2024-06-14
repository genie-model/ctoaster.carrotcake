import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from utils import read_ctoaster_config

app = FastAPI()

# CORS configuration
origins = [
    "http://localhost:5001",  # React development server
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the configuration
read_ctoaster_config()


@app.get("/jobs")
def list_jobs():
    try:
        # Access the global variable ctoaster_jobs after it has been initialized
        from utils import ctoaster_jobs

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


# Run the server with: uvicorn REST:app --reload
