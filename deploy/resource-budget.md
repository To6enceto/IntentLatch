# Resource budget

Snapshot: 3 October 2026, FRA1, from `kubectl describe nodes`. All three workers
have **3890m CPU** and approximately **6414Mi (6.26Gi)** allocatable memory.
This is lower than the brief's approximate 6.5Gi. Requests below are scheduling
reservations; CPU limits can exceed allocatable in aggregate and then throttle.

## Workloads

Values are per replica. “General” means either of the two workers without the
Ollama role. The taint plus required node affinity keeps ordinary workloads off
the dedicated worker. Gateway replicas spread across the two general workers.

| Workload | Replicas | CPU request / limit | Memory request / limit | Placement |
| --- | ---: | ---: | ---: | --- |
| Ollama | 1 | 2000m / 3000m | 4096Mi / 4608Mi | `3x1f37` only |
| Gateway | 2 | 250m / 1000m | 256Mi / 512Mi | One per general worker |
| PostgreSQL | 1 | 250m / 1000m | 512Mi / 1024Mi | General |
| Valkey | 1 | 100m / 500m | 128Mi / 256Mi | General |
| Open WebUI | 1 | 250m / 1000m | 512Mi / 1536Mi | General |
| Prometheus | 1 | 200m / 1000m | 768Mi / 1536Mi | General |
| Prometheus config reloader | 1 | 10m / 100m | 32Mi / 64Mi | With Prometheus |
| Alertmanager | 1 | 25m / 100m | 64Mi / 128Mi | General |
| Alertmanager config reloader | 1 | 10m / 100m | 32Mi / 64Mi | With Alertmanager |
| Grafana | 1 | 100m / 500m | 256Mi / 512Mi | General |
| Grafana dashboard sidecar | 1 | 20m / 100m | 64Mi / 128Mi | With Grafana |
| Prometheus operator | 1 | 50m / 300m | 64Mi / 128Mi | General |
| kube-state-metrics | 1 | 30m / 200m | 64Mi / 128Mi | General |
| node-exporter | 3 | 20m / 100m | 32Mi / 64Mi | Every worker, Ollama toleration |
| Sealed Secrets | 1 | 25m / 200m | 64Mi / 128Mi | General |
| Envoy data plane | 2 | 100m / 500m | 128Mi / 256Mi | General, spread preferred |
| Envoy shutdown manager | 2 | 10m / unbounded | 32Mi / unbounded | With each Envoy proxy |
| Envoy Gateway controller | 1 | 100m / 500m | 128Mi / 256Mi | General |
| cert-manager controller | 1 | 25m / 200m | 64Mi / 128Mi | General |
| cert-manager cainjector | 1 | 25m / 200m | 64Mi / 128Mi | General |
| cert-manager webhook | 1 | 25m / 100m | 32Mi / 64Mi | General |
| Model pull/warm-up Job, temporary | 1 | 50m / 250m | 64Mi / 128Mi | General |
| Envoy certificate generation Job, temporary | 1 | 10m / 100m | 32Mi / 64Mi | General via taint exclusion |
| cert-manager startup check, temporary | 1 | 10m / 100m | 32Mi / 64Mi | General via taint exclusion |
| HTTP-01 solver, temporary | Up to 3 | 10m / 100m | 16Mi / 32Mi | General via taint exclusion |
| NetworkPolicy smoke pod, temporary | 1 | 10m / 100m | 16Mi / 32Mi | General via taint exclusion |

No CNPG, Loki, Tempo, Argo CD, GPU runtime, or other model is included. The
availability rules and three JSON dashboards do not create extra workload pods.

## Existing system reservations

| System workload | Placement | CPU request / configured limit | Memory request / configured limit |
| --- | --- | ---: | ---: |
| Cilium | Every worker | 310m / 1000m | 332Mi / 1024Mi |
| DigitalOcean node agent | Every worker | 102m / unbounded | 80Mi / 300Mi |
| CoreDNS | One on `3x1f3m`, one on `3x1f3q` | 100m / unbounded each | 170Mi / 170Mi each |
| CSI node plugin, telemetry reloader | Every worker | No declared requests/limits | No declared requests/limits |
| Hubble relay/UI and two konnectivity agents | Currently `3x1f3q` | No declared requests/limits | No declared requests/limits |

