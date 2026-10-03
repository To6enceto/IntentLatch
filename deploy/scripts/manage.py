#!/usr/bin/env python3
"""Small staged Helm driver with a context-bound teardown inventory."""
import argparse
import hashlib
import io
import ipaddress
import json
import os
import platform
import shutil
import sys
import tarfile
import time
import urllib.request

import yaml

from common import (APP_NAMESPACES, CHART, CONTEXT, DEPLOY, HELM, KUBE,
                    PROFILE, REPO, SEALED, STATE, app_values, atomic_json,
                    kube_json, run, upgrade)

VERSIONS = dict(line.split("=", 1) for line in (DEPLOY / "third-party" / "versions.env").read_text().splitlines() if line and not line.startswith("#"))
STATE_FILE = STATE / "inventory.json"
OP_NAMESPACES = ["sealed-secrets", "envoy-gateway-system", "cert-manager"]
COMPONENTS = {
    "sealed-secrets": ("sealed-secrets", "sealed-secrets", "sealed-secrets", "https://bitnami.github.io/sealed-secrets", "SEALED_CHART_VERSION", "sealed-secrets.yaml"),
    "monitoring": ("monitoring", "observability", "kube-prometheus-stack", "https://prometheus-community.github.io/helm-charts", "PROMETHEUS_CHART_VERSION", "monitoring.yaml"),
    "envoy": ("eg", "envoy-gateway-system", "oci://docker.io/envoyproxy/gateway-helm", None, "ENVOY_CHART_VERSION", "envoy-gateway.yaml"),
    "cert-manager": ("cert-manager", "cert-manager", "cert-manager", "https://charts.jetstack.io", "CERT_MANAGER_CHART_VERSION", "cert-manager.yaml"),
}


def inventory():
    uid = kube_json("get", "namespace", "kube-system")["metadata"]["uid"]
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        if state["cluster_uid"] != uid or state["context"] != CONTEXT:
            raise RuntimeError("Inventory belongs to a different cluster/context. Use a separate state directory; never reuse sealed ciphertext after recreating a cluster.")
        return state
    namespaces = {n["metadata"]["name"] for n in kube_json("get", "namespaces")["items"]}
    conflicts = namespaces.intersection(APP_NAMESPACES + OP_NAMESPACES)
    if conflicts:
        raise RuntimeError("Refusing to adopt pre-existing namespaces without the original inventory: " + ", ".join(sorted(conflicts)))
    state = {"cluster_uid": uid, "context": CONTEXT,
             "baseline_crds": [c["metadata"]["name"] for c in kube_json("get", "crds")["items"]],
             "created_crds": [], "node": None}
    atomic_json(STATE_FILE, state)
    return state


def prep():
    state = inventory()
    if PROFILE == "local":
        print("Local profile: no node label or taint needed.")
        return
    name = os.environ.get("OLLAMA_NODE", "pool-d5e2gdbur-3x1f37")
    node = kube_json("get", "node", name)
    role = node["metadata"].get("labels", {}).get("intentlatch.io/role")
    taints = [t for t in node["spec"].get("taints", []) if t["key"] == "intentlatch.io/role"]
    if role not in {None, "ollama"} or any(t.get("value") != "ollama" or t["effect"] != "NoSchedule" for t in taints):
        raise RuntimeError("Chosen node already has a different IntentLatch role or taint")
    if state["node"] and state["node"]["name"] != name:
        raise RuntimeError("An Ollama node is already recorded. Restore its placement before selecting a replacement.")
    pods = kube_json("get", "pods", "-A", "--field-selector", f"spec.nodeName={name}")["items"]
    unexpected = [p["metadata"]["name"] for p in pods if p["status"].get("phase") not in {"Succeeded", "Failed"}
                  and p["metadata"].get("labels", {}).get("app.kubernetes.io/name") != "ollama"
                  and not any(o["kind"] == "DaemonSet" for o in p["metadata"].get("ownerReferences", []))]
    if unexpected:
        raise RuntimeError("NoSchedule does not evict existing pods. Choose an otherwise empty worker or move these pods first: " + ", ".join(unexpected))
    if not state["node"]:
        state["node"] = {"name": name, "previous_role": role, "had_taint": bool(taints)}
        atomic_json(STATE_FILE, state)
    run([*KUBE, "label", "node", name, "intentlatch.io/role=ollama", "--overwrite"])
    run([*KUBE, "taint", "node", name, "intentlatch.io/role=ollama:NoSchedule", "--overwrite"])
    run([*KUBE, "describe", "node", name])


