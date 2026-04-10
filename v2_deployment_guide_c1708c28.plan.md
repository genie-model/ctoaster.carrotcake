---
name: V2 Deployment Guide
overview: A detailed reference document covering every code change made in the V2 scaled architecture refactor, plus a step-by-step Cloud Shell deployment playbook for GKE.
todos:
  - id: doc-review
    content: Review and confirm the deployment guide document before writing it to a .md file in the repo
    status: completed
  - id: doc-write
    content: Write the finalised guide to ctoaster.carrotcake/docs/v2-deployment-guide.md
    status: completed
isProject: false
---

# Carrotcake V2 — Code Changes & GKE Deployment Guide

---

## 1. Architecture Overview

```mermaid
flowchart TD
    Browser -->|"HTTPS /api/*"| Ingress
    Browser -->|"HTTPS /*"| Ingress
    Ingress -->|"/api → strip prefix"| API_Service["ctoaster-api-service (ClusterIP)"]
    Ingress -->|"/"| FE_Service["ctoaster-frontend-service (ClusterIP)"]
    FE_Service --> FE_Pod["Frontend Pod (React)"]
    API_Service --> API_Pod["API Pod (FastAPI, stateless)"]
    API_Pod -->|"mounts RWX"| Filestore[("GKE Filestore\n/ctoaster-filestore")]
    API_Pod -->|"reads/writes"| CloudSQL[("Cloud SQL\nPostgres")]
    API_Pod -->|"batch/v1 create Job"| K8s_Job["Runner Job Pod (ephemeral)"]
    K8s_Job -->|"mounts RWX"| Filestore
    K8s_Job -->|"reads/writes"| CloudSQL
    K8s_Job -->|"emptyDir for fast I/O"| Workspace[("emptyDir\n/workspace")]
```



Key design changes from V1:

- The API tier is **fully stateless** — no `ACTIVE_RUNS` dict, no in-process science execution.
- Each science run gets its **own isolated Kubernetes Job pod** (one per run, any user).
- A **central Postgres database** (Cloud SQL) replaces per-replica SQLite.
- A **GKE Filestore RWX volume** is the single canonical location for all job folders, logs, and outputs. Both API pods and runner pods mount it.

---

## 2. Code Changes Made

### 2.1 New file: `[tools/db.py](tools/db.py)` (525 lines)

Central database abstraction layer. Supports **PostgreSQL** (production) and **SQLite** (local dev) via the `DB_URL` environment variable.

**Schema tables created by `init_db()`:**

- `users` — email, password_hash, salt, created_at
- `jobs` — user_id (FK), job_name, job_path, created_at, updated_at
- `runs` — job_id (FK), user_id (FK), run_id (UUID hex), k8s_job_name, status, desired_state, heartbeat_at, started_at, finished_at, shared_run_path
- `artifacts` — run_id (FK), artifact_type, path, size_bytes, created_at

**Key exported functions:**


| Function                                                             | Purpose                                                         |
| -------------------------------------------------------------------- | --------------------------------------------------------------- |
| `init_db()`                                                          | Creates all tables if they do not exist (called on API startup) |
| `create_user(email, password)`                                       | Hashes password with PBKDF2+SHA256, inserts user row            |
| `verify_password(password, salt, pw_hash)`                           | Constant-time PBKDF2 comparison                                 |
| `get_user_by_email(email)`                                           | Lookup for login                                                |
| `upsert_job_record(user_id, job_name, job_path)`                     | Insert-or-update job metadata                                   |
| `get_job_record(user_id, job_name)`                                  | Used by pause/delete to find active run                         |
| `delete_job_record(user_id, job_name)`                               | Removes DB row when job folder is deleted                       |
| `create_run(job_id, user_id, run_id, k8s_job_name, shared_run_path)` | Records a new run as PENDING                                    |
| `update_run(run_id, **fields)`                                       | Updates status, desired_state, heartbeat_at, etc.               |
| `get_active_run_for_job(job_id)`                                     | Returns run row if status not in TERMINAL_STATES                |
| `get_latest_run_for_job(job_id)`                                     | Returns most recent run regardless of state                     |


**Environment variable:** `DB_URL` (default `sqlite:///./ctoaster_dev.db`)

---

### 2.2 New file: `[tools/storage.py](tools/storage.py)` (276 lines)

Filestore path helpers and sync logic. **No FastAPI dependency** — usable by both API and runner pods.

**Key exported functions:**


| Function                                                   | Purpose                                                                |
| ---------------------------------------------------------- | ---------------------------------------------------------------------- |
| `get_filestore_root()`                                     | Resolves Filestore root from `FILESTORE_ROOT` env var or `.ctoasterrc` |
| `validate_job_name(job_name)`                              | Enforces allowlist chars, max 128 chars                                |
| `safe_join(base, *paths)`                                  | Path-traversal guard — raises ValueError                               |
| `get_user_root(user_id)`                                   | `<FILESTORE_ROOT>/jobs/<user_id>`                                      |
| `get_job_path(user_id, job_name)`                          | `<FILESTORE_ROOT>/jobs/<user_id>/<job_name>`                           |
| `get_exe_path(version, platform)`                          | `<FILESTORE_ROOT>/MODELS/<ver>/<platform>/ship/carrotcake.exe`         |
| `stage_job_to_workspace(user_id, job_name, workspace_dir)` | Copies job folder from Filestore to local emptyDir                     |
| `sync_to_shared(workspace_job_path, shared_job_path)`      | Incrementally syncs local workspace → Filestore                        |
| `find_plot_data_path(job_path)`                            | Walks for `output/biogem` sub-directory                                |
| `write_owner(job_path, user_id, email)`                    | Atomically writes `owner.json`                                         |
| `read_owner(job_path)`                                     | Returns owner dict or None                                             |


