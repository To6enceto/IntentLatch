# Live deployment validation

Verified on 4 October 2026 in `do-fra1-hackyeah-cluster`, FRA1. The deployment
is installed and running. `gateway/` source is unchanged.

## Access

Local port-forwards were started in the background on this laptop:

| Interface | URL | Login |
| --- | --- | --- |
| Chat | http://localhost:3000 | `judge@intentlatch.example` |
| Chat administration | http://localhost:3000 | `admin@intentlatch.example` |
| Grafana | http://localhost:3001 | `admin` |
| Private gateway | http://localhost:8080 | Employee/admin credentials according to endpoint |

Passwords remain in Kubernetes Secrets backed by Sealed Secrets. Retrieve a
password in your own terminal, outside recordings:

```bash
export KUBECONFIG=/home/antonstankov/hackyeah/hackyeah-cluster-kubeconfig.yaml
# Judge login:
kubectl -n agents get secret webui-accounts -o jsonpath='{.data.judge-password}' | base64 --decode; echo
# WebUI administrator:
kubectl -n agents get secret webui-accounts -o jsonpath='{.data.admin-password}' | base64 --decode; echo
# Grafana administrator:
kubectl -n observability get secret grafana-auth -o jsonpath='{.data.admin-password}' | base64 --decode; echo
```

Network disconnects and pod replacements trigger automatic reconnection.
After a laptop restart or if the access process is stopped, run
`make -C deploy access` from the repository root to restore the forwards.
The background process PID and log are in ignored `.state/cloud/access.json`
and `.state/cloud/access.log`.

## Passed checks

- Public GHCR manifest, configuration, and all seven layers are anonymously
  accessible; the registry digest matches `image-build.json`.
- All 15 workload pods are ready, with zero restarts in the current pods.
  Six block-storage PVCs are bound, totalling 24Gi. No worker reports memory,
  disk, or PID pressure.
- Two gateway replicas run on different general workers. PostgreSQL's
  application database is owned by `intentlatch`; authenticated Valkey responds.
- Real gateway inference passes for `corporate-a` and `corporate-b`, including
  JSON and buffered SSE with `[DONE]`. Ollama-compatible NDJSON, model aliases,
  health, and authority introspection pass.
- Both Qwen 2.5 3B and Llama 3.2 1B remain resident on CPU with 4096-token
  contexts. Ollama's measured working set is approximately 3717Mi against a
  4608Mi limit. Node working sets were 4652Mi, 3046Mi, and 1374Mi for worker
  suffixes `3x1f37`, `3x1f3m`, and `3x1f3q`; these are snapshots, not peak bounds.
- A pod with the WebUI agent labels reaches the gateway but times out reaching
  Ollama by DNS and Service IP. The temporary pull Job and Internet permissions
  are removed; only gateway pods have steady-state model access.
- WebUI admin and judge logins work. The judge has explicit read grants for
  both corporate models, and prompts through WebUI return answers from both.
  Login remains enabled and signup is disabled in Helm.
- Grafana authentication, all three provisioned dashboards, and the Prometheus
  data source pass. All infrastructure scrape targets are up, and no configured
  availability alert is firing.
- Cloud/local chart rendering, image/resource/probe checks, network-policy
  checks, and 26 custom resources against the pinned CRD schemas pass. The
  existing 50 gateway tests passed in the published image during image validation.
- Teardown ownership was exercised with mocked cluster calls. Actual teardown
  was not run, so the deployed system and its data remain available.

## Corrections made during installation

- Disabled the Prometheus operator's separate TLS setting along with its
  admission webhook, removing a dependency on an absent certificate Secret.
- Raised Grafana's memory limit to 512Mi and its dashboard sidecar to 128Mi
  after startup OOMs; the resource budget includes the revised requests/limits.
- Allowed Prometheus pods to scrape Grafana through its ingress policy.
- Added explicit WebUI model grants required by version 0.11.4 for ordinary
  users, plus repeatable sealed login bootstrap.
- Recorded the operator-created kubelet Service outside the application
  namespaces so teardown removes it only when this installation created it.

## Browser chat incident, 4 October 2026

Reviewed the recent logs from all running application, monitoring, operator,
and system containers. The database, node agents, and running pods were healthy.
The gateway recorded a 180-second upstream timeout at 01:04 Sofia time
(`adf57eef-625b-47db-84e4-15c82610032e`). Several other requests took 90-176 seconds.

A real Chrome browser test reproduced the long blank response. WebUI v0.11.4
automatically adds built-in tool definitions only for browser requests with a
session ID. The short test prompt expanded to 4192 tokens, was truncated to
2050, and spent about 114 seconds evaluating its prompt on CPU. The gateway
request took 121 seconds. Earlier direct JSON API tests did not exercise that
browser-specific behavior.

Applied to the cluster and retained in the deployment files:

- Disabled automatic built-in tools for both corporate models through WebUI's
  model API, preserving their access grants and other model settings.
- Set a 256-token output default when the model has no explicit limit.
- Disabled automatic title, tag, and follow-up inference and memory in Helm.
  Existing chat history and login credentials are retained.

Fresh browser chats then rendered short Markdown responses in **4.2 seconds
for corporate-a** and **2.6 seconds for corporate-b**. Both displayed a bold
heading and bullet points, showed the completed-response controls, and retained
the answer after reloading. Neither test had a browser JavaScript error.
These are short-prompt measurements, not a guarantee for longer conversations
or concurrent users. Helm rendering and CRD validation passed after the changes.
The final application-log check found no new gateway 502 responses; all running
application and monitoring pods were ready with zero restarts.

Refresh `http://localhost:3000` and start a new chat to use the updated model
configuration. `corporate-b` uses the smaller, faster Llama 3.2 1B model.
The gateway still buffers an entire answer before replaying SSE; there is no
token-by-token display while Ollama generates. Its source was not changed.

Two separate WebUI log errors remain outside the repaired response path:

- Version 0.11.4's initial-title callback omits `model` from its context and
  logs `KeyError: 'model'`. Disabling title inference prevents the extra model
  call but does not prevent this callback. The completed browser tests show
  that this error does not block the response or its persistence.
- The offline embedding model is not cached in the data volume, so RAG
  initialization logs an error. Document retrieval is not validated or ready;
  plain corporate chat does not depend on this embedding model.

## Other remaining boundaries

There is no domain, public edge, TLS certificate, or load balancer. Public
DNS/TLS/edge checks remain unrun. The local k3d variant was rendered and
validated but was not installed on the laptop.

The gateway's two `/metrics` targets return the expected 404 until that endpoint
is implemented. Application metric panels therefore have no data yet. No
gateway code was changed to supply the missing feature. The Kubernetes Metrics
API is absent; resource measurements used kubelet summary statistics instead.

After the event, run `make -C deploy destroy` with this kubeconfig, then delete
the DOKS cluster in DigitalOcean and check Volumes, Load Balancers, and Billing.
The worker nodes and volumes continue to incur charges while retained.
