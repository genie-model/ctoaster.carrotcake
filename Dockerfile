# Use a lightweight Python image
FROM python:3.10-slim

# Set environment variables to avoid Python buffering
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set the working directory in the container
WORKDIR /ctoaster.carrotcake

# Install required system packages (e.g., git)
RUN apt-get update && apt-get install -y git && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy the application code and tools directory
COPY tools /ctoaster.carrotcake/tools
COPY requirements.txt /ctoaster.carrotcake/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Add the working directory to PYTHONPATH
ENV PYTHONPATH=/ctoaster.carrotcake

# Create required directories
RUN mkdir -p /ctoaster.carrotcake-data \
    && mkdir -p /ctoaster.carrotcake-test \
    && mkdir -p /ctoaster.carrotcake-jobs

# Clone the required repositories
RUN git clone https://github.com/genie-model/ctoaster-data /ctoaster.carrotcake-data \
    && git clone https://github.com/genie-model/ctoaster-test /ctoaster.carrotcake-test

# Create the hidden .ctoasterrc file with the configuration
RUN echo "ctoaster_root: /ctoaster.carrotcake\nctoaster_data: /ctoaster.carrotcake-data\nctoaster_test: /ctoaster.carrotcake-test\nctoaster_jobs: /ctoaster.carrotcake-jobs\nctoaster_version: DEVELOPMENT" > /root/.ctoasterrc

# Expose the port the FastAPI server runs on
EXPOSE 8000

# Command to run the FastAPI server
CMD ["uvicorn", "tools.REST:app", "--host", "0.0.0.0", "--port", "8000"]
