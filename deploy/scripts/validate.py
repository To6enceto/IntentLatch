#!/usr/bin/env python3
"""Render deployment variants and check their security and CRD contracts."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

try:
    import jsonschema
    import yaml
except ImportError:
    raise SystemExit("Install deploy/validation-requirements.txt in a Python virtual environment first")

from common import CHART, DEPLOY
from manage import COMPONENTS, VERSIONS


class KubernetesLoader(yaml.SafeLoader):
    pass


# Some upstream CRDs have an unquoted '=' enum member, which PyYAML 1.1 tags.
KubernetesLoader.add_constructor("tag:yaml.org,2002:value", KubernetesLoader.construct_scalar)


def command(args):
    result = subprocess.run([str(x) for x in args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout


def documents(text):
    return [doc for doc in yaml.load_all(text, Loader=KubernetesLoader) if doc]


def render(*overrides, local=False):
    args = ["helm", "template", "intentlatch", CHART]
    if local:
        args += ["-f", CHART / "values-local.yaml"]
    for value in overrides:
        args += ["--set", value]
    return documents(command(args))


def selected(selector, labels):
    return all(labels.get(key) == value for key, value in selector.get("matchLabels", {}).items())


def allowed(docs, source, destination, port, protocol="TCP"):
    def side(endpoint, other, direction):
        policies = [d["spec"] for d in docs if d["kind"] == "NetworkPolicy"
                    and d["metadata"]["namespace"] == endpoint[0]
                    and selected(d["spec"]["podSelector"], endpoint[1])
                    and direction in d["spec"]["policyTypes"]]
        if not policies:
            return True
        for policy in policies:
            for rule in policy.get(direction.lower(), []):
                if "ports" in rule and not any(p["port"] == port and p.get("protocol", "TCP") == protocol for p in rule["ports"]):
                    continue
                peers = rule.get("from" if direction == "Ingress" else "to", [{}])
                for peer in peers:
                    if "ipBlock" in peer:
                        continue
                    if "namespaceSelector" in peer:
                        if not selected(peer["namespaceSelector"], {"kubernetes.io/metadata.name": other[0]}):
                            continue
                    elif "podSelector" in peer and endpoint[0] != other[0]:
                        continue
                    if "podSelector" in peer and not selected(peer["podSelector"], other[1]):
                        continue
                    return True
        return False
    return side(source, destination, "Egress") and side(destination, source, "Ingress")


def verify(docs, *, local=False, pulling=False):
    identities = [(d["kind"], d["metadata"].get("namespace"), d["metadata"]["name"]) for d in docs]
    assert len(identities) == len(set(identities)), "Duplicate resource identities"
    assert not any(d["kind"] == "Secret" for d in docs), "Plain Secrets must not be rendered"
    for doc in docs:
        kind = doc["kind"]
        if kind in {"Deployment", "StatefulSet", "Job", "DaemonSet"}:
            spec = doc["spec"]["template"]["spec"]
            assert spec.get("automountServiceAccountToken") is False
            for container in spec.get("containers", []) + spec.get("initContainers", []):
                assert re.search(r"(:[^/:]+|@sha256:[a-f0-9]{64})$", container["image"])
                assert not container["image"].endswith(":latest")
                assert "requests" in container["resources"] and "limits" in container["resources"]
        if local and kind == "PersistentVolumeClaim":
            assert doc["spec"]["storageClassName"] == "local-path"
    pod = lambda ns, name: (ns, {"app.kubernetes.io/name": name})
    gateway = pod("intentlatch-system", "intentlatch-gateway")
    agent = pod("agents", "open-webui")
    ollama = pod("upstreams", "ollama")
    postgres = pod("intentlatch-data", "postgres")
    valkey = pod("intentlatch-data", "valkey")
    envoy = pod("envoy-gateway-system", "envoy")
    prometheus = pod("observability", "prometheus")
    grafana = pod("observability", "grafana")
    pull = pod("upstreams", "ollama-pull")
    for source, destination, port in [(gateway, ollama, 11434), (gateway, postgres, 5432),
                                      (gateway, valkey, 6379), (agent, gateway, 8080),
                                      (envoy, gateway, 8080), (envoy, agent, 8080),
                                      (prometheus, gateway, 8080), (prometheus, grafana, 3000),
                                      (envoy, grafana, 3000)]:
        assert allowed(docs, source, destination, port), (source, destination, "unexpected deny")
    for source, destination, port in [(agent, ollama, 11434), (agent, postgres, 5432),
                                      (envoy, ollama, 11434), (prometheus, ollama, 11434),
                                      (pod("agents", "intentlatch-gateway"), ollama, 11434),
                                      (pod("agents", "untrusted"), gateway, 8080),
                                      (postgres, gateway, 8080),
                                      (pod("observability", "untrusted"), grafana, 3000)]:
        assert not allowed(docs, source, destination, port), (source, destination, "unexpected allow")
    assert allowed(docs, pull, ollama, 11434) == pulling
    for source in [gateway, agent, ollama, postgres, valkey]:
        for protocol in ["TCP", "UDP"]:
            assert allowed(docs, source, ("kube-system", {"k8s-app": "kube-dns"}), 53, protocol)
    if not pulling:
        assert not any(d["metadata"]["name"] == "ollama-download" for d in docs)
    gateways = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "intentlatch-gateway"]
    if gateways:
        spec = gateways[0]["spec"]["template"]["spec"]
        container = spec["containers"][0]
        assert "tcpSocket" in container["livenessProbe"]
        assert container["readinessProbe"]["httpGet"]["path"] == "/healthz"
        assert container["readinessProbe"]["timeoutSeconds"] > 2
        assert container["securityContext"]["readOnlyRootFilesystem"]
        assert spec["securityContext"]["runAsNonRoot"]
    if local:
        assert not any(d["kind"] in {"Gateway", "Certificate", "EnvoyProxy"} for d in docs)
        ollama_spec = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "ollama")["spec"]["template"]["spec"]
        assert "nodeSelector" not in ollama_spec and "tolerations" not in ollama_spec
    for doc in docs:
        if doc["kind"] == "HTTPRoute" and doc["metadata"]["name"] == "api":
            paths = {m["path"]["value"]: m["path"]["type"] for rule in doc["spec"]["rules"] for m in rule["matches"]}
            assert paths == {"/v1": "PathPrefix", "/api": "PathPrefix", "/authority": "Exact", "/healthz": "Exact"}
        if doc["kind"] == "HTTPRoute" and doc["metadata"]["name"] in {"api", "chat"}:
            assert all(int(rule["timeouts"]["request"].removesuffix("s")) >= 300 for rule in doc["spec"]["rules"])
    print(f"PASS: {'local' if local else 'cloud'}, pull={pulling}, {len(docs)} resources; isolation, probes, image pins, and routes")


def strict_fields(value, schema, path="spec"):
    if isinstance(value, dict) and schema.get("properties"):
        for key, child in value.items():
            if key not in schema["properties"]:
                if not schema.get("additionalProperties") and not schema.get("x-kubernetes-preserve-unknown-fields"):
                    raise AssertionError(f"Unknown CRD field {path}.{key}")
            else:
                strict_fields(child, schema["properties"][key], path + "." + key)
    elif isinstance(value, list) and "items" in schema:
        for child in value:
            strict_fields(child, schema["items"], path + "[]")


def third_party(directory, fetch, app_docs):
    directory.mkdir(parents=True, exist_ok=True)
    all_docs = []
    for name, (release, namespace, chart, repo, version, values) in COMPONENTS.items():
        chart_name = chart.rsplit("/", 1)[-1]
        path = directory / chart_name
        if fetch and not path.exists():
            args = ["helm", "pull", chart, "--version", VERSIONS[version], "--untar", "--untardir", directory]
            if repo:
                args += ["--repo", repo]
            command(args)
        metadata = yaml.safe_load((path / "Chart.yaml").read_text())
        assert metadata["version"].lstrip("v") == VERSIONS[version].lstrip("v"), "Wrong third-party chart version"
        docs = documents(command(["helm", "template", release, path, "-n", namespace, "--include-crds", "-f", DEPLOY / "third-party" / values]))
        if name == "monitoring":
            # Disabling admission must also remove its certificate dependency.
            operator = next(doc for doc in docs if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "monitoring-operator")
            mounted_secrets = {volume["secret"]["secretName"] for volume in (operator["spec"]["template"]["spec"].get("volumes") or []) if "secret" in volume}
            assert "monitoring-admission" not in mounted_secrets, "Operator depends on the disabled webhook's certificate Secret"
        for doc in docs:
            if doc["kind"] == "Service":
                assert doc["spec"].get("type", "ClusterIP") != "LoadBalancer", "Only the EnvoyProxy may create a load balancer"
            if doc["kind"] in {"Deployment", "DaemonSet", "StatefulSet", "Job"}:
                for c in doc["spec"]["template"]["spec"].get("containers", []):
                    assert not c["image"].endswith(":latest")
                    assert re.search(r"(:[^/:]+|@sha256:[a-f0-9]{64})$", c["image"])
                    assert c.get("resources", {}).get("requests"), f"Missing resources for {name}/{c['name']}"
        all_docs.extend(docs)
    schemas = {}
    for doc in all_docs:
        if doc["kind"] == "CustomResourceDefinition":
            for version in doc["spec"]["versions"]:
                schemas[(doc["spec"]["group"] + "/" + version["name"], doc["spec"]["names"]["kind"])] = version["schema"]["openAPIV3Schema"]
    count = 0
    for doc in app_docs + all_docs:
        schema = schemas.get((doc["apiVersion"], doc["kind"]))
        if schema:
            jsonschema.Draft7Validator(schema).validate(doc)
            strict_fields(doc["spec"], schema["properties"]["spec"])
            count += 1
    print(f"PASS: third-party image pins/resources and {count} custom resources against pinned CRD schemas (CEL rules require a live API server)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--third-party-dir", type=Path)
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    command(["helm", "lint", CHART])
    cloud = render("monitoring.enabled=true", "edge.enabled=true", "edge.domain=example.com", "edge.acmeEmail=admin@example.com", "webui.enableSignup=false",
                   "sealedSecrets.schemaCheck.name=schema-check", "sealedSecrets.schemaCheck.namespace=agents",
                   "sealedSecrets.schemaCheck.encryptedData.placeholder=AgSchemaValidationOnly")
    verify(cloud)
    ip_bootstrap = render("edge.enabled=true", "edge.mode=ip", "webui.enableSignup=false")
    ip_ready = render("edge.enabled=true", "edge.mode=ip", "edge.publicIP=203.0.113.10", "webui.enableSignup=false")
    for docs in [ip_bootstrap, ip_ready]:
        verify(docs)
        gateway = next(d for d in docs if d["kind"] == "Gateway")
        assert all("hostname" not in l for l in gateway["spec"]["listeners"])
        assert all("hostnames" not in d["spec"] for d in docs if d["kind"] == "HTTPRoute")
        assert all(d["spec"]["acme"]["profile"] == "shortlived" for d in docs if d["kind"] == "ClusterIssuer")
        http_routes = [d for d in docs if d["kind"] == "HTTPRoute" and any(p.get("sectionName") == "http" for p in d["spec"]["parentRefs"])]
        assert len(http_routes) == 1
        assert all("backendRefs" not in rule for rule in http_routes[0]["spec"]["rules"])
    assert not any(d["kind"] == "Certificate" for d in ip_bootstrap)
    assert [l["port"] for d in ip_bootstrap if d["kind"] == "Gateway" for l in d["spec"]["listeners"]] == [80]
    assert {l["name"]: l["port"] for d in ip_ready if d["kind"] == "Gateway" for l in d["spec"]["listeners"]} == {"http": 80, "chat": 443, "grafana": 8443, "api": 9443}
    certificate = next(d["spec"] for d in ip_ready if d["kind"] == "Certificate")
    assert certificate["ipAddresses"] == ["203.0.113.10"] and "dnsNames" not in certificate
    assert certificate["duration"] == "160h" and certificate["renewBefore"] == "48h"
    verify(render(local=True), local=True)
    verify(render("ollama.pull.enabled=true"), pulling=True)
    verify(render("ollama.pull.enabled=true", local=True), pulling=True, local=True)
    for dashboard in (CHART / "dashboards").glob("*.json"):
        assert json.loads(dashboard.read_text())["panels"]
    # The public edge must fail closed while the domain/login setup is missing.
    result = subprocess.run(["helm", "template", "test", CHART, "--set", "edge.enabled=true"], capture_output=True)
    assert result.returncode != 0
    if args.third_party_dir or args.fetch:
        third_party(args.third_party_dir or DEPLOY / ".state" / "validation-charts", args.fetch, cloud + ip_bootstrap + ip_ready)


if __name__ == "__main__":
    main()
