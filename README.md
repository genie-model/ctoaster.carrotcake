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

# 🔥 CTOASTER Backend - Kubernetes Deployment Guide

This guide outlines how to deploy the CTOASTER Backend application on a Google Kubernetes Engine (GKE) cluster.

---

## 🚀 **1. Prerequisites**
Ensure you have the following installed:

- [Google Cloud SDK](https://cloud.google.com/sdk/docs/install)
- [kubectl](https://kubernetes.io/docs/tasks/tools/) for Kubernetes
- [GKE Auth Plugin](https://cloud.google.com/kubernetes-engine/docs/how-to/cluster-access-for-kubectl#install_plugin)

To verify installations:
```bash
gcloud version
kubectl version --client
```

---

## ☁️ **2. Deploy CTOASTER Backend to GKE**

### **2.1 Enable GKE and Create Cluster**
Ensure GKE API is enabled:
```bash
gcloud services enable container.googleapis.com
```

Create GKE cluster (**only if a new cluster is needed**):
```bash
gcloud container clusters create ctoaster-cluster \
    --region us-west2 \
    --num-nodes=2 \
    --enable-autoupgrade
```

Connect `kubectl` to the cluster:
```bash
gcloud container clusters get-credentials ctoaster-cluster --region us-west2
```

---

### **2.2 Deploy Backend App to GKE**

1. **Push Docker image to Google Artifact Registry (GAR):**
```bash
gcloud auth configure-docker us-west2-docker.pkg.dev

# Tag and push the image
docker tag ctoaster-backend:1.0 us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0
docker push us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0
```

2. **Create Kubernetes manifests (**needs to be done only once per cluster**):**
- `deployment.yaml`:
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ctoaster-backend-deployment
  labels:
    app: ctoaster-backend
spec:
  replicas: 2
  selector:
    matchLabels:
      app: ctoaster-backend
  template:
    metadata:
      labels:
        app: ctoaster-backend
    spec:
      containers:
      - name: ctoaster-backend
        image: us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          value: "your-database-url"
        - name: OTHER_ENV_VAR
          value: "some-value"
        imagePullPolicy: Always
```

- `service.yaml`:
```yaml
apiVersion: v1
kind: Service
metadata:
  name: ctoaster-backend-service
spec:
  type: LoadBalancer
  ports:
    - port: 80
      targetPort: 8000
  selector:
    app: ctoaster-backend
```

---

3. **Deploy to GKE:**
```bash
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

> **Persistent volume for jobs (per-user subdirs)**  
> Ensure a ReadWriteMany PVC is created and mounted at `/ctoaster.carrotcake-jobs` on all backend pods so any pod can serve any user’s jobs. Example PVC:
> ```bash
> kubectl apply -f pvc-jobs.yaml
> ```
> The backend will store jobs under `/ctoaster.carrotcake-jobs/<user_id>/<job_name>`.

> **Secrets / env**  
> Create a secret containing `jwt-secret`:
> ```bash
> kubectl create secret generic ctoaster-secrets --from-literal=jwt-secret='set-a-strong-secret'
> ```
> The deployment references this secret via `CTOASTER_JWT_SECRET`.

4. **Check status:**
```bash
kubectl get pods
kubectl get svc
```

Expected output:
```
NAME                                           READY   STATUS    RESTARTS   AGE
ctoaster-backend-deployment-xyz123              1/1     Running   0          10s
```

To get the external IP of the backend service:
```bash
kubectl get svc ctoaster-backend-service
```
Expected output:
```
NAME                        TYPE           CLUSTER-IP      EXTERNAL-IP     PORT(S)        AGE
ctoaster-backend-service    LoadBalancer   34.118.234.130  35.235.72.29    80:31234/TCP   5m
```

You can now access the API at:
```
http://35.235.72.29
```

---

# ⚖️ 4 CTOASTER Backend - Horizontal Pod Autoscaler (HPA) Guide

This guide explains how to automatically scale the number of pods for the `ctoaster-backend` Kubernetes deployment based on CPU utilization.

---

## 📌 Prerequisites

1. Your backend deployment **must define resource requests and limits**:

```yaml
resources:
  requests:
    cpu: "250m"
    memory: "256Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"
```

2. The **Kubernetes metrics server** must be installed. If not already installed, run:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

---

## 📄 Create HPA Manifest

Create a file named `hpa-backend.yaml` with the following contents:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: ctoaster-backend-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: ctoaster-backend
  minReplicas: 2
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
```

---

## 🚀 Apply the HPA

Apply the updated deployment (if you added `resources:`) and then the HPA manifest:

```bash
kubectl apply -f deployment.yaml
kubectl apply -f pvc-jobs.yaml
kubectl apply -f hpa-backend.yaml
```

---

## 🔍 Monitor the HPA

You can verify that the HPA is active and functioning:

```bash
kubectl get hpa
kubectl top pods
```

Example output:
```
NAME                    REFERENCE              TARGETS   MINPODS   MAXPODS   REPLICAS   AGE
ctoaster-backend-hpa   Deployment/ctoaster-backend   45%/60%   2         5         2          1m
```

---

## 🔍 **5. Monitoring & Troubleshooting**

1. **Check pod status:**
```bash
kubectl get pods
```

2. **View pod logs:**
```bash
kubectl logs -f <pod-name>
```

3. **Check deployment:**
```bash
kubectl describe deployment ctoaster-backend-deployment
```

4. **Check service and external IP:**
```bash
kubectl get svc
```

---

## 🧹 **6. Cleanup (Optional)**
To delete the GKE cluster and avoid billing:
```bash
# Delete cluster
gcloud container clusters delete ctoaster-cluster --region us-west2

# Delete Docker image from GAR
gcloud artifacts docker images delete us-west2-docker.pkg.dev/ucr-ursa-major-ridgwell-lab/cupcake/ctoaster-backend:1.0
```

---

## 📞 **7. Support**
For any issues or further assistance:
- Check GKE logs: `kubectl describe pod <pod-name>`
- Visit [Google Kubernetes Engine Docs](https://cloud.google.com/kubernetes-engine/docs)
- Contact the project admin or Slack channel.

---

🎉 **That's it! Your CTOASTER Backend is now running on Kubernetes with a LoadBalancer service!** 🚀🔥