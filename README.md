# Carrotcake

**Carrotcake** is a variant of **cGENIE** (also known as **cTOASTER** — the **Carbon Turnover in Ocean, Atmosphere, Sediment, and Terrestrial Exchangeable Reservoirs** model). It is designed to simulate and analyze carbon turnover across various Earth system reservoirs.

---

## Setup Instructions

### Prerequisites

1. **Install Docker**  
   Follow the official Docker installation guide for your platform:  
   [Docker Installation Guide](https://docs.docker.com/engine/install/)

2. **Install Google Cloud SDK**  
   Install the Google Cloud SDK for your operating system:  
   [Google Cloud SDK Installation Guide](https://cloud.google.com/sdk/docs/install)

---

## Docker Commands

### 1. Building the Docker Image

To build the Docker image, run the following command:

```
docker build -t ctoaster-backend:1.0 .
```
2. Running the Docker Image
To run the Docker container in detached mode, use:

```
docker run -d --name ctoaster-backend-container -p 8000:8000 ctoaster-backend:1.0
```
3. Checking Container Logs
To monitor the logs of the running container:

List all running containers:

```
docker container ls
```
# or
```
docker ps
```
Check the logs using the container ID:

```
docker logs <container_id>
```
4. Pushing the Docker Image to Google Container Registry
To upload the Docker image to Google Container Registry:

Authenticate with Google Cloud:

```
gcloud auth login
```
```
gcloud auth configure-docker
```

Tag the Docker image:

```
docker tag ctoaster-backend:1.0 us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0
```


Push the Docker image:

```
docker push us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0
```
5. Pulling the Docker Image
To pull the Docker image from Google Container Registry:

```
docker pull us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0
```
6. Listing Existing Docker Images
To list all Docker images in the cupcake folder of your Google Container Registry:

```
gcloud artifacts docker images list us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend
```

7. Deleting a Docker Image
To delete a Docker image from Google Container Registry:

Authenticate with Google Cloud:

```
gcloud auth login
```

```
gcloud auth configure-docker
```

List repositories:

```
gcloud artifacts repositories list
```

List Docker images:

```
gcloud artifacts docker images list us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake
```

Delete the Docker image:

```
gcloud artifacts docker images delete us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0 --quiet
```