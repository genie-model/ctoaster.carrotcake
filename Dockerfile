# Use a lightweight Python image
FROM python:3.10-slim

# Set environment variables to avoid Python buffering
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Set the working directory in the container
WORKDIR /ctoaster.carrotcake

# Install required system packages and dependencies for netCDF installation
RUN apt-get update && apt-get install -y \
    git \
    net-tools \
    wget \
    libnetcdf-dev \
    libnetcdff-dev \
    gfortran \
    build-essential \
    m4 \
    libxml2-dev \
    libcurl4-openssl-dev \
    libhdf5-dev \
    zlib1g-dev \
    unzip \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Download and install netCDF C libraries
RUN wget https://downloads.unidata.ucar.edu/netcdf-c/4.9.2/netcdf-c-4.9.2.tar.gz \
    && tar -xvzf netcdf-c-4.9.2.tar.gz \
    && cd netcdf-c-4.9.2 \
    && export LDFLAGS="-L/usr/lib/x86_64-linux-gnu/hdf5/serial/lib" \
    && export CFLAGS="-I/usr/lib/x86_64-linux-gnu/hdf5/serial/include" \
    && ./configure \
    && make -j$(nproc) \
    && make check \
    && make install \
    && cd .. && rm -rf netcdf-c-4.9.2 netcdf-c-4.9.2.tar.gz

# Download and install netCDF Fortran libraries
RUN wget https://downloads.unidata.ucar.edu/netcdf-fortran/4.6.1/netcdf-fortran-4.6.1.tar.gz \
    && tar -xvzf netcdf-fortran-4.6.1.tar.gz \
    && cd netcdf-fortran-4.6.1 \
    && ./configure \
    && make -j$(nproc) \
    && make check \
    && make install \
    && cd .. && rm -rf netcdf-fortran-4.6.1 netcdf-fortran-4.6.1.tar.gz

# Run ldconfig to update library links
RUN ldconfig

# Copy the entire project
COPY . /ctoaster.carrotcake

# Copy the MODELS folder
COPY MODELS /ctoaster.carrotcake-jobs/MODELS

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create required directories
RUN mkdir -p /ctoaster.carrotcake-data \
    && mkdir -p /ctoaster.carrotcake-test \
    && mkdir -p /ctoaster.carrotcake-jobs

# Clone the required repositories
RUN git clone https://github.com/genie-model/ctoaster-data /ctoaster.carrotcake-data \
    && git clone https://github.com/genie-model/ctoaster-test /ctoaster.carrotcake-test

# Create the hidden .ctoasterrc file with the configuration
RUN echo "ctoaster_root: /ctoaster.carrotcake\nctoaster_data: /ctoaster.carrotcake-data\nctoaster_test: /ctoaster.carrotcake-test\nctoaster_jobs: /ctoaster.carrotcake-jobs\nctoaster_version: DEVELOPMENT" > /root/.ctoasterrc

# Make necessary scripts executable
RUN chmod +x /ctoaster.carrotcake/setup-ctoaster /ctoaster.carrotcake/run-carrotcake /ctoaster.carrotcake/tests

# Set up SCons and build the project
RUN pip install scons 
# \
    # && scons -C /ctoaster.carrotcake-jobs/MODELS/DEVELOPMENT/LINUX/ship

# Expose the port for the FastAPI server
EXPOSE 8000

# Command to run the FastAPI server
CMD ["uvicorn", "tools.REST:app", "--host", "0.0.0.0", "--port", "8000"]