**Environment variables:** `FILESTORE_ROOT`, `CTOASTER_VERSION`, `CTOASTER_PLATFORM`

---

### 2.3 New file: `[tools/k8s_jobs.py](tools/k8s_jobs.py)` (222 lines)

Kubernetes Job lifecycle management using the `kubernetes` Python client.

**Key exported functions:**


| Function                                                          | Purpose                                                       |
| ----------------------------------------------------------------- | ------------------------------------------------------------- |
| `create_runner_job(run_id, job_id, user_id, job_name, namespace)` | Creates a `batch/v1` Job; returns `k8s_job_name`              |
| `delete_runner_job(k8s_job_name, namespace)`                      | Deletes Job and its pods (cascade)                            |
| `get_runner_job_status(k8s_job_name, namespace)`                  | Returns `"RUNNING"`, `"COMPLETE"`, `"FAILED"`, or `"UNKNOWN"` |


`create_runner_job` injects these env vars into the runner pod: `RUN_ID`, `JOB_ID`, `USER_ID`, `JOB_NAME`, `DB_URL`, `FILESTORE_ROOT`, `CTOASTER_JWT_SECRET`, and optionally `CTOASTER_VERSION`, `CTOASTER_PLATFORM`, `CTOASTER_SYNC_INTERVAL_SECONDS`.

**Environment variables read:** `RUNNER_IMAGE` (required), `FILESTORE_PVC_NAME` (default `ctoaster-jobs-pvc`), `FILESTORE_ROOT` (default `/ctoaster-filestore`), `DB_URL`, `CTOASTER_JWT_SECRET`

---

### 2.4 New file: `[tools/runner.py](tools/runner.py)` (373 lines)

Entrypoint for runner Job pods, invoked as `python -m tools.runner`.

**Execution flow:**

```mermaid
flowchart TD
    Start([Pod starts]) --> ReadEnv["Read required env vars\nRUN_ID, JOB_ID, USER_ID, JOB_NAME"]
    ReadEnv --> InitDB["init_db() — ensure schema"]
    InitDB --> UpdateRunning["DB: update run → RUNNING"]
    UpdateRunning --> StageJob["stage_job_to_workspace()\nFilestore → /workspace"]
    StageJob --> CopyExe["Copy carrotcake.exe into workspace"]
    CopyExe --> CheckPrior{Prior status\n== PAUSED?}
    CheckPrior -->|Yes| WriteRestart["Write GUI_RESTART to command file"]
    CheckPrior -->|No| Launch
    WriteRestart --> Launch["Popen carrotcake.exe\ncwd=workspace, stdout→run.log"]
    Launch --> SyncThread["Start background sync thread\nevery SYNC_INTERVAL seconds"]
    SyncThread --> Wait["process.wait() — blocks"]
    Wait --> StopThread["Stop sync thread"]
    StopThread --> FinalSync["Final sync_to_shared()"]
    FinalSync --> FinalDB["DB: update run → COMPLETE/FAILED/PAUSED"]
    FinalDB --> Exit([sys.exit 0 or 1])
```



Background sync thread also:

- Checks DB for `desired_state == PAUSE_REQUESTED` → writes `PAUSE\n` to workspace command file
- Checks DB for `desired_state == CANCEL_REQUESTED` → sends `SIGTERM` to the science process
- Updates `heartbeat_at` in DB every cycle

**Required environment variables:** `RUN_ID`, `JOB_ID`, `USER_ID`, `JOB_NAME`, `DB_URL` (via `tools.db`), `FILESTORE_ROOT` (via `tools.storage`)
**Optional:** `WORKSPACE_ROOT` (default `/workspace`), `CTOASTER_SYNC_INTERVAL_SECONDS` (default `2.0`)

---

### 2.5 New file: `[tools/__init__.py](tools/__init__.py)` (2 lines)

Empty file that makes `tools/` a Python package so `python -m tools.runner` works as the pod entrypoint.

---

### 2.6 Modified file: `[tools/REST.py](tools/REST.py)` (1017 lines)

Major stateless refactor. Summary of all changes:

**Removed:**

- `ACTIVE_RUNS` dict and all in-process subprocess management
- `selected_job_name_by_user` session dict
- Local SQLite auth helpers (`_sqlite_conn`, `_hash_pw`, etc.)
- `stage_shared_job_to_workspace`, `sync_workspace_back_to_shared`, `get_effective_job_path`
- `safe_join`, `validate_job_name`, `write_owner`, `read_owner` (all moved to `storage.py`)

