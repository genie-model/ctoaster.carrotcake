"""
tools/k8s_jobs.py
-----------------
Kubernetes Job lifecycle management for ctoaster runner pods.

Uses the official `kubernetes` Python client.
Config resolution:
  - Inside a pod  → load_incluster_config()   (production / GKE)
  - Outside a pod → load_kube_config()         (local dev with kubeconfig)

Environment variables consumed (read at call time, not at import):
  RUNNER_IMAGE        Docker image tag for the runner pod  (required)
  FILESTORE_PVC_NAME  PVC claim name mounted as Filestore   (default: ctoaster-jobs-pvc)
  FILESTORE_ROOT      Mount path of the Filestore volume    (default: /ctoaster-filestore)
  DB_URL              Postgres / SQLite connection string   (required)
  CTOASTER_JWT_SECRET JWT signing secret                   (required)
  K8S_NAMESPACE       Target namespace                      (default: default)
  CTOASTER_VERSION    Fortran model version tag             (passed through)
  CTOASTER_PLATFORM   Platform string e.g. LINUX            (passed through)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# K8s client bootstrap
# ---------------------------------------------------------------------------

def _load_k8s_config() -> None:
    """Load Kubernetes config: in-cluster first, kubeconfig fallback."""
    from kubernetes import config
    try:
        config.load_incluster_config()
        logger.debug("Loaded in-cluster Kubernetes config")
    except config.ConfigException:
        config.load_kube_config()
        logger.debug("Loaded kubeconfig (local dev)")


def _batch_api():
    from kubernetes import client
    _load_k8s_config()
    return client.BatchV1Api()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_runner_job(
    run_id: str,
    job_id: int,
    user_id: int,
    job_name: str,
    namespace: str = "default",
) -> str:
    """
    Create a Kubernetes Job that will execute the science run.

    The Job:
      - Runs `python -m tools.runner` as its entrypoint
      - Mounts the Filestore PVC at FILESTORE_ROOT (read-write)
      - Mounts an emptyDir at /workspace for local scratch space
      - Has restartPolicy=Never and backoffLimit=0 (no automatic retries)
      - Auto-cleans after 1 hour via ttlSecondsAfterFinished

    Returns the k8s_job_name (e.g. "runner-<run_id[:12]>").
    """
    from kubernetes import client

    runner_image = os.environ.get("RUNNER_IMAGE", "").strip()
    if not runner_image:
        raise ValueError(
            "RUNNER_IMAGE environment variable must be set before submitting runs. "
            "Set it to the full Docker image tag for the ctoaster backend."
        )

    pvc_name = os.environ.get("FILESTORE_PVC_NAME", "ctoaster-filestore-pvc")
    filestore_root = os.environ.get("FILESTORE_ROOT", "/ctoaster-filestore")
    db_url = os.environ.get("DB_URL", "")
    jwt_secret = os.environ.get("CTOASTER_JWT_SECRET", "changeme-in-prod")

    k8s_job_name = f"runner-{run_id[:12]}"

    # Core env vars injected into the runner pod
    env_vars = [
        client.V1EnvVar(name="RUN_ID",              value=run_id),
        client.V1EnvVar(name="JOB_ID",              value=str(job_id)),
        client.V1EnvVar(name="USER_ID",             value=str(user_id)),
        client.V1EnvVar(name="JOB_NAME",            value=job_name),
        client.V1EnvVar(name="DB_URL",              value=db_url),
        client.V1EnvVar(name="FILESTORE_ROOT",      value=filestore_root),
        client.V1EnvVar(name="CTOASTER_JWT_SECRET", value=jwt_secret),
        client.V1EnvVar(name="WORKSPACE_ROOT",      value="/workspace"),
    ]

    # Optional pass-through vars
    for name in ("CTOASTER_VERSION", "CTOASTER_PLATFORM", "CTOASTER_SYNC_INTERVAL_SECONDS"):
        val = os.environ.get(name, "").strip()
        if val:
            env_vars.append(client.V1EnvVar(name=name, value=val))

    container = client.V1Container(
        name="runner",
        image=runner_image,
        image_pull_policy="Always",
        command=["python", "-m", "tools.runner"],
        env=env_vars,
        volume_mounts=[
            client.V1VolumeMount(
                name="filestore",
                mount_path=filestore_root,
            ),
            client.V1VolumeMount(
                name="workspace",
                mount_path="/workspace",
            ),
        ],
        resources=client.V1ResourceRequirements(
            requests={"cpu": "200m", "memory": "256Mi"},
            limits={"cpu": "2000m", "memory": "4Gi"},
        ),
    )

    pod_spec = client.V1PodSpec(
        restart_policy="Never",
        containers=[container],
        volumes=[
            client.V1Volume(
                name="filestore",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=pvc_name,
                ),
            ),
            client.V1Volume(
                name="workspace",
                empty_dir=client.V1EmptyDirVolumeSource(),
            ),
        ],
    )

    job_labels = {
        "app":     "ctoaster-runner",
        "run-id":  run_id[:12],
        "user-id": str(user_id),
    }

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=k8s_job_name,
            namespace=namespace,
            labels=job_labels,
        ),
        spec=client.V1JobSpec(
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels=job_labels),
                spec=pod_spec,
            ),
            backoff_limit=0,
            # Auto-delete the Job object 1 hour after it finishes
            ttl_seconds_after_finished=3600,
        ),
    )

    _batch_api().create_namespaced_job(namespace=namespace, body=job)
    logger.info(f"Created Kubernetes Job {k8s_job_name!r} for run {run_id} (namespace={namespace})")
    return k8s_job_name


def delete_runner_job(k8s_job_name: str, namespace: str = "default") -> None:
    """
    Delete a runner Job object (and its pods via Foreground propagation).
    Silently ignores 404 (already deleted / ttl-cleaned).
    """
    from kubernetes import client
    from kubernetes.client.rest import ApiException

    try:
        _batch_api().delete_namespaced_job(
            name=k8s_job_name,
            namespace=namespace,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )
        logger.info(f"Deleted Kubernetes Job {k8s_job_name!r}")
    except ApiException as exc:
        if exc.status == 404:
            logger.debug(f"Job {k8s_job_name!r} already deleted (404)")
        else:
            logger.warning(f"Failed to delete Job {k8s_job_name!r}: {exc}")


def get_runner_job_status(
    k8s_job_name: str, namespace: str = "default"
) -> Optional[str]:
    """
    Return a simple status string for a Kubernetes Job:
      'running'   — at least one active pod
      'succeeded' — job completed successfully
      'failed'    — job has failed pods
      None        — job not found or client error
    """
    try:
        job = _batch_api().read_namespaced_job(
            name=k8s_job_name, namespace=namespace
        )
        if job.status.succeeded:
            return "succeeded"
        if job.status.failed:
            return "failed"
        return "running"
    except Exception as exc:
        logger.debug(f"Could not read job {k8s_job_name!r}: {exc}")
        return None