def fetch_tool(repo, version, asset_name, executable, archived=False):
    destination = DEPLOY / ".state" / "bin" / executable
    if destination.exists():
        return
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/tags/{version}", headers={"User-Agent": "intentlatch-deploy"})
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    asset = next(a for a in release["assets"] if a["name"] == asset_name)
    digest = asset.get("digest", "")
    if not digest.startswith("sha256:"):
        raise RuntimeError("Release API has no SHA256 for this asset; install the verified tool manually")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != digest.removeprefix("sha256:"):
        raise RuntimeError("Downloaded tool checksum mismatch")
    if archived:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            content = archive.extractfile(executable).read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    destination.chmod(0o755)


def tools():
    system = platform.system().lower()
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    if system not in {"linux", "darwin"} or arch is None:
        raise RuntimeError("Install kubeseal manually for this platform")
    version = VERSIONS["KUBESEAL_VERSION"]
    fetch_tool("bitnami/sealed-secrets", "v" + version, f"kubeseal-{version}-{system}-{arch}.tar.gz", "kubeseal", archived=True)
    if PROFILE == "local":
        fetch_tool("k3d-io/k3d", VERSIONS["K3D_VERSION"], f"k3d-{system}-{arch}", "k3d")
    print("Pinned tools available in deploy/.state/bin")


def component(name):
    state = inventory()
    if name == "monitoring" and "kubelet_service_created" not in state:
        existing = run([*KUBE, "-n", "kube-system", "get", "service", "monitoring-kubelet",
                        "--ignore-not-found=true", "-o", "name"], capture=True).stdout.strip()
        # The operator creates this outside its namespace, without an owner.
        state["kubelet_service_created"] = not bool(existing)
        atomic_json(STATE_FILE, state)
    before = {c["metadata"]["name"] for c in kube_json("get", "crds")["items"]}
    release, namespace, chart, repo, version, values = COMPONENTS[name]
    args = [*HELM, "upgrade", "--install", release, chart, "--version", VERSIONS[version],
            "-n", namespace, "--create-namespace", "-f", str(DEPLOY / "third-party" / values), "--wait", "--timeout", "15m"]
    if repo:
        args += ["--repo", repo]
    if name == "monitoring" and PROFILE == "local":
        args += ["-f", str(DEPLOY / "third-party" / "monitoring-local.yaml")]
    try:
        if name == "envoy":
            # DOKS preserves newer bundles. Apply CRDs separately so Helm does
            # not claim ownership of the provider's pre-existing definitions.
            crds = run([*HELM, "show", "crds", chart, "--version", VERSIONS[version]], capture=True).stdout
            class Loader(yaml.SafeLoader):
                pass
            Loader.add_constructor("tag:yaml.org,2002:value", Loader.construct_scalar)
            documents = [d for d in yaml.load_all(crds, Loader=Loader) if d]
            existing = {d["metadata"]["name"]: d for d in kube_json("get", "crds")["items"]}
            provider_upgrade = False
            for doc in documents:
                old = existing.get(doc["metadata"]["name"])
                if not old:
                    continue
                old_bundle = old["metadata"].get("annotations", {}).get("gateway.networking.k8s.io/bundle-version")
                new_bundle = doc["metadata"].get("annotations", {}).get("gateway.networking.k8s.io/bundle-version")
                if old_bundle and new_bundle:
                    if tuple(map(int, old_bundle.lstrip("v").split("."))) > tuple(map(int, new_bundle.lstrip("v").split("."))):
                        raise RuntimeError("Refusing to downgrade Gateway API CRDs")
                    if old_bundle != new_bundle:
                        if PROFILE != "cloud" or doc["metadata"]["name"] not in state["baseline_crds"]:
                            raise RuntimeError("Inspect the existing Gateway API CRD owner before upgrading")
                        provider_upgrade = True
                served = {v["name"] for v in doc["spec"]["versions"] if v["served"]}
                required = set(old["status"].get("storedVersions", []))
                if doc["metadata"]["name"] == "tlsroutes.gateway.networking.k8s.io":
                    required.add("v1alpha2")  # Required by DOKS Cilium.
                if not required <= served:
                    raise RuntimeError("CRD upgrade would remove required versions: " + doc["metadata"]["name"])
            backup = STATE / "gateway-crds-before.json"
            if not backup.exists():
                atomic_json(backup, [existing[d["metadata"]["name"]] for d in documents if d["metadata"]["name"] in existing])
            apply_args = [*KUBE, "apply", "--server-side", "--field-manager=intentlatch-edge", "-f", "-"]
            if provider_upgrade:
                # Transfer the validated older DOKS bundle's schema fields.
                # DOKS detects the newer bundle and stops reconciling it.
                apply_args += ["--force-conflicts"]
            run([*apply_args, "--dry-run=server"], data=crds)
            run(apply_args, data=crds)
            args += ["--set", "crds.enabled=false"]
        run(args)
    finally:
        after = {c["metadata"]["name"] for c in kube_json("get", "crds")["items"]}
        state["created_crds"] = sorted(set(state["created_crds"]) | (after - before))
        atomic_json(STATE_FILE, state)