**Added:**

- Imports from `tools.db`, `tools.storage`, `tools.k8s_jobs`
- `init_db()` call at module load

**Endpoint-by-endpoint changes:**


| Endpoint              | Change                                                                                                                              |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `POST /auth/register` | Now calls `db.create_user()` instead of local SQLite helper                                                                         |
| `POST /auth/login`    | Now calls `db.get_user_by_email()` + `db.verify_password()`                                                                         |
| `POST /run-job`       | Accepts `job_name` in JSON body; checks DB for active run; calls `k8s_jobs.create_runner_job()`; persists run via `db.create_run()` |
| `POST /pause-job`     | Accepts `job_name` in JSON body; calls `db.update_run(desired_state="PAUSE_REQUESTED")`; writes `PAUSE\n` command file to Filestore |
| `DELETE /delete-job`  | Accepts `job_name` as query param (was implicit session); checks DB for active run before deleting; calls `db.delete_job_record()`  |
| `POST /add-job`       | Calls `db.upsert_job_record()` + `storage.write_owner()`                                                                            |
| All read endpoints    | Replace `get_effective_job_path()` with `storage.get_job_path(user_id, job_name)` — always reads from Filestore                     |
| `GET /job/{job_name}` | Removes `selected_job_name_by_user[uid] = job_name` side-effect                                                                     |
| `GET /healthz`        | Unchanged — returns `{"ok": True}`                                                                                                  |


---

### 2.7 Modified file: `[requirements.txt](requirements.txt)` (40 lines)

Added two new dependencies:

```
kubernetes>=29.0.0
psycopg2-binary>=2.9.9
```

---

### 2.8 Modified file: `[cupcake-frontend/src/components/HomePage.js](../cupcake-frontend/src/components/HomePage.js)`

Three call-site changes to pass `job_name` explicitly (removes server-side session dependency):

- `handleRunJob`: `api.post('/run-job', { job_name: selectedJob.name })`
- `handlePauseJob`: `api.post('/pause-job', { job_name: selectedJob.name })`
- `handleDeleteJob`: `api.delete('/delete-job', { params: { job_name: selectedJob.name } })`

---

### 2.9 New directory: `[k8s/base/](k8s/base/)` (11 files)

All Kubernetes manifests consolidated here. Old root-level YAML files (`deployment.yaml`, `service.yaml`, `horizontal-pod-autoscaler.yaml`, `pvc-jobs.yaml`) are superseded.


| File                       | Kind(s)                                 | Purpose                                                |
| -------------------------- | --------------------------------------- | ------------------------------------------------------ |
| `namespace.yaml`           | Namespace                               | Creates `ctoaster` namespace                           |
| `api-rbac.yaml`            | ServiceAccount, Role, RoleBinding       | `ctoaster-api` SA with `batch/v1` Jobs + Pods CRUD     |
| `filestore-pvc.yaml`       | PersistentVolume, PersistentVolumeClaim | RWX Filestore volume (1 Ti)                            |
| `api-deployment.yaml`      | Deployment                              | Stateless API + Cloud SQL Auth Proxy sidecar           |
| `api-service.yaml`         | Service (ClusterIP)                     | Internal API service                                   |
| `api-hpa.yaml`             | HorizontalPodAutoscaler                 | CPU+memory autoscaler, 2–10 replicas                   |
| `frontend-deployment.yaml` | Deployment                              | React frontend, `REACT_APP_API_URL=/api`               |
| `frontend-service.yaml`    | Service (ClusterIP)                     | Internal frontend service                              |
| `frontend-hpa.yaml`        | HorizontalPodAutoscaler                 | CPU autoscaler, 2–5 replicas                           |
| `ingress.yaml`             | Ingress                                 | Nginx: `/api/*` → API (strips prefix), `/*` → frontend |
| `runner-job-template.yaml` | Job (reference only)                    | Documents the shape `k8s_jobs.py` creates at runtime   |


---

## 3. Cloud Shell Deployment Playbook

Work through these steps in order. All commands run in Cloud Shell against your GKE cluster.

### Step 0 — Set shell variables (do this once per session)

```bash
export PROJECT_ID="ctoaster-test-deploy"
export REGION="us-west2"
export ZONE="us-west2-a"          # used for Filestore; must be a zone inside REGION
export CLUSTER_NAME="cupcake-cluster"
export REGISTRY="us-west2-docker.pkg.dev/${PROJECT_ID}/cupcake"
export IMAGE_TAG="v2.0.0"         # increment per release
export NAMESPACE="ctoaster"
export CLOUDSQL_INSTANCE="ctoaster-test-deploy:us-west2:ctoaster-db"
```

---

### Step 1 — Authenticate & connect to the cluster

```bash
gcloud auth login
gcloud config set project ${PROJECT_ID}
gcloud container clusters get-credentials ${CLUSTER_NAME} --region ${REGION}
kubectl cluster-info
```

---

### Step 2 — Enable required GCP APIs

```bash
gcloud services enable \
  container.googleapis.com \
  file.googleapis.com \
  sqladmin.googleapis.com \
  artifactregistry.googleapis.com
```

---