Pods with no requests still consume real resources. The arithmetic below sums
declared values and leaves headroom; it does not treat those processes as free.
Use `kubectl top nodes` and `kubectl top pods -A` after real model inference.

## Feasible per-node packing, including the public edge

One example packing uses `3x1f3m` for PostgreSQL, Prometheus/reloader,
kube-state-metrics, Envoy controller, cert-manager's three controllers, and one
gateway/Envoy replica. `3x1f3q` takes Valkey, WebUI, Grafana/sidecar,
Alertmanager/reloader, Prometheus operator, Sealed Secrets, and the other
gateway/Envoy replica. Both also include their current system pods and
node-exporter. The Ollama worker includes its current system pods and exporter.

| Worker suffix | CPU requests / allocatable | Memory requests / allocatable | Sum of configured CPU limits | Sum of configured memory limits |
| --- | ---: | ---: | ---: | ---: |
| `3x1f37` | 2432m / 3890m | 4540Mi / 6414Mi | 4100m + unbounded pods | 5996Mi + unbounded pods |
| `3x1f3m` | 1557m / 3890m | 2694Mi / 6414Mi | 5900m + unbounded pods | 5654Mi + unbounded pods |
| `3x1f3q` | 1472m / 3890m | 2214Mi / 6414Mi | 5400m + unbounded pods | 5206Mi + unbounded pods |
| Total | 5461m / 11670m | 9448Mi / 19242Mi | CPU is overcommitted | 16856Mi + unbounded pods |

This is a feasible packing, not a promise of exact scheduler decisions. Apart
from dedicated Ollama placement and gateway spread, general workloads remain
movable. The public edge was enabled on 4 October 2026. Envoy Gateway
adds a shutdown-manager sidecar to each proxy; its default requests are
included above, and it has no configured limits.

Live installation on 4 October required raising Grafana from 256Mi to 512Mi and
its dashboard sidecar from 64Mi to 128Mi after both hit their initial memory
limits. The table includes these measured startup corrections.

Confirm actual per-node placement and reservations after installation:

```bash
kubectl get pods -A -o wide
kubectl describe nodes
kubectl top nodes
kubectl top pods -A --sort-by=memory
```

Without the public edge, subtract 395m CPU / 608Mi memory in total requests and
2000m CPU / 1088Mi in configured limits. The temporary model Job adds 50m/64Mi
requests and 250m/128Mi limits on a general worker while downloads are open.
CPU limit overcommit can cause throttling when many workloads peak together;
the demo should use one simultaneous inference request.

The original 6Gi Ollama limit would leave only about 270Mi of allocatable memory
before Cilium and other system processes. With current system configured limits
and node-exporter it would sum to **7532Mi**, exceeding the node's 6414Mi. The
cloud profile therefore caps Ollama at **4608Mi**. That leaves about 418Mi even
if all configured memory limits on that worker are reached, plus the kubelet's
reservation already excluded from allocatable. This still needs measurement of
the unbounded CSI/telemetry processes and both resident models. Run the model
warm-up and smoke checks; if Ollama OOMs, increase worker RAM or explicitly
reconsider the two-resident-model requirement. Do not silently restore 6Gi.

If actual demand is too high: keep Loki and Tempo omitted, keep the stock
Grafana dashboards/renderer disabled, then reduce gateway replicas to one.
That last cut removes 250m/256Mi of requests and 1000m/512Mi of limits, but loses
gateway redundancy and its one-replica PDB blocks voluntary drain. It does not
solve an Ollama worker memory shortage.

Cloud PVCs total 24Gi: Ollama 10, PostgreSQL 5, Valkey 1, WebUI 2, Prometheus 5,
Grafana 1. `do-block-storage` was verified with reclaim policy `Delete`.
The selected k3d profile requests roughly 5Gi before system overhead; provision
about 10Gi Docker memory for its limits, sidecars, image startup, and model load.