def namespaces():
    inventory()
    existing = run([*HELM, "status", "intentlatch", "-n", "default"], capture=True, check=False)
    if existing.returncode == 0:
        print("Application release already exists; retaining its staged settings.")
        return
    upgrade("gateway.enabled=false", "postgres.enabled=false", "valkey.enabled=false", "ollama.enabled=false",
            "webui.enabled=false", "monitoring.enabled=false", "edge.enabled=false", "ollama.pull.enabled=false", initial=True)


def secrets():
    inventory()
    run([sys.executable, DEPLOY / "scripts" / "seal-secrets.py"])
    upgrade()
    for secret in json.loads(SEALED.read_text())["sealedSecrets"].values():
        run([*KUBE, "-n", secret["namespace"], "wait", "sealedsecret/" + secret["name"], "--for=condition=Synced", "--timeout=120s"])


def stage(name):
    inventory()
    if name == "gateway":
        sha = os.environ.get("IMAGE_TAG") or run(["git", "-C", REPO, "rev-parse", "HEAD"], capture=True).stdout.strip()
        upgrade("gateway.enabled=true", "gateway.tag=" + sha)
    else:
        upgrade(name + ".enabled=true")


def models():
    inventory()
    run([*KUBE, "-n", "upstreams", "delete", "job", "ollama-pull", "--ignore-not-found=true", "--wait=true"])
    try:
        upgrade("ollama.pull.enabled=true")
        run([*KUBE, "-n", "upstreams", "wait", "--for=condition=complete", "job/ollama-pull", "--timeout=3600s"])
        run([*KUBE, "-n", "upstreams", "logs", "job/ollama-pull", "--tail=30"])
    finally:
        # This removes the Job and both its access and Ollama's HTTPS egress.
        upgrade("ollama.pull.enabled=false")


def monitoring():
    component("monitoring")
    upgrade("monitoring.enabled=true")


