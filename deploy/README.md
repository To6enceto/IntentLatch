# IntentLatch deployment

This directory contains the Docker image definition, one application Helm chart,
pinned operator values, staged install commands, and smoke tests. `gateway/` is
unchanged. Run commands below from the repository root unless stated otherwise.

The FRA1 installation was completed on 4 October 2026. See
[live validation and login instructions](live-validation.md) for the tested
state, localhost access, and remaining limits.

The live gateway image remains pinned to source commit `b1b0289`, recorded in
`image-build.json`. Later gateway policy features pulled into this repository
have not yet been built and deployed; the live reports describe the pinned image.

For an explanation of every component, Kubernetes resource type, communication
path, and technology, read the [cluster architecture guide](cluster-guide.md).
Its [resource inventory](cluster-resource-inventory.csv) lists the individual
installation objects and their purposes from the live cluster snapshot.

## Confirmed choices

- FRA1, three `s-4vcpu-8gb` workers. Default Ollama worker:
  `pool-d5e2gdbur-3x1f37`. Override `OLLAMA_NODE` if it changes.
- Plain PostgreSQL 17 StatefulSet. No CloudNativePG operator or CNPG resources.
- Public image under `ghcr.io/antonstwork/intentlatch-gateway`, as subsequently
  authorized after GHCR denied package creation under `to6enceto`.
- Plain Helm. No Argo CD, Loki, Tempo, or Discord webhook.
- No domain yet. Initial installation uses localhost port-forwards and creates
  **zero** load balancers. Enabling the prepared edge creates exactly one.
- Alertmanager receives the local availability/budget rules with a null receiver;
  it sends no external notifications.

## 1. Build and publish the gateway

```bash
export IMAGE_TAG="$(git rev-parse HEAD)"
docker build --platform linux/amd64 \
  -f deploy/docker/gateway.Dockerfile \
  -t "ghcr.io/antonstwork/intentlatch-gateway:$IMAGE_TAG" .
docker login ghcr.io --username antonstwork
docker push "ghcr.io/antonstwork/intentlatch-gateway:$IMAGE_TAG"
```

Equivalent targets: `make -C deploy build publish`. The build context is the
repository root. The Dockerfile-specific ignore file includes only the gateway
package/source, excluding `.env`, kubeconfigs, Git metadata, and tests. The
Python 3.12.15 image uses UID/GID 10001, installs `./gateway` without test extras,
and starts the required `uvicorn intentlatch.main:app` command on port 8080.
The application wheel includes its SQL migrations.

