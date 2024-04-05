# Use a multi-stage build to create a platform-independent image
FROM --platform=$BUILDPLATFORM continuumio/miniconda3:latest AS build

# Set build arguments
ARG TARGETPLATFORM
ARG BUILDPLATFORM

# Install necessary build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create and activate the conda environment
RUN conda create --name myproject python=3.8 -y
SHELL ["conda", "run", "-n", "myproject", "/bin/bash", "-c"]

# Install netcdf-fortran using apt-get
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnetcdf-dev \
    libnetcdff-dev \
    && rm -rf /var/lib/apt/lists/*

# Install scons and matplotlib using pip
RUN pip install --no-cache-dir scons matplotlib

# Clone the ctoaster.cupcake repository
RUN git clone https://github.com/derpycode/ctoaster.cupcake.git
WORKDIR /ctoaster.cupcake

# Create the final image
FROM continuumio/miniconda3:latest

# Copy the conda environment from the build stage
COPY --from=build /opt/conda/envs/myproject /opt/conda/envs/myproject

# Activate the conda environment
SHELL ["conda", "run", "-n", "myproject", "/bin/bash", "-c"]

# Copy the project files
COPY --from=build /ctoaster.cupcake /ctoaster.cupcake
WORKDIR /ctoaster.cupcake

# Set any necessary environment variables
