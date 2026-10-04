#!/usr/bin/env python3
"""Exercise real inference and isolation without printing identity credentials."""
import argparse
from contextlib import contextmanager
import getpass
import ipaddress
import json
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from common import KUBE, SEALED, atomic_json, kube_json, run, seal, secret_data, upgrade

REDACTIONS = set()


def safe(value):
    for credential in REDACTIONS:
        value = value.replace(credential, "[REDACTED]")
    return value


def request(base, path, *, token=None, body=None, expected=200, tls=None):
    headers = {}
    if token:
        REDACTIONS.add(token)
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=370, context=tls)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        text = response.read().decode()
        if response.status != expected:
            request_id = response.headers.get("X-Request-ID", "not provided")
            raise RuntimeError(safe(f"{path}: HTTP {response.status}, X-Request-ID={request_id}, body={text[:4000]}"))
        return text


@contextmanager
def forward(namespace, service, remote_port, local_port=0):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", local_port))
        port = sock.getsockname()[1]
    process = subprocess.Popen([*KUBE, "-n", namespace, "port-forward", "--address", "127.0.0.1",
                                "svc/" + service, f"{port}:{remote_port}"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Port-forward exited before becoming ready")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Port-forward did not become ready")
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stderr.close()


def employee(base, bootstrap):
    token = secret_data("agents", "webui-employee")["OPENAI_API_KEY"]
    REDACTIONS.add(token)
    authority = json.loads(request(base, "/authority", body={"token": token}))
    if authority.get("valid"):
        return token
    if not bootstrap:
        raise RuntimeError("The WebUI employee token is not valid. Run make bootstrap first.")
    if not SEALED.exists():
        raise RuntimeError("Missing sealed-values.json; restore it before bootstrapping an identity")
    admin = secret_data("intentlatch-system", "gateway-auth")["INTENTLATCH_ADMIN_API_KEY"]
    REDACTIONS.add(admin)
    teams = json.loads(request(base, "/admin/teams", token=admin))["teams"]
    team = next((t for t in teams if t["name"] == "judges"), None)
    if team is None:
        team = json.loads(request(base, "/admin/teams", token=admin, expected=201,
                                  body={"name": "judges", "authorized_models": ["corporate-a", "corporate-b"]}))
    elif set(team["authorized_models"]) != {"corporate-a", "corporate-b"}:
        raise RuntimeError("Existing judges team does not authorize both corporate models. Update it through the private admin API first.")
    path = "/admin/teams/" + team["id"] + "/employees"
    employees = json.loads(request(base, path, token=admin))["employees"]
    existing = next((e for e in employees if e["name"] == "open-webui"), None)
    if existing is None:
        result = json.loads(request(base, path, token=admin, body={"name": "open-webui"}, expected=201))
    else:
        result = json.loads(request(base, "/admin/employees/" + existing["id"] + "/token", token=admin, body={}))
    token = result["token"]
    REDACTIONS.add(token)
    values = json.loads(SEALED.read_text())
    values["sealedSecrets"]["employee"] = seal("agents", "webui-employee", {"OPENAI_API_KEY": token})
    atomic_json(SEALED, values)
    upgrade()
    for _ in range(60):
        if secret_data("agents", "webui-employee")["OPENAI_API_KEY"] == token:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Sealed employee token did not reconcile within 60 seconds")
    exists = run([*KUBE, "-n", "agents", "get", "deployment", "open-webui", "--ignore-not-found=true", "-o", "name"], capture=True).stdout.strip()
    if exists:
        run([*KUBE, "-n", "agents", "rollout", "restart", "deployment/open-webui"])
        run([*KUBE, "-n", "agents", "rollout", "status", "deployment/open-webui", "--timeout=15m"])
    print("PASS: judges/open-webui identity sealed; token was not printed.")
    return token


def inference(base, token):
    health = json.loads(request(base, "/healthz"))
    assert health["database"] == "ok" and health["upstream"] == "ok", health
    models = json.loads(request(base, "/v1/models", token=token))
    assert {m["id"] for m in models["data"]} == {"corporate-a", "corporate-b"}
    tags = json.loads(request(base, "/api/tags", token=token))
    assert {m["name"] for m in tags["models"]} == {"corporate-a", "corporate-b"}
    for model in ("corporate-a", "corporate-b"):
        body = {"model": model, "messages": [{"role": "user", "content": "Reply with OK only."}],
                "max_completion_tokens": 16, "tool_choice": "none", "stream": False}
        result = json.loads(request(base, "/v1/chat/completions", token=token, body=body))
        assert result["model"] == model and result["choices"][0]["message"]["content"]
        body["stream"] = True
        stream = request(base, "/v1/chat/completions", token=token, body=body)
        events = [line.removeprefix("data: ") for line in stream.splitlines() if line.startswith("data: ")]
        assert len(events) >= 2 and events[-1] == "[DONE]", "SSE did not terminate with [DONE]"
        assert all(json.loads(event)["model"] == model for event in events[:-1])
        print(f"PASS: {model} buffered JSON and SSE inference")
    stream = request(base, "/api/chat", token=token, body={"model": "corporate-b", "stream": True,
                     "messages": [{"role": "user", "content": "Reply with OK only."}], "options": {"num_predict": 16}})
    lines = [json.loads(line) for line in stream.splitlines() if line.strip()]
    assert len(lines) == 2 and lines[0]["done"] is False and lines[1]["done"] is True
    assert all(line["model"] == "corporate-b" for line in lines)
    assert json.loads(request(base, "/authority", body={"token": token}))["valid"] is True
    print("PASS: NDJSON, corporate model names, health, and authority")


def network_proof():
    name = "intentlatch-netproof-" + uuid.uuid4().hex[:8]
    ip = kube_json("-n", "upstreams", "get", "service", "ollama")["spec"]["clusterIP"]
    # Use the allowed agent labels so failure cannot be explained just by a
    # blanket agents egress deny. Check both DNS and the direct Service IP.
    command = f'''set -eu
curl --fail --silent --show-error --max-time 10 http://intentlatch-gateway.intentlatch-system:8080/healthz >/dev/null
for target in ollama.upstreams {ip}; do
  set +e
  curl --silent --show-error --connect-timeout 3 --max-time 5 "http://$target:11434/api/version" >/dev/null 2>&1
  result=$?
  set -e
  if [ "$result" -ne 28 ]; then
    echo "Expected a policy timeout for $target, got curl exit $result"
    exit 1
  fi
done
echo "PASS: agent reaches gateway, direct Ollama DNS and IP traffic times out"
'''
    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": name, "namespace": "agents",
           "labels": {"app.kubernetes.io/name": "open-webui"}}, "spec": {"restartPolicy": "Never",
           "automountServiceAccountToken": False, "securityContext": {"runAsNonRoot": True, "runAsUser": 10001, "seccompProfile": {"type": "RuntimeDefault"}},
           "containers": [{"name": "curl", "image": "curlimages/curl:8.22.0", "command": ["sh", "-ec", command],
                           "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                           "resources": {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"cpu": "100m", "memory": "32Mi"}}}]}}
    try:
        run([*KUBE, "create", "-f", "-"], data=json.dumps(pod))
        result = run([*KUBE, "-n", "agents", "wait", "--for=jsonpath={.status.phase}=Succeeded", "pod/" + name, "--timeout=180s"], check=False)
        run([*KUBE, "-n", "agents", "logs", name], check=False)
        if result.returncode:
            raise RuntimeError("NetworkPolicy proof failed; inspect pod scheduling, image pulls, DNS, and CNI enforcement")
    finally:
        run([*KUBE, "-n", "agents", "delete", "pod", name, "--ignore-not-found=true", "--wait=true"], check=False)


def webui_test(base, email):
    page = request(base, "/")
    assert "<html" in page.lower()
    if not email:
        print("PASS: WebUI HTML loads. NOT TESTED: judge login and UI prompt; use --webui-email or complete the browser check in README.")
        return
    password = getpass.getpass("WebUI judge password: ")
    REDACTIONS.add(password)
    session = json.loads(request(base, "/api/v1/auths/signin", body={"email": email, "password": password}))["token"]
    REDACTIONS.add(session)
    result = json.loads(request(base, "/api/chat/completions", token=session, body={"model": "corporate-b", "stream": False,
                                "messages": [{"role": "user", "content": "Reply with OK only."}]}))
    assert result["choices"][0]["message"]["content"]
    print("PASS: WebUI judge login and prompt through the gateway")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap-only", action="store_true")
    address = parser.add_mutually_exclusive_group()
    address.add_argument("--domain")
    address.add_argument("--public-ip", type=ipaddress.IPv4Address)
    parser.add_argument("--staging", action="store_true", help="Explicitly accept the untrusted ACME staging certificate")
    parser.add_argument("--webui-email")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    with forward("intentlatch-system", "intentlatch-gateway", 8080, args.port) as base:
        token = employee(base, args.bootstrap_only)
        if args.bootstrap_only:
            return
        inference(base, token)
    resident = run([*KUBE, "-n", "upstreams", "exec", "deployment/ollama", "--", "ollama", "ps"], capture=True).stdout
    assert "qwen2.5:3b" in resident and "llama3.2:1b" in resident, "Both models must remain resident; inspect Ollama memory limits and logs"
    print("PASS: both models remain resident in Ollama")
    network_proof()
    with forward("agents", "open-webui", 8080) as base:
        webui_test(base, args.webui_email)
    if args.domain or args.public_ip:
        tls = ssl._create_unverified_context() if args.staging else ssl.create_default_context()
        host = str(args.public_ip) if args.public_ip else args.domain
        base = "https://" + host + ":9443" if args.public_ip else "https://api." + host
        chat = "https://" + host if args.public_ip else "https://chat." + host
        grafana = "https://" + host + ":8443" if args.public_ip else "https://grafana." + host
        assert json.loads(request(base, "/healthz", tls=tls))["database"] == "ok"
        for path in ["/admin/teams", "/metrics", "/docs", "/openapi.json"]:
            request(base, path, expected=404, tls=tls)
        request(base, "/v1/models", expected=401, tls=tls)
        assert len(json.loads(request(base, "/v1/models", token=token, tls=tls))["data"]) == 2
        request(base, "/v1/chat/completions", token=token, expected=413, tls=tls,
                body={"model": "corporate-b", "messages": [{"role": "user", "content": "x" * (1024 * 1024)}]})
        assert "<html" in request(chat, "/", tls=tls).lower()
        request(chat, "/api/models", expected=401, tls=tls)
        assert json.loads(request(grafana, "/api/health", tls=tls))["database"] == "ok"
        request(grafana, "/api/user", expected=401, tls=tls)
        print("PASS: public TLS, health, API authentication, private admin/metrics/docs, 1MiB body limit, Chat, and Grafana")
    else:
        print("NOT TESTED: public TLS/edge, no domain or public IP supplied")
    print("Completed the enabled smoke checks. Gateway errors include X-Request-ID and response body; report upstream incompatibilities to the gateway owner.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, AssertionError, OSError, KeyError, ValueError) as error:
        print(safe(f"FAIL: {error}"), file=sys.stderr)
        sys.exit(1)