New GHCR packages start private. While signed in as `antonstwork`, open
[Package settings](https://github.com/users/antonstwork/packages/container/intentlatch-gateway/settings).
At the bottom, choose **Danger Zone**, **Change visibility**, then **Public**,
and confirm with the package name `intentlatch-gateway`. Alternatively, open
[the package](https://github.com/users/antonstwork/packages/container/package/intentlatch-gateway)
and find **Package settings** in the right-hand sidebar. Verify anonymous access:

```bash
make -C deploy check-image
```

There is no pull secret in the selected public-image configuration. The install
preflight stops before cluster changes when the image is not anonymously pullable.
The image built and pushed for this checkout is recorded in `image-build.json`,
including its registry digest and platform.
Python dependencies currently have lower bounds in the gateway project. Tags
identify the source commit, but rebuilding that commit later can resolve newer
dependencies; preserve the published image digest for an exact replay.

## 2. Application chart

| File in `helm/intentlatch/templates/` | Purpose |
| --- | --- |
| `namespaces.yaml` | Five dedicated application/observability namespaces, owned by the Helm release |
| `gateway.yaml` | Two replicas, ClusterIP service, PDB, spread across general workers, Ollama anti-affinity, hardened container, HTTP readiness and TCP liveness |
| `postgres.yaml` | One PostgreSQL instance, headless identity service, client service, 5Gi RWO PVC, owner bootstrap SQL |
| `valkey.yaml` | Password-protected StatefulSet, AOF on a 1Gi PVC, bounded cache memory |
| `ollama.yaml` | Dedicated worker placement, 10Gi model PVC, internal service, optional pull/warm-up Job |
| `webui.yaml` | One Open WebUI replica, 2Gi PVC, login enabled, gateway-only OpenAI connection |
| `network-policies.yaml` | Four namespace default denies, DNS and explicit application flows, temporary download permissions, Grafana ingress restriction |
| `sealed-secrets.yaml` | Strict name/namespace-bound ciphertext supplied through a generated values file |
| `monitoring.yaml` | Gateway ServiceMonitor, availability/budget rules, three dashboard ConfigMaps |
| `edge.yaml` | EnvoyProxy, GatewayClass, Gateway, routes, body limit, timeouts, staging/production issuers, certificate |

`values.yaml` is the cloud profile. `values-local.yaml` overrides storage,
placement, and resources for k3d. `values-secrets.example.yaml` documents every
secret key with intentionally empty ciphertext. It is not an installable secret
file. `make secrets` generates the real overlay without writing plaintext.

The PostgreSQL application role owns `intentlatch` and can create tables without
being a superuser. Its separate superuser credential is not injected into the
gateway. The database URL points to
`postgres.intentlatch-data.svc.cluster.local:5432/intentlatch`. CNPG's `-rw`
service naming does not apply to this selected StatefulSet deployment.
Two gateway replicas need up to 22 connections, or 33 during a one-pod rolling
surge, within PostgreSQL's default 100-connection limit.

The gateway's Valkey URL is prepared in `intentlatch-system/gateway-valkey`, key
`url`, but is not injected. Future environment names remain empty placeholders
in `gateway.future`. No missing gateway behavior is implemented in infrastructure.

## 3. Pinned third-party components

| Component | Chart version | Application version |
| --- | --- | --- |
| Upstream Sealed Secrets | 2.20.0 | 0.40.0 |
| kube-prometheus-stack | 91.9.0 | Prometheus operator 0.94.1 |
| Envoy Gateway | v1.9.2 | v1.9.2 |
| cert-manager | v1.21.2 | v1.21.2 |

Versions are recorded in `third-party/versions.env`. `manage.py` uses these
versions and the adjacent values files. Equivalent Helm commands are:

```bash
helm upgrade --install sealed-secrets sealed-secrets \
  --repo https://bitnami.github.io/sealed-secrets --version 2.20.0 \
  -n sealed-secrets --create-namespace -f deploy/third-party/sealed-secrets.yaml --wait
helm upgrade --install monitoring kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts --version 91.9.0 \
  -n observability -f deploy/third-party/monitoring.yaml --wait
helm upgrade --install eg oci://docker.io/envoyproxy/gateway-helm --version v1.9.2 \
  -n envoy-gateway-system --create-namespace -f deploy/third-party/envoy-gateway.yaml --wait
helm upgrade --install cert-manager cert-manager \
  --repo https://charts.jetstack.io --version v1.21.2 \
  -n cert-manager --create-namespace -f deploy/third-party/cert-manager.yaml --wait
```

Prefer the Make targets for lifecycle operations: they record created CRDs for
teardown. The commands above show the exact upstream inputs. Operators must
precede application resources that use their CRDs. Envoy installs compatible
Gateway API CRDs before cert-manager starts. cert-manager explicitly sets
`config.enableGatewayAPI: true`.

Sealed Secrets is the requested open-source controller, published upstream as
`ghcr.io/bitnami/sealed-secrets-controller:0.40.0` with its own community chart.
This interprets the brief's Bitnami prohibition as referring to the retired
application catalog. All application images and the monitoring stack use other
upstreams. A blanket ban on even the Sealed Secrets project would conflict with
the explicit requirement to use it.

The pinned monitoring chart renders Grafana 13.2.3-distroless, Prometheus
v3.15.0-distroless, Alertmanager v0.34.1, kube-state-metrics v2.20.0,
node-exporter v1.12.1-distroless, and dashboard sidecar 2.11.2. The operator's
config-reloader uses v0.94.1. Unused admission webhooks, managed-control-plane
scrapes, stock dashboards, and the Grafana renderer are disabled. Prometheus
retains two days within a 4GB retention bound on its 5Gi volume; Grafana has 1Gi.

## 4. Install in stages with verification

Prerequisites: Docker, Helm 3, kubectl, Python 3, and this cluster's kubeconfig.
`make tools` installs a pinned kubeseal into ignored `deploy/.state/bin`, checking
the release asset's SHA256 from GitHub. It does not install into system paths.

```bash
export KUBECONFIG="$PWD/../hackyeah-cluster-kubeconfig.yaml"
export KUBE_CONTEXT=do-fra1-hackyeah-cluster
export IMAGE_TAG="$(git rev-parse HEAD)"
kubectl --context "$KUBE_CONTEXT" get nodes
```

`make -C deploy install` runs the following private install sequence and API
smoke checks. It does not enable public access. For inspection between steps:

1. **Cluster prep.** Read [the resource budget](resource-budget.md), then:
   ```bash
   make -C deploy cluster-prep
   kubectl describe node pool-d5e2gdbur-3x1f37
   ```
   The target labels and taints that worker. It refuses to use a worker with
   non-DaemonSet workloads already on it, since `NoSchedule` does not evict them.
   A DOKS replacement loses manually applied labels/taints. Re-establish placement
   on a replacement before restarting Ollama; do not give ordinary apps this
   toleration. node-exporter has it, and existing DOKS DaemonSets already run on
   every worker.
2. **Sealed Secrets, namespace skeleton, and credentials.**
   ```bash
   make -C deploy tools sealed-secrets namespaces secrets
   kubectl -n sealed-secrets rollout status deployment/sealed-secrets
   kubectl get sealedsecrets -A
   ```
   The namespace skeleton installs default denies immediately, avoiding an open
   window during the later workload stages. Keep `.state/cloud/inventory.json`
   and `.state/cloud/sealed-values.json`; they contain context metadata and
   ciphertext, not raw credentials. Subsequent runs reuse credentials.
3. **PostgreSQL.** The selected StatefulSet replaces the brief's CNPG step.
   ```bash
   make -C deploy postgres
   kubectl -n intentlatch-data rollout status statefulset/postgres
   kubectl -n intentlatch-data exec postgres-0 -- \
     psql -U postgres -d postgres -Atc \
     "SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname='intentlatch'"
   ```
   Expected owner: `intentlatch`. Bootstrap SQL runs only on an empty volume.
   Changing a Secret alone does not rotate an initialized PostgreSQL password.
   No backups are configured for this event.
4. **Valkey.**
   ```bash
   make -C deploy valkey
   kubectl -n intentlatch-data exec valkey-0 -- valkey-cli ping
   ```
   Expected `PONG`; `REDISCLI_AUTH` is already injected into the pod. This is the
   variable read by the pinned 8.1.10 CLI, even though current 9.x docs use
   `VALKEYCLI_AUTH`.
5. **Ollama and both models.**
   ```bash
   make -C deploy ollama models
   kubectl -n upstreams exec deployment/ollama -- ollama list
   kubectl -n upstreams exec deployment/ollama -- ollama ps
   kubectl -n upstreams get networkpolicies
   ```
   `make models` opens the temporary pull permissions, pulls and warms exactly
   `qwen2.5:3b` and `llama3.2:1b`, then closes the permissions in a `finally`
   block. Run it again to rerun the Job. If the process is forcibly killed or
   the API server is unavailable during cleanup, explicitly close the window:
   ```bash
   helm upgrade intentlatch deploy/helm/intentlatch -n default \
     --reuse-values --set ollama.pull.enabled=false
   ```
6. **Gateway image, replicas, and identity bootstrap.** Publish the image using
   section 1, then:
   ```bash
   make -C deploy check-image gateway bootstrap
   kubectl -n intentlatch-system rollout status deployment/intentlatch-gateway
   ```
   Bootstrap uses a localhost-only port-forward on 8080, creates `judges` and
   `open-webui`, and seals the returned token without printing it or persisting
   it in plaintext. A valid existing token is reused. An invalid existing one
   is reissued only when explicitly running `make bootstrap`.
7. **Verify NetworkPolicies.** They have been enforced since namespace creation.
   ```bash
   kubectl get networkpolicies -n intentlatch-system
   kubectl get networkpolicies -n agents
   kubectl get networkpolicies -n upstreams
   ```
   The smoke test below proves that a pod with Open WebUI's permitted agent
   labels can reach the gateway but times out reaching Ollama by DNS and by IP.
8. **Open WebUI.**
   ```bash
   make -C deploy webui
   kubectl -n agents rollout status deployment/open-webui
   kubectl -n agents port-forward --address 127.0.0.1 svc/open-webui 3000:8080
   ```
   `make install` also runs `make bootstrap-webui` after the smoke checks. It
   creates `admin@intentlatch.example` and `judge@intentlatch.example`, seals
   separate generated passwords into `agents/webui-accounts`, disables signup
   in Helm, grants the judge read access to both corporate models, and verifies
   both through a judge login. For staged installs,
   run `make -C deploy bootstrap-webui` after WebUI is ready. The script reuses
   existing credentials and refuses to replace an unrelated administrator.

   Bootstrap also disables automatic built-in tools for the two corporate
   models. WebUI v0.11.4 otherwise adds thousands of tool-description tokens
   to browser chats, which can exhaust the gateway's 180-second timeout on CPU.
   Newly configured models default to 256 output tokens; existing explicit
   token limits are preserved. Change this in the model's advanced parameters
   when longer answers are needed. Helm disables automatic titles, tags,
   follow-up suggestions, and memory so these tasks do not compete with chat.
   After changing these settings, refresh the browser and start a new chat.
   The current gateway buffers the complete answer before replaying SSE, so
   text appears when generation finishes. `corporate-b` is the faster 1B model.

   Retrieve the judge password in your own terminal, outside recordings:
   ```bash
   kubectl -n agents get secret webui-accounts -o jsonpath='{.data.judge-password}' | base64 --decode; echo
   ```
   Use `admin-password` instead of `judge-password` for the administrator.
   Open `http://localhost:3000` and log in with the selected account. The
   initial `DEFAULT_USER_ROLE=pending` prevents new signups receiving access
   without administrator approval. All judge prompts use the one gateway
   `open-webui` employee identity; per-judge gateway identity is not implemented.
9. **Observability.**
   ```bash
   make -C deploy monitoring
   kubectl -n observability get pods,servicemonitor,prometheusrule,pvc
   kubectl -n observability port-forward --address 127.0.0.1 svc/monitoring-grafana 3001:80
   ```
   Grafana login is `admin` with `observability/grafana-auth` key
   `admin-password`. Retrieve it locally with your secret-management workflow;
   do not paste it into Git or terminal recordings. Anonymous access is disabled.
   Gateway `/metrics` currently returns 404, so a DOWN scrape target and empty
   dashboards are expected. Availability rules use kube-state-metrics and do
   not depend on the missing endpoint. Budget rules become useful when metrics
   land. The latency panel assumes a histogram; confirm that type with the
   gateway owner.
10. **Envoy and cert-manager, later when a domain exists.** Follow section 6.
    Verify controllers, Gateway conditions, route attachment, and certificate.
11. **DNS, later.** Create the wildcard A record described in section 6.
    Verify `dig +short api.YOUR_DOMAIN` returns the load balancer IP.
12. **Smoke test.**
    ```bash
    make -C deploy smoke
    # After public DNS/TLS is ready:
    make -C deploy smoke SMOKE_ARGS='--domain YOUR_DOMAIN --webui-email judge@example.com'
    ```
    The optional WebUI login test prompts for the password without echoing it.
    Without `--webui-email`, HTML loading is tested and judge login/prompt is
    explicitly reported as untested. In a browser, log in as a judge, choose
    each corporate model, and check that both answer. No domain means the
    public-edge checks are explicitly skipped.

After installation, `make -C deploy access` holds all three localhost forwards
open: chat at `http://localhost:3000`, Grafana at `http://localhost:3001`, and
the private gateway at `http://localhost:8080`. Disconnected forwards reconnect
automatically. Ctrl+C closes them together. Run the target again after a laptop
restart or if the access process is stopped.

## 5. Sealing and isolation details

The sealing script uses the exact equivalent of `secrets.token_urlsafe(32)` for
every generated credential. One in-memory database password feeds both the
PostgreSQL Secret and the separately sealed gateway database URL. PostgreSQL,
Valkey, gateway signing/admin credentials, WebUI session key and employee token,
and Grafana credentials are all separate strict-scope SealedSecrets.

For a manual seal, use stdin, never `--from-literal` with a secret in argv. This
example replaces only the employee token. Run in a private, unrecorded terminal:

```bash
export PATH="$PWD/deploy/.state/bin:$PATH"
kubeseal --controller-name sealed-secrets --controller-namespace sealed-secrets \
  --fetch-cert > /tmp/intentlatch-sealing-public.pem
python3 -c 'import getpass,sys; sys.stdout.write(getpass.getpass("Employee token: "))' \
  | kubectl -n agents create secret generic webui-employee \
      --from-file=OPENAI_API_KEY=/dev/stdin --dry-run=client -o json \
  | kubeseal --cert /tmp/intentlatch-sealing-public.pem --scope strict --format yaml
```

Persist only the resulting `spec.encryptedData` into the matching `employee`
entry in `.state/cloud/sealed-values.json`, apply through Helm, and restart
Open WebUI. `make bootstrap` automates this. Do not apply a manual SealedSecret
and leave different ciphertext in Helm values, since the next upgrade would
restore the old value. Cloud and local clusters have different sealing keys.
Back up controller keys outside Git if you need recovery; `make destroy`
intentionally removes the controller and its keys.

All four core namespaces deny ingress and egress by default. Namespace and pod
selectors in one peer are combined, preventing an `agents` pod from gaining
gateway access simply by copying a gateway label. People allowed to edit
NetworkPolicies, create pods in `intentlatch-system`, or port-forward are
trusted administrators; namespace labels alone are not an RBAC boundary.

The steady-state model endpoint accepts only gateway pods. During maintenance,
only `upstreams` pods labeled `ollama-pull` get the additional path. Ollama's
temporary egress allows public IPv4 HTTPS on 443 plus cluster DNS; standard
NetworkPolicy cannot restrict that HTTPS permission by registry hostname.
After pulling, DNS remains and Internet HTTPS is closed. Image pulls occur
through kubelet/containerd and do not need pod egress permissions.

Operator namespaces have no default deny. Sealed Secrets accesses the Kubernetes
API to create Secrets, rather than connecting to application pods. cert-manager
needs Kubernetes API, ACME HTTPS, DNS, and its admission webhook; its challenge
pods are placed with the edge certificate in `envoy-gateway-system`, where Envoy
can reach them. Prometheus needs API/service discovery and node/kubelet scrapes;
Grafana's ingress policy also allows its metrics scrape from Prometheus pods.

## 6. Enable the public edge after choosing a domain

Copy `deploy/site-values.example.yaml` to `deploy/site-values.yaml`, replace
the domain/email, and keep `webui.enableSignup: false`. Configure the first
WebUI administrator and judge accounts over the private port-forward first.

```bash
make -C deploy edge
kubectl -n envoy-gateway-system get svc \
  -l gateway.envoyproxy.io/owning-gateway-name=intentlatch -w
```

At your chosen DNS provider, open the domain's DNS records and add:

| Type | Name/host | Value | TTL |
| --- | --- | --- | --- |
| A | `*` | Envoy Service's external IPv4 address | 300 seconds |

Use DNS-only mode if the provider also offers an HTTP proxy/CDN. An intermediary
proxy could impose its own shorter request timeout. Remove conflicting wildcard
or host-specific A/AAAA records for these names. DNS does not need to move to
DigitalOcean. Do not create a wildcard TLS certificate: the single certificate
has explicit SANs for `api`, `chat`, and `grafana`; HTTP-01 cannot validate a
wildcard certificate. `console` is reserved by the DNS wildcard and has no route.

```bash
dig +short api.YOUR_DOMAIN
kubectl -n envoy-gateway-system get gateway intentlatch
kubectl get httproutes -A
kubectl -n envoy-gateway-system wait certificate/intentlatch-edge --for=condition=Ready --timeout=10m
make -C deploy smoke SMOKE_ARGS='--domain YOUR_DOMAIN --staging'
```

Staging certificates are deliberately untrusted. `--staging` permits that
certificate only for the explicit public smoke checks. After staging succeeds,
change `edge.issuer` in the site file to `letsencrypt-production`, rerun
`make edge`, wait for the certificate, and rerun smoke without `--staging`.
Confirm the Certificate's Ready condition corresponds to its current generation.

Envoy Gateway v1.9.2's `BackendTrafficPolicy.spec.requestBuffer.limit: 1Mi`
rejects oversized API bodies with 413. It buffers the finite JSON request;
SSE/NDJSON response replay remains supported. The chat route has no request
buffer policy, preserving its WebSocket upgrades. API and chat route request
and backend timeouts are 360 seconds, with 360-second downstream idle/request
timeouts. Open WebUI's AIOHTTP total and stream-idle timeouts are also 360 seconds.
The DigitalOcean load balancer forwards TCP; TLS and HTTP handling stay in Envoy.
Only `/v1`, `/api`, exact `/authority`, and exact `/healthz` are routed on the
API hostname. `/admin`, `/metrics`, and API documentation paths have no route.
Admins continue to use `kubectl port-forward`.

## 7. k3d fallback

Allow roughly 10GiB Docker memory and enough disk for the container images plus
model storage. The resource profile is smaller, but the model weights remain the
same. The 4Gi local Ollama limit must pass the two-resident-model smoke check.
Pre-pull everything before the venue loses connectivity.

```bash
make -C deploy PROFILE=local local-cluster
export KUBECONFIG="$HOME/.kube/config"
export KUBE_CONTEXT=k3d-intentlatch
make -C deploy PROFILE=local build image-import
make -C deploy PROFILE=local install
```

k3d is pinned to v5.9.0 and k3s to v1.35.9-k3s1. The cluster has one server and
two agents, with Traefik and ServiceLB disabled and no k3d load balancer. k3s's
network policy controller remains enabled. Cloud node labels/taints, public
routes, and TLS are disabled. PVCs use `local-path`. The same port-forwards and
NetworkPolicy proof apply. Local volumes are tied to their local node and are
lost with the k3d cluster. The local deployment uses independently sealed secrets.

## 8. Validation and teardown

```bash
python3 -m venv deploy/.state/validation-venv
deploy/.state/validation-venv/bin/pip install -r deploy/validation-requirements.txt
deploy/.state/validation-venv/bin/python deploy/scripts/validate.py --fetch
```

Validation renders cloud, local, and temporary-pull variants, evaluates allowed
and denied network paths, checks probes/image pins/routes, and validates custom
resources against the pinned charts' CRD schemas. Kubernetes CEL admission
rules and real CNI behavior still require a live installation. `smoke.py` uses
real Ollama, checks JSON and buffered SSE for both models, two-line NDJSON,
token introspection, model tags, model residency, isolation, and optional public
routes/body limits. Upstream HTTP failures include `X-Request-ID` and the error
body, with known credentials redacted. `max_completion_tokens`, `tool_choice`,
and Ollama model-name mismatches are gateway issues to report, not fix here.

Remove the installed system and its data with one command:

```bash
make -C deploy destroy
# For the separate local deployment:
make -C deploy PROFILE=local destroy
```

Teardown verifies the recorded cluster UID/context, removes the Gateway while
Envoy is still running, waits for the load-balancer Service's deletion, removes
operator-managed workloads before operators, uninstalls the Helm releases,
deletes the dedicated namespaces/PVCs, waits for the recorded PVs to disappear,
removes CRDs installed by the driver, the operator-created kubelet Service and
endpoints when recorded as new, and restores the node label/taint it added.
It preserves CRDs that predated the installation and refuses to remove a CRD
that still has instances. No finalizers are forcibly removed.

After the event, **delete the DOKS cluster itself** in DigitalOcean and check
**Networking > Load Balancers**, **Volumes**, and **Billing** for remaining
charges. Remove the wildcard DNS record. `destroy` intentionally leaves the
existing cluster and the reusable GHCR image; the original task asks for a
cluster-deletion reminder. For local Docker cleanup also run
`deploy/.state/bin/k3d cluster delete intentlatch`. The event's $30 budget depends
on runtime and actual cloud charges; do not leave worker nodes running afterward.

## Verified interfaces and remaining limits

- [Ollama v0.35.1 environment definitions](https://github.com/ollama/ollama/blob/v0.35.1/envconfig/config.go): `OLLAMA_CONTEXT_LENGTH`, max loaded models, parallelism, keep-alive, and cloud-disable settings exist. This release is well after the old path-traversal fix.
- [Open WebUI v0.11.4 configuration](https://github.com/open-webui/open-webui/blob/v0.11.4/backend/open_webui/config.py) and [runtime environment](https://github.com/open-webui/open-webui/blob/v0.11.4/backend/open_webui/env.py): connection/authentication/timeouts were checked against the tag. Disabling persistent config makes Helm environment settings win after restart. Offline mode avoids model-download attempts by auxiliary WebUI features.
- [Valkey 8.1.10 CLI source](https://github.com/valkey-io/valkey/blob/8.1.10/src/valkey-cli.c) uses `REDISCLI_AUTH`. The pinned image was tested with password enforcement, AOF enabled, and a readiness probe that fails on authentication errors. PostgreSQL's pinned image was tested with the bootstrap SQL and a non-superuser database owner creating a table.
- [Envoy request buffering](https://gateway.envoyproxy.io/docs/tasks/traffic/request-buffering/) and the pinned chart CRDs define the 1MiB policy. [cert-manager HTTP-01](https://cert-manager.io/docs/configuration/acme/http01/) documents Gateway support and challenge routing.
- [DigitalOcean load-balancer configuration](https://docs.digitalocean.com/products/kubernetes/how-to/configure-load-balancers/) documents the Service annotations and TCP forwarding.
- Gateway startup failure code 3, package imports, migrations in the wheel, and the existing 50 gateway tests were checked in the built image. The source uses Python's default `StreamHandler`, which writes application logs to stderr; Uvicorn also produces access logs. The brief's stdout-only, one-line-per-request logging description therefore needs confirmation from the gateway owner. Kubernetes collects both streams.
- A one-instance database, RWO WebUI/Valkey volumes, and a single Ollama worker are event tradeoffs. The smaller Ollama memory limits and real-model API compatibility require live smoke validation. No backups or automatic node-role repair are configured.