### Step 3 — Create the Artifact Registry repository (if not already done)

```bash
gcloud artifacts repositories create cupcake \
  --repository-format=docker \
  --location=${REGION} \
  --description="Carrotcake container images"
```

---

### Step 4 — Build and push the backend image

We use **Cloud Build** instead of local `docker build`/`docker push` because Cloud Shell's Docker daemon often cannot reach Artifact Registry (connection-refused errors). Cloud Build runs on Google's infrastructure and pushes directly.

The `Dockerfile` contains `COPY MODELS /ctoaster.carrotcake-jobs/MODELS`. That directory must exist in the build context even if it is empty, because the Fortran binary is **not** compiled during the image build — it is compiled later inside the running pod (Step 14). The scons build is intentionally commented out in the Dockerfile so the image build remains fast; the compiled binary lives on the Filestore, not baked into the image.

```bash
cd ~/ctoaster.carrotcake

# The COPY MODELS line in the Dockerfile requires this directory to exist.
mkdir -p MODELS

# Build and push via Cloud Build (~15–25 minutes; netCDF compiles from source).
gcloud builds submit \
  --tag ${REGISTRY}/ctoaster-backend:${IMAGE_TAG} \
  .
```

Wait for `STATUS: SUCCESS` at the end of the output. The same image is used for **both** the API pods and the runner Job pods.

> **Note:** If the push fails with `Permission 'artifactregistry.repositories.uploadArtifacts' denied`, grant the Cloud Build service account write access:

```bash
> PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')
> gcloud projects add-iam-policy-binding ${PROJECT_ID} \
>   --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
>   --role="roles/artifactregistry.writer"
> 

```

---

### Step 5 — Build and push the frontend image

```bash
cd ~/cupcake-frontend

gcloud builds submit \
  --tag ${REGISTRY}/cupcake-frontend:${IMAGE_TAG} \
  .
```

---

### Step 6 — Create the Cloud SQL Postgres instance

```bash
# Creates a public-IP instance. The Cloud SQL Auth Proxy sidecar in the API
# pod handles encrypted auth, so a public IP is safe and avoids the need for
# VPC peering / private services access setup.
gcloud sql instances create ctoaster-db \
  --database-version=POSTGRES_15 \
  --tier=db-g1-small \
  --region=${REGION}

gcloud sql databases create ctoaster --instance=ctoaster-db

gcloud sql users create ctoaster_user \
  --instance=ctoaster-db \
  --password='Geronemo#101'
```

Note the **instance connection name**:

```bash
gcloud sql instances describe ctoaster-db --format="value(connectionName)"
# prints: ctoaster-test-deploy:us-west2:ctoaster-db
```

---

### Step 7 — Create a Cloud SQL service account key

The Cloud SQL Auth Proxy sidecar needs credentials to connect to Cloud SQL. We create
a dedicated GCP service account, grant it the Cloud SQL Client role, and download a
JSON key file that will be mounted into the proxy sidecar.

```bash
# Create the service account
gcloud iam service-accounts create ctoaster-api-sa \
  --display-name="Carrotcake API Service Account"

# Grant it Cloud SQL Client role
gcloud projects add-iam-policy-binding ${PROJECT_ID} \
  --member="serviceAccount:ctoaster-api-sa@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/cloudsql.client"

# Download a JSON key file for the service account
gcloud iam service-accounts keys create ~/cloudsql-sa-key.json \
  --iam-account=ctoaster-api-sa@${PROJECT_ID}.iam.gserviceaccount.com
```

This key file will be uploaded to the cluster as a Kubernetes Secret in Step 12 and
mounted into the Cloud SQL Auth Proxy sidecar container.

---

### Step 8 — Enable the Filestore CSI driver on the cluster

```bash
gcloud container clusters update ${CLUSTER_NAME} \
  --update-addons=GcpFilestoreCsiDriver=ENABLED \
  --region=${REGION}
```

---

### Step 9 — Create the Filestore instance

```bash
gcloud filestore instances create ctoaster-filestore \
  --zone=${ZONE} \
  --tier=BASIC_HDD \
  --file-share=name="ctoaster_data",capacity=1TB \
  --network=name="default"

# Note the NFS IP address
gcloud filestore instances describe ctoaster-filestore \
  --zone=${ZONE} \
  --format="value(networks[0].ipAddresses[0])"
```

The Filestore IP is `10.184.99.242`. This has already been set in `[k8s/base/filestore-pvc.yaml](k8s/base/filestore-pvc.yaml)`.

---

### Step 10 — Substitute image tags and instance names in YAML files

Run this from the `ctoaster.carrotcake/` directory to generate deploy-ready copies:

```bash
# CLOUDSQL_INSTANCE was already exported in Step 0

# Substitute placeholders in api-deployment.yaml
sed -i \
  -e "s|<IMAGE_TAG>|${IMAGE_TAG}|g" \
  -e "s|<PROJECT_ID>:<REGION>:<INSTANCE>|${CLOUDSQL_INSTANCE}|g" \
  k8s/base/api-deployment.yaml

# Substitute in frontend-deployment.yaml
sed -i "s|<IMAGE_TAG>|${IMAGE_TAG}|g" k8s/base/frontend-deployment.yaml

# Substitute in runner-job-template.yaml (reference doc only, but keep it accurate)
sed -i "s|<IMAGE_TAG>|${IMAGE_TAG}|g" k8s/base/runner-job-template.yaml
```