def edge():
    inventory()
    site = DEPLOY / "site-values.yaml"
    if not site.exists():
        raise RuntimeError("Create deploy/site-values.yaml using a domain or edge.mode=ip, with webui.enableSignup=false")
    config = yaml.safe_load(site.read_text())
    ip_mode = config.get("edge", {}).get("mode") == "ip"
    if ip_mode and config["edge"].get("publicIP"):
        ipaddress.IPv4Address(config["edge"]["publicIP"])
    run([*HELM, "template", "intentlatch", CHART, *app_values(), "--set", "edge.enabled=true"], capture=True)
    component("envoy")
    component("cert-manager")
    run([*HELM, "upgrade", "intentlatch", CHART, "-n", "default", "--reuse-values", "-f", site,
         "--set", "edge.enabled=true", "--wait", "--timeout", "15m"])
    if ip_mode:
        deadline = time.monotonic() + 900
        public_ip = None
        while time.monotonic() < deadline:
            services = kube_json("-n", "envoy-gateway-system", "get", "services", "-l", "gateway.envoyproxy.io/owning-gateway-name=intentlatch")["items"]
            addresses = [a["ip"] for s in services for a in s.get("status", {}).get("loadBalancer", {}).get("ingress", [])
                         if a.get("ip") and ipaddress.ip_address(a["ip"]).version == 4]
            if addresses:
                public_ip = str(ipaddress.ip_address(addresses[0]))
                break
            print("Waiting for the DigitalOcean load balancer IP...", flush=True)
            time.sleep(10)
        if not public_ip:
            raise RuntimeError("Load balancer IP not allocated within 15 minutes")
        if config["edge"].get("publicIP") != public_ip:
            config["edge"]["publicIP"] = public_ip
            temporary = site.with_suffix(".yaml.tmp")
            temporary.write_text(yaml.safe_dump(config, sort_keys=False))
            temporary.replace(site)
            run([*HELM, "upgrade", "intentlatch", CHART, "-n", "default", "--reuse-values", "-f", site,
                 "--set", "edge.enabled=true", "--wait", "--timeout", "15m"])
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            certificate = kube_json("-n", "envoy-gateway-system", "get", "certificate", "intentlatch-edge")
            if any(c["type"] == "Ready" and c["status"] == "True" and c.get("observedGeneration") == certificate["metadata"]["generation"]
                   for c in certificate.get("status", {}).get("conditions", [])):
                break
            print("Waiting for the current certificate generation...", flush=True)
            time.sleep(10)
        else:
            raise RuntimeError("Certificate was not issued within 15 minutes; inspect Orders and Challenges")
        print(f"Chat https://{public_ip}/ | Grafana https://{public_ip}:8443/ | API https://{public_ip}:9443/")
        issuer = config["edge"].get("issuer", "letsencrypt-staging")
        print("Issuer: " + issuer)
        if issuer == "letsencrypt-staging":
            print("Staging certificates are not browser-trusted; switch to letsencrypt-production after validation.")
        return
    print("Create your wildcard DNS A record after the load balancer IP appears:")
    run([*KUBE, "-n", "envoy-gateway-system", "get", "gateway", "intentlatch"])
    run([*KUBE, "-n", "envoy-gateway-system", "get", "services"])


