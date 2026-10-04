"""Shared subprocess and sealing helpers. Secret values stay in memory."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

DEPLOY = Path(__file__).resolve().parents[1]
REPO = DEPLOY.parent
PROFILE = os.environ.get("PROFILE", "cloud")
if PROFILE not in {"cloud", "local"}:
    raise SystemExit("PROFILE must be cloud or local")
CONTEXT = os.environ.get("KUBE_CONTEXT", "k3d-intentlatch" if PROFILE == "local" else "do-fra1-hackyeah-cluster")
STATE = DEPLOY / ".state" / PROFILE
SEALED = STATE / "sealed-values.json"
KUBE = ["kubectl", "--context", CONTEXT]
HELM = ["helm", "--kube-context", CONTEXT]
CHART = DEPLOY / "helm" / "intentlatch"
APP_NAMESPACES = ["intentlatch-system", "intentlatch-data", "agents", "upstreams", "observability"]


def run(args, *, capture=False, data=None, check=True):
    result = subprocess.run([str(a) for a in args], input=data, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if check and result.returncode:
        # Never dump captured output: it can contain a Secret or identity token.
        raise RuntimeError(f"{args[0]} failed with exit code {result.returncode}")
    return result


def kube_json(*args):
    return json.loads(run([*KUBE, *args, "-o", "json"], capture=True).stdout)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def secret_data(namespace, name):
    secret = kube_json("-n", namespace, "get", "secret", name)
    return {key: base64.b64decode(value).decode() for key, value in secret["data"].items()}


def seal(namespace, name, data, secret_type="Opaque"):
    kubeseal = shutil.which("kubeseal") or str(DEPLOY / ".state" / "bin" / "kubeseal")
    secret = {"apiVersion": "v1", "kind": "Secret", "type": secret_type,
              "metadata": {"name": name, "namespace": namespace},
              "data": {key: base64.b64encode(value.encode()).decode() for key, value in data.items()}}
    response = run([kubeseal, "--context", CONTEXT, "--controller-name", "sealed-secrets",
                    "--controller-namespace", "sealed-secrets", "--scope", "strict", "--format", "json"],
                   capture=True, data=json.dumps(secret))
    sealed = json.loads(response.stdout)
    return {"name": name, "namespace": namespace, "type": secret_type,
            "encryptedData": sealed["spec"]["encryptedData"]}


def app_values():
    args = ["-f", str(CHART / "values.yaml")]
    if PROFILE == "local":
        args += ["-f", str(CHART / "values-local.yaml")]
    site = DEPLOY / "site-values.yaml"
    if PROFILE == "cloud" and site.exists():
        args += ["-f", str(site)]
    if SEALED.exists():
        args += ["-f", str(SEALED)]
    return args


def upgrade(*overrides, initial=False, wait=True):
    args = [*HELM, "upgrade", "--install", "intentlatch", str(CHART), "-n", "default"]
    if initial:
        args += app_values()
    else:
        # Preserve staged enabled flags, especially the closed pull exception.
        args += ["--reuse-values"]
        if SEALED.exists():
            args += ["-f", str(SEALED)]
    for override in overrides:
        args += ["--set", override]
    if wait:
        args += ["--wait", "--timeout", "15m"]
    run(args)
