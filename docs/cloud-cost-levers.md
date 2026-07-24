# Cloud cost / usage dial-down reference

Inventory of every enhancement that consumes GCP resources, what it costs, and
the exact knob to dial it down — so usage can be cut selectively (e.g. on an ITS
request) based on what's actually needed. Costs are rough us-west2 monthly
estimates; verify against the billing console before quoting.

Key distinction:
- **Standing cost** = paid even when the platform is idle (nodes at `min`,
  Filestore, Cloud SQL). These are what actually reduce the bill.
- **Ceiling** = autoscaler `max` / HPA `max`. Costs nothing unless load uses it,
  so lowering it caps a spike but saves nothing at idle.

GCP project `ucr-ursa-major-ridgwell-lab`, region `us-west2`, cluster
`cupcake-cluster`, namespace `ctoaster`. Prefix gcloud/kubectl with
`export PATH="$HOME/google-cloud-sdk/bin:$PATH"`.

---

## Standing costs (cut these to actually save money at idle)

### 1. Filestore — `ctoaster-filestore` 1 TiB BASIC_HDD  (~$200/mo) — biggest standing cost
- Fixed: BASIC_HDD minimum is 1 TB, so capacity can't be shrunk in place.
- Reduce real usage by deleting unused users/jobs/published snapshots (admin
  panel "Delete User"; published snapshots live under Filestore `PUBLISHED/<id>`).
- Deep cut (only if drastically downsizing): migrate job data off Filestore to a
  cheaper store — non-trivial, not a quick lever.

### 2. Runner pool warm node — `runner-pool-c2` c2-standard-4, min 1  (~$170/mo standing)
- Drop the warm node (idle → $0, at the cost of a ~1–3 min cold start on the
  first run after idle):
  ```
  gcloud container node-pools update runner-pool-c2 --cluster cupcake-cluster \
    --region us-west2 --min-nodes 0 --max-nodes 6
  ```
- Cheaper machine (slower per-core): recreate the pool as `n2-standard-4`
  (~$140/mo/node) or `e2-standard-4` (~$100/mo/node). c2 was chosen for fastest
  single-thread (matches Andy's laptop target); downgrade if speed isn't needed.

### 3. API/frontend pool — `default-pool-v2` e2-medium, min 2  (~$48/mo standing for 2 nodes)
- Hosts api + frontend + cloudsql-proxy + system pods. `min 2` is driven by the
  HPAs below (api min 2 + frontend min 2 don't fit on one e2-medium).
- To get to 1 standing node: lower the HPA mins (next item) **and**
  `--min-nodes 1`. e2-medium is already the cheapest tier.

### 4. Cloud SQL — `ctoaster-db` db-g1-small  (~$30/mo)
- Downsize for very low usage:
  ```
  gcloud sql instances patch ctoaster-db --tier db-f1-micro   # ~$8/mo, brief restart
  ```
- Connections are short-lived, so f1-micro is fine for a handful of users.

---

## Ceilings (lower to cap spikes; ~no idle saving)

### 5. Node autoscaler maxes
- `default-pool-v2` max 24 (raised for the 25–30 student class) → now overkill:
  ```
  gcloud container node-pools update default-pool-v2 --cluster cupcake-cluster \
    --region us-west2 --min-nodes 2 --max-nodes 6
  ```
- `runner-pool-c2` max 6 → lower if fewer concurrent runs are expected.

### 6. HPAs — `ctoaster-api-hpa` (2→10), `ctoaster-frontend-hpa` (2→5)
- For a few users, drop mins to 1 (also lets default-pool reach min 1):
  ```
  kubectl -n ctoaster patch hpa ctoaster-api-hpa --type merge -p '{"spec":{"minReplicas":1,"maxReplicas":4}}'
  kubectl -n ctoaster patch hpa ctoaster-frontend-hpa --type merge -p '{"spec":{"minReplicas":1,"maxReplicas":3}}'
  ```

---

## Other enhancements (negligible cost, listed for completeness)

- **Prepull DaemonSet** `ctoaster-runner-prepull` — 1 tiny idle pod/node (10m CPU
  / 32Mi) that keeps the runner image warm. Negligible cost. Remove if desired:
  `kubectl -n ctoaster delete ds ctoaster-runner-prepull` (first run on a new node
  then pays a one-time image pull). Keep its tag in sync with `RUNNER_IMAGE`.
- **Runner CPU request 1000m** (k8s_jobs.py) — scheduling only, no direct cost;
  governs how many runs pack per node.
- **NFS `actimeo=1`** on the Filestore PV — correctness/latency tuning, no cost.
- **`-O3` build** — pure speed, no cost.

---

## Suggested order if asked to cut
1. `runner-pool-c2` → min 0 (saves ~$170/mo; only downside is cold starts).
2. Cloud SQL → db-f1-micro (~$22/mo).
3. HPA mins → 1 + default-pool min 1 (~$24/mo).
4. Lower autoscaler maxes (spike protection; no idle saving).
5. Filestore: delete unused data; capacity itself is fixed at 1 TB.

---

## Pre-warm before a big class (the opposite: spend a little to avoid cold starts)
Day-to-day the API HPA sits at min 3. Before a known ~25-30 person session,
pre-warm so the spike doesn't start from cold, then restore afterwards. The API
HPA max is 8 and `pods x DB_POOL_MAX(6)` must stay under Cloud SQL
max_connections(100), so don't raise min above ~8.

```
# ~30 min before the class:
kubectl -n ctoaster patch hpa ctoaster-api-hpa --type merge -p '{"spec":{"minReplicas":6}}'
gcloud container node-pools update runner-pool-c2 --cluster cupcake-cluster \
  --region us-west2 --min-nodes 2 --max-nodes 6      # a couple warm c2 runner nodes

# after the class:
kubectl -n ctoaster patch hpa ctoaster-api-hpa --type merge -p '{"spec":{"minReplicas":3}}'
gcloud container node-pools update runner-pool-c2 --cluster cupcake-cluster \
  --region us-west2 --min-nodes 1 --max-nodes 6
```