def assert_public_image():
    tag = os.environ.get("IMAGE_TAG") or run(["git", "-C", REPO, "rev-parse", "HEAD"], capture=True).stdout.strip()
    repo = "antonstwork/intentlatch-gateway"
    try:
        with urllib.request.urlopen(f"https://ghcr.io/token?scope=repository:{repo}:pull&service=ghcr.io", timeout=30) as response:
            token = json.load(response)["token"]
        request = urllib.request.Request(f"https://ghcr.io/v2/{repo}/manifests/{tag}", method="HEAD",
                                         headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.v2+json"})
        urllib.request.urlopen(request, timeout=30).close()
    except Exception as error:
        raise RuntimeError("Gateway image is not anonymously pullable. Run make publish and have the package owner set GHCR visibility to Public before install.") from error


def install():
    if PROFILE == "cloud":
        assert_public_image()
    tools()
    prep()
    component("sealed-secrets")
    namespaces()
    secrets()
    for name in ["postgres", "valkey", "ollama"]:
        stage(name)
    models()
    stage("gateway")
    run([sys.executable, DEPLOY / "scripts" / "smoke.py", "--bootstrap-only"])
    stage("webui")
    monitoring()
    run([sys.executable, DEPLOY / "scripts" / "smoke.py"])
    run([sys.executable, DEPLOY / "scripts" / "bootstrap-webui.py"])
    print("Private install complete. WebUI admin and judge accounts are ready. Enable edge after choosing a domain.")


def destroy():
    if not STATE_FILE.exists():
        raise RuntimeError("Missing deployment inventory. Restore it before teardown so existing resources cannot be mistaken for ours.")
    state = inventory()
    print(f"Removing IntentLatch resources and data from context {CONTEXT}", flush=True)
    volumes = [p["metadata"]["name"] for p in kube_json("get", "pv")["items"]
               if p["spec"].get("claimRef", {}).get("namespace") in APP_NAMESPACES]
    api_resources = run([*KUBE, "api-resources", "-o", "name"], capture=True).stdout.splitlines()
    if "gateways.gateway.networking.k8s.io" in api_resources:
        run([*KUBE, "-n", "envoy-gateway-system", "delete", "gateway", "intentlatch", "--ignore-not-found=true", "--wait=true"])
        run([*KUBE, "-n", "envoy-gateway-system", "delete", "service", "-l", "gateway.envoyproxy.io/owning-gateway-name=intentlatch", "--wait=true", "--timeout=10m"])
    # Let the running operator remove the generated StatefulSets first.
    for resource in ["prometheuses.monitoring.coreos.com", "alertmanagers.monitoring.coreos.com"]:
        if resource in api_resources:
            run([*KUBE, "-n", "observability", "delete", resource, "--all", "--wait=true", "--timeout=5m"])
    for release, namespace in [("monitoring", "observability"), ("intentlatch", "default"),
                               ("eg", "envoy-gateway-system"), ("cert-manager", "cert-manager"), ("sealed-secrets", "sealed-secrets")]:
        if run([*HELM, "status", release, "-n", namespace], capture=True, check=False).returncode == 0:
            run([*HELM, "uninstall", release, "-n", namespace, "--wait", "--timeout", "15m"])
    if state.get("kubelet_service_created"):
        run([*KUBE, "-n", "kube-system", "delete", "service,endpoints", "monitoring-kubelet", "--ignore-not-found=true"])
        run([*KUBE, "-n", "kube-system", "delete", "endpointslices", "-l", "kubernetes.io/service-name=monitoring-kubelet", "--ignore-not-found=true"])
    run([*KUBE, "delete", "namespace", *APP_NAMESPACES, *OP_NAMESPACES, "--ignore-not-found=true", "--wait=true", "--timeout=15m"])
    for name in volumes:
        remaining = run([*KUBE, "get", "pv", name, "--ignore-not-found=true", "-o", "name"], capture=True).stdout.strip()
        if remaining:
            run([*KUBE, "wait", "--for=delete", "pv/" + name, "--timeout=10m"])
    for name in state["created_crds"]:
        # A shared CRD must be left alone if someone created another instance.
        remaining = run([*KUBE, "get", name, "-A", "-o", "name"], capture=True, check=False)
        if remaining.returncode == 0 and remaining.stdout.strip():
            raise RuntimeError(f"CRD {name} still has instances; inspect them before removing the shared definition")
        run([*KUBE, "delete", "crd", name, "--ignore-not-found=true"])
    if state["node"]:
        node = state["node"]
        exists = run([*KUBE, "get", "node", node["name"], "--ignore-not-found=true", "-o", "name"], capture=True).stdout.strip()
        if exists:
            if not node["had_taint"]:
                run([*KUBE, "taint", "node", node["name"], "intentlatch.io/role:NoSchedule-"], check=False)
            if node["previous_role"] is None:
                run([*KUBE, "label", "node", node["name"], "intentlatch.io/role-"], check=False)
    shutil.rmtree(STATE)
    print("Removed recorded resources, PVCs, retained chart CRDs, and the edge Service. Delete the DOKS cluster after the event and verify Volumes, Load Balancers, and Billing in DigitalOcean. Remove the wildcard DNS record too. GHCR images remain available.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["tools", "cluster-prep", "namespaces", "sealed-secrets", "secrets", "postgres", "valkey", "ollama", "models", "gateway", "webui", "monitoring", "edge", "install", "destroy", "status", "check-image", "local-cluster"])
    action = parser.parse_args().action
    if action == "status":
        run([*HELM, "list", "-A"])
        run([*KUBE, "get", "pods,svc,pvc", "-A"])
    elif action == "local-cluster":
        if PROFILE != "local":
            raise RuntimeError("Use PROFILE=local for local-cluster")
        tools()
        run([DEPLOY / ".state" / "bin" / "k3d", "cluster", "create", "intentlatch", "--image", VERSIONS["K3S_IMAGE"],
             "--servers", "1", "--agents", "2", "--no-lb", "--api-port", "127.0.0.1:6550", "--k3s-arg", "--disable=traefik,servicelb@server:*"])
    elif action == "sealed-secrets":
        component(action)
    elif action in {"postgres", "valkey", "ollama", "gateway", "webui"}:
        stage(action)
    else:
        {"tools": tools, "cluster-prep": prep, "namespaces": namespaces, "secrets": secrets,
         "models": models, "monitoring": monitoring, "edge": edge, "install": install,
         "destroy": destroy, "check-image": assert_public_image}[action]()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