---

### Step 11 — Install the Nginx Ingress Controller

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm install ingress-nginx ingress-nginx/ingress-nginx \
  --namespace ingress-nginx --create-namespace \
  --set controller.service.type=LoadBalancer

# Wait for the external IP (takes 1–2 minutes)
kubectl -n ingress-nginx get svc ingress-nginx-controller --watch
```

Note the `EXTERNAL-IP` — this is the public address for the whole application.

# 34.118.227.11

---

### Step 12 — Apply manifests in order

```bash
cd ctoaster.carrotcake

# 1. Namespace
kubectl apply -f k8s/base/namespace.yaml

# 2. RBAC (ServiceAccount must exist before secrets reference it)
kubectl apply -f k8s/base/api-rbac.yaml

# 3. App secrets (create manually — never commit plaintext secrets to git)
kubectl -n ${NAMESPACE} create secret generic ctoaster-secrets \
  --from-literal=db-url="postgresql://ctoaster_user:Geronemo#101@127.0.0.1:5432/ctoaster" \
  --from-literal=jwt-secret="$(openssl rand -hex 32)"

# 4. Cloud SQL Auth Proxy key secret (from the key file downloaded in Step 7)
kubectl -n ${NAMESPACE} create secret generic cloudsql-sa-key \
  --from-file=key.json=$HOME/cloudsql-sa-key.json

# 5. Storage
kubectl apply -f k8s/base/filestore-pvc.yaml

# 6. Deployments
kubectl apply -f k8s/base/api-deployment.yaml
kubectl apply -f k8s/base/frontend-deployment.yaml

# 7. Services
kubectl apply -f k8s/base/api-service.yaml
kubectl apply -f k8s/base/frontend-service.yaml

# 8. Ingress
kubectl apply -f k8s/base/ingress.yaml

# 9. HPAs — SKIP on small clusters (3× e2-medium); they scale to 2 replicas
#    and starve the cluster. Apply only on production clusters with larger nodes.
# kubectl apply -f k8s/base/api-hpa.yaml
# kubectl apply -f k8s/base/frontend-hpa.yaml
```

---

### Step 12b — Deploy standalone Cloud SQL proxy Service

Runner Job pods cannot use the API's Cloud SQL proxy sidecar (it listens on `127.0.0.1` inside the API pod only). A standalone proxy Deployment + Service allows runner pods to reach Cloud SQL at `cloudsql-proxy:5432`.

```bash
# Create the proxy deployment
kubectl -n ${NAMESPACE} create deployment cloudsql-proxy \
  --image=gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.11.4 \
  --dry-run=client -o yaml | kubectl apply -f -

# Patch it with the correct args, SA key mount, and instance name
kubectl -n ${NAMESPACE} patch deployment cloudsql-proxy --type=json -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/args","value":["--structured-logs","--port=5432","--address=0.0.0.0","--credentials-file=/secrets/cloudsql/key.json","'"${CLOUDSQL_INSTANCE}"'"]},
  {"op":"add","path":"/spec/template/spec/containers/0/volumeMounts","value":[{"name":"cloudsql-sa-key","mountPath":"/secrets/cloudsql","readOnly":true}]},
  {"op":"add","path":"/spec/template/spec/volumes","value":[{"name":"cloudsql-sa-key","secret":{"secretName":"cloudsql-sa-key"}}]}
]'

# Expose as a ClusterIP service
kubectl -n ${NAMESPACE} expose deployment cloudsql-proxy \
  --port=5432 --target-port=5432

# Verify it's running
sleep 15
kubectl -n ${NAMESPACE} get pods -l app=cloudsql-proxy
```

The `k8s_jobs.py` code auto-derives the runner's DB URL by replacing `127.0.0.1` with `cloudsql-proxy` in the API's `DB_URL`. No additional configuration is needed.

Also set the Filestore PVC name env var on the API deployment:

```bash
kubectl -n ${NAMESPACE} set env deploy/ctoaster-api \
  FILESTORE_PVC_NAME=ctoaster-filestore-pvc
