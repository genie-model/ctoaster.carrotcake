# carrotcake
(cGENIE varient)
(also known as cTOASTER -- the carbon Turnover in Ocean, Atmosphere, Sediment, and Terrestrial Exchangeable Reservoirs model)


Docker Commands:

1. docker build -t ctoaster-backend:1.0 .

2. docker run -d -p 8000:8000 \
  -v /home/ravitheja/ctoaster.carrotcake-jobs:/home/ravitheja/ctoaster.carrotcake-jobs \
  -v /home/ravitheja/ctoaster.carrotcake-data:/home/ravitheja/ctoaster.carrotcake-data \
  -v /home/ravitheja/ctoaster.carrotcake-test:/home/ravitheja/ctoaster.carrotcake-test \
  ctoaster-backend:1.0

3. docker logs <container_id>

4. Pushing the image:

    # Authenticate with Google Cloud
    gcloud auth login
    gcloud auth configure-docker

    # Tag the Docker image
    docker tag ctoaster-backend:1.0 us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0

    # Push the Docker image
    docker push us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0

    # Pull the Docker image
    docker pull us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0

    # Verify the image
    gcloud artifacts docker images list us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend

5. Deleting the image:

    # Authenticate with Google Cloud
    gcloud auth login
    gcloud auth configure-docker


    a. gcloud artifacts repositories list
    b. gcloud artifacts docker images list \
    us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake
    c. gcloud artifacts docker images delete \
    us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0 \
    --quiet
 
 6. 