```

---

### Step 13 — Discover the application URL and verify the deployment

The public URL for the application is the **Ingress ADDRESS**, not necessarily the
Nginx controller service's `EXTERNAL-IP` (they can differ if GKE assigns them
separately). Run both commands below and test each IP until you find the one that
returns `{"ok":true}`:

```bash
# Option A — Nginx controller service IP (usually correct with ingressClassName: nginx)
NGINX_IP=$(kubectl -n ingress-nginx get svc ingress-nginx-controller \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Nginx controller IP: ${NGINX_IP}"

# Option B — Ingress ADDRESS (GKE may assign a different IP via the GCE LB)
INGRESS_IP=$(kubectl -n ${NAMESPACE} get ingress ctoaster-ingress \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Ingress ADDRESS IP:  ${INGRESS_IP}"

# Test both — the one that returns {"ok":true} is your working URL
echo "--- Testing Nginx IP ---"
curl -s --max-time 5 http://${NGINX_IP}/api/healthz || echo "(no response)"

echo "--- Testing Ingress IP ---"
curl -s --max-time 5 http://${INGRESS_IP}/api/healthz || echo "(no response)"
```

Whichever IP returns `{"ok":true}` is the one to use. Save it:

```bash
export APP_IP="<the working IP from above>"
```

Now verify the full stack:

```bash
# All pods in Running state
kubectl -n ${NAMESPACE} get pods

# API health check
curl http://${APP_IP}/api/healthz
# Expected: {"ok":true}

# Frontend (should return HTML)
curl -sL http://${APP_IP}/ | head -5

# Open in browser
echo "Open http://${APP_IP}/ in your browser"
```

**Important:** If you see a white screen or stale content in the browser, do a hard
refresh (Cmd+Shift+R on Mac, Ctrl+Shift+R on Windows) to clear any cached assets.
If that doesn't help, clear the site's localStorage via DevTools → Application →
Storage → Clear site data.

---

### Step 14 — Compile the Fortran model and install it to the Filestore

The runner pods look for the compiled `carrotcake.exe` on the Filestore at:

```
/ctoaster-filestore/MODELS/DEVELOPMENT/LINUX/ship/carrotcake.exe
```

The binary is **not** compiled during the Docker image build (the `scons` line is commented out in the Dockerfile). Instead, you run the scons build once inside a running API pod — which already has all the build tools (`gfortran`, `netCDF`, `scons`) installed — and then copy the output to the Filestore volume.

You only need to do this **once per cluster** (or whenever the Fortran source changes). The binary persists on the Filestore across pod restarts.

```bash
# Open a shell in the running API container
kubectl -n ${NAMESPACE} exec -it deploy/ctoaster-api -c api -- bash
```

Inside the pod, run:

```bash
set -euo pipefail

echo "== sanity: scons exists =="
scons --version

echo "== sanity: repo dir =="
cd /ctoaster.carrotcake

echo "== compile (uses all available cores) =="
scons -j"$(nproc)"

echo "== locate build output =="
ls -lah build | head -n 50

echo "== install to Filestore =="
DEST=/ctoaster-filestore/MODELS/DEVELOPMENT/LINUX/ship
mkdir -p "$DEST"

if [ -f build/carrotcake.exe ]; then
  cp -v build/carrotcake.exe "$DEST/carrotcake.exe"
else
  echo "ERROR: build/carrotcake.exe not found. Candidate executables:"
  find build -maxdepth 2 \( -name "*.exe" -o -perm -111 -type f \) | sed -n "1,80p"
  exit 2
fi

echo "== verify =="
ls -lah "$DEST/carrotcake.exe"
exit
```

After `exit`, confirm the binary is present on the Filestore by reading it from a second pod (or re-exec the same one):

```bash
kubectl -n ${NAMESPACE} exec -it deploy/ctoaster-api -c api -- \
  ls -lah /ctoaster-filestore/MODELS/DEVELOPMENT/LINUX/ship/carrotcake.exe
```

**Re-compiling after source changes**: repeat the `kubectl exec` block above. The Filestore copy will be overwritten in place and all subsequent runner pods will pick up the new binary.

---

### Step 15 — Point ctoaster.org at the new Ingress

The Nginx Ingress Controller from Step 11 is the single public entry point for the entire V2 deployment. These steps migrate traffic from any existing VM-level Nginx or old LoadBalancer to it.

#### 15a — Get the Nginx LoadBalancer IP

```bash
kubectl -n ingress-nginx get svc ingress-nginx-controller \
  --output jsonpath='{.status.loadBalancer.ingress[0].ip}'
# example output: 34.102.x.y
```

#### 15b — Update the DNS A record for ctoaster.org

In your DNS provider (Google Cloud DNS, Cloudflare, etc.):

- Change the `A` record for `ctoaster.org` (and `www.ctoaster.org` if it exists) to point to the IP from 15a.
- TTL: set to 60 seconds during the cut-over so you can roll back quickly; increase to 300 or higher once stable.

If you are using **Google Cloud DNS**:

```bash
NGINX_IP=$(kubectl -n ingress-nginx get svc ingress-nginx-controller \
  --output jsonpath='{.status.loadBalancer.ingress[0].ip}')

# Replace the existing A record
gcloud dns record-sets update ctoaster.org. \
  --type=A \
  --ttl=60 \
  --rrdatas="${NGINX_IP}" \
  --zone=<YOUR_CLOUD_DNS_ZONE_NAME>
```

#### 15c — Update the Ingress host field

Open `[k8s/base/ingress.yaml](k8s/base/ingress.yaml)` and change the empty `host:` to your real domain:

```yaml
spec:
  rules:
    - host: "ctoaster.org"   # was empty string
      http:
        ...
```

Re-apply:

```bash
kubectl apply -f k8s/base/ingress.yaml
```

#### 15d — Enable HTTPS with cert-manager (recommended)

Install cert-manager to obtain a free Let's Encrypt TLS certificate automatically:

```bash
helm repo add jetstack https://charts.jetstack.io
helm repo update
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --set installCRDs=true
```

Create a ClusterIssuer for Let's Encrypt:

```bash
kubectl apply -f - <<'EOF'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: <YOUR_EMAIL>
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
EOF
```

Then add the TLS block and annotation to `[k8s/base/ingress.yaml](k8s/base/ingress.yaml)`:

```yaml
metadata:
  annotations:
    kubernetes.io/ingress.class: "nginx"
    cert-manager.io/cluster-issuer: "letsencrypt-prod"   # add this line
    ...
spec:
  tls:
    - hosts:
        - ctoaster.org
      secretName: ctoaster-org-tls     # cert-manager creates this secret
  rules:
    - host: "ctoaster.org"
      ...
```

Apply and watch the certificate issue (takes ~60 seconds):

```bash
kubectl apply -f k8s/base/ingress.yaml
kubectl get certificate -n ctoaster --watch
# READY column should become True
```

Once `READY=True`, `https://ctoaster.org` is live and HTTP traffic is automatically redirected to HTTPS by the Nginx controller.

#### 15e — Remove or disable the old VM-level Nginx proxy (if any)

If a GCE VM was previously running Nginx as a reverse proxy pointing to the old GKE LoadBalancer IP, that VM no longer needs to forward traffic. After confirming DNS has propagated and HTTPS is working:

1. Remove the old proxy `server {}` block from the VM's `/etc/nginx/sites-enabled/` config.
2. Run `sudo nginx -t && sudo systemctl reload nginx` on the VM, or stop/delete the VM entirely if it served no other purpose.
3. Delete the old GKE LoadBalancer `Service` objects (the ones with `type: LoadBalancer` in the archived manifests) to avoid unnecessary GCP charges.

---

### Step 16 — Archive old root-level manifests

Once you have verified the new deployment is healthy, the old manifests in both repos are superseded and can be removed from the active branch:

- `ctoaster.carrotcake/deployment.yaml`
- `ctoaster.carrotcake/service.yaml`
- `ctoaster.carrotcake/horizontal-pod-autoscaler.yaml`
- `ctoaster.carrotcake/pvc-jobs.yaml`
- `cupcake-frontend/deployment.yaml`
- `cupcake-frontend/service.yaml`
- `cupcake-frontend/horizontal_pod_autoscaler.yaml`

---

## 4. Environment Variables Reference


| Variable                         | Where it is set               | Used by               | Example value                                             |
| -------------------------------- | ----------------------------- | --------------------- | --------------------------------------------------------- |
| `DB_URL`                         | K8s Secret `ctoaster-secrets` | API pods              | `postgresql://ctoaster_user:pass@127.0.0.1:5432/ctoaster` |
| `RUNNER_DB_URL`                  | API Deployment env (optional) | `k8s_jobs.py`         | Auto-derived: replaces `127.0.0.1` → `cloudsql-proxy`     |
| `CTOASTER_JWT_SECRET`            | K8s Secret `ctoaster-secrets` | API pods              | `<openssl rand -hex 32>`                                  |
| `FILESTORE_ROOT`                 | Deployment env                | API pods, runner pods | `/ctoaster-filestore`                                     |
| `FILESTORE_PVC_NAME`             | API Deployment env            | `k8s_jobs.py`         | `ctoaster-filestore-pvc`                                  |
| `RUNNER_IMAGE`                   | Deployment env                | `k8s_jobs.py`         | `us-west2-docker.pkg.dev/.../ctoaster-backend:v2.0.0`     |
| `K8S_NAMESPACE`                  | Deployment env                | `k8s_jobs.py`         | `ctoaster`                                                |
| `RUN_ID`                         | Injected by `k8s_jobs.py`     | `runner.py`           | `a3f9c12d8b4e...`                                         |
| `JOB_ID`                         | Injected by `k8s_jobs.py`     | `runner.py`           | `42`                                                      |
| `USER_ID`                        | Injected by `k8s_jobs.py`     | `runner.py`           | `7`                                                       |
| `JOB_NAME`                       | Injected by `k8s_jobs.py`     | `runner.py`           | `my-experiment-01`                                        |
| `CTOASTER_SYNC_INTERVAL_SECONDS` | Deployment env (optional)     | `runner.py`           | `2.0`                                                     |
| `CTOASTER_VERSION`               | Deployment env (optional)     | `storage.py`          | `DEVELOPMENT`                                             |
| `CTOASTER_PLATFORM`              | Deployment env (optional)     | `storage.py`          | `LINUX`                                                   |


---

## 5. Filestore Directory Layout

```
/ctoaster-filestore/
  MODELS/
    DEVELOPMENT/
      LINUX/
        ship/
          carrotcake.exe          ← pre-built Fortran binary
  jobs/
    <user_id>/
      <job_name>/
        config/                   ← job configuration files
        data_genie/               ← GENIE data files
        owner.json                ← {"user_id": N, "email": "..."}
        status                    ← plain text: RUNNABLE / RUNNING / COMPLETE / ...
        run.log                   ← live Fortran stdout (synced every 2s)
        command                   ← ephemeral: PAUSE or CANCEL signal
        output/
          biogem/                 ← NetCDF output files
```

---

## 6. What Happens When a User Clicks "Run"

```mermaid
sequenceDiagram
    participant Browser
    participant API as API Pod (FastAPI)
    participant DB as Cloud SQL
    participant K8s as Kubernetes API
    participant Runner as Runner Job Pod
    participant FS as Filestore

    Browser->>API: POST /api/run-job {job_name}
    API->>FS: Verify job folder exists
    API->>DB: upsert_job_record(); check no active run
    API->>K8s: batch/v1 create Job (ctoaster-runner-<run_id[:12]>)
    API->>DB: create_run(run_id, k8s_job_name, status=PENDING)
    API-->>Browser: {run_id, k8s_job_name, message}

    K8s->>Runner: Schedule pod, inject env vars
    Runner->>DB: update_run status=RUNNING
    Runner->>FS: stage_job_to_workspace() → copy to /workspace
    Runner->>Runner: Popen carrotcake.exe
    loop Every 2 seconds
        Runner->>FS: sync_to_shared() workspace → Filestore
        Runner->>DB: update heartbeat_at; check desired_state
    end
    Runner->>FS: Final sync_to_shared()
    Runner->>DB: update_run status=COMPLETE/FAILED/PAUSED
```



---

## 7. Local Development Flow (post-refactor)

```bash
# Terminal 1 — backend
cd ctoaster.carrotcake
DB_URL=sqlite:///./dev.db \
FILESTORE_ROOT=./ctoaster.carrotcake-jobs \
CTOASTER_JWT_SECRET=dev-secret \
  uvicorn tools.REST:app --reload --port 8000

# Terminal 2 — frontend
cd cupcake-frontend
REACT_APP_API_URL=http://localhost:8000 npm start
```

When running locally, `K8S_NAMESPACE` is not set so `k8s_jobs.py` will raise `NotImplementedError` (the API returns HTTP 501). Science runs must be triggered manually or via a local mock.

---

## 8. Deployment Lessons & Corrections

These items were discovered during the first GKE deployment and differ from the original plan:

### Infrastructure

1. **Standalone Cloud SQL proxy Service** — Runner pods cannot use the API's sidecar proxy. A standalone `cloudsql-proxy` Deployment + Service is required (Step 12b).
2. **PVC name** — Filestore PVC is `ctoaster-filestore-pvc`. Set `FILESTORE_PVC_NAME` env var on the API deployment.
3. **Ingress** — `spec.ingressClassName: nginx` is required (annotation alone is deprecated). Frontend catch-all path must use regex `/()(.*)`  with capture group for `rewrite-target: /$2` to work.
4. **HPA** — On small clusters, disable HPAs or set `minReplicas: 1` / `maxReplicas: 1`.

### Code fixes applied

1. `**tools/utils.py` — `available_versions()`** — Docker image has no `.git`; fixed with try/except fallback returning `["DEVELOPMENT"]`.
2. `**tools/db.py` — `_postgres_lastval()`** — `RealDictCursor` returns dicts, not tuples; fixed to `SELECT lastval() AS id`.
3. **Frontend `.env`** — Inline comments get baked into values by dotenv; comment must be on its own line.
4. **Frontend URLs** — `REACT_APP_API_URL=/api`. Components using `api` axios instance must NOT prepend `apiUrl` (avoids `/api/api/jobs`). Components using `fetchEventSource` directly DO need `apiUrl`.
5. **Runner resources** — Reduced to `200m` CPU / `256Mi` memory requests for small clusters.
6. `**tools/REST.py` — `_ensure_owner()`** — Falls back to path-based ownership check if `owner.json` is missing.
7. `**tools/k8s_jobs.py` — `imagePullPolicy`** — Changed to `IfNotPresent` (saves 15-30s per run).

---

## 9. Performance Optimisation Roadmap

The K8s Job architecture introduces a cold-start delay per run (pod scheduling + image pull + Python init + file staging). Apply these once the system is verified working.

### Already done


| Change                                | Impact                                                       |
| ------------------------------------- | ------------------------------------------------------------ |
| `imagePullPolicy: IfNotPresent`       | **-15–30s** per run (image cached after first pull per node) |
| Runner CPU requests reduced to `200m` | Faster scheduling on constrained clusters                    |


### To do — university production cluster


| Change                         | Impact                         | How                                                                                 |
| ------------------------------ | ------------------------------ | ----------------------------------------------------------------------------------- |
| Larger nodes (`e2-standard-4`) | **-5–10s** scheduling          | `gcloud container node-pools update`                                                |
| Cluster autoscaler             | Eliminates Pending             | `gcloud container clusters update --enable-autoscaling --min-nodes=2 --max-nodes=6` |
| Pre-pull image via DaemonSet   | **-10–20s** first run per node | DaemonSet init container pulls image at deploy time                                 |
| Reduce sync interval to 1s     | ~1s fresher logs               | Set `CTOASTER_SYNC_INTERVAL_SECONDS=1`                                              |


### Expected delay after optimisations

- **First run on cold node**: ~8–12s
- **Subsequent runs**: ~3–5s
- **Log appearance**: ~1–2s after model starts writing

