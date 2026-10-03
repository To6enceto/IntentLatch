#!/usr/bin/env python3
"""Create local demo logins, persisting only sealed passwords."""
import json
import secrets
import sys
import time

from common import SEALED, atomic_json, seal, secret_data, upgrade
from smoke import REDACTIONS, forward, request, safe


def credentials():
    values = json.loads(SEALED.read_text())
    if "webuiAccounts" in values["sealedSecrets"]:
        return secret_data("agents", "webui-accounts")
    data = {
        "admin-email": "admin@intentlatch.example",
        "admin-password": secrets.token_urlsafe(32),
        "judge-email": "judge@intentlatch.example",
        "judge-password": secrets.token_urlsafe(32),
    }
    REDACTIONS.update(data.values())
    values["sealedSecrets"]["webuiAccounts"] = seal("agents", "webui-accounts", data)
    atomic_json(SEALED, values)
    upgrade()
    for _ in range(60):
        try:
            if secret_data("agents", "webui-accounts") == data:
                return data
        except RuntimeError:
            pass
        time.sleep(1)
    raise RuntimeError("WebUI login credentials did not reconcile within 60 seconds")


def signin(base, data, role):
    result = json.loads(request(base, "/api/v1/auths/signin", body={
        "email": data[role + "-email"], "password": data[role + "-password"],
    }))
    REDACTIONS.add(result["token"])
    assert result["role"] == ("admin" if role == "admin" else "user"), "Unexpected WebUI account role"
    return result


def configure_models(base, admin, judge_id):
    for model in ("corporate-a", "corporate-b"):
        # Base models must be registered before ordinary users can see them.
        available = json.loads(request(base, "/api/models?refresh=true", token=admin))["data"]
        entry = next(item for item in available if item["id"] == model)
        grants = (entry.get("info") or {}).get("access_grants", [])
        if not any(grant.get("principal_type") == "user" and grant.get("principal_id") == judge_id
                   and grant.get("permission") == "read" for grant in grants):
            grants.append({"principal_type": "user", "principal_id": judge_id, "permission": "read"})
            request(base, "/api/v1/models/model/access/update", token=admin,
                    body={"id": model, "name": model, "access_grants": grants})

        # The model list omits params; fetch the complete record to preserve it.
        record = json.loads(request(base, f"/api/v1/models/model?id={model}", token=admin))
        meta = record.get("meta") or {}
        meta["capabilities"] = {**(meta.get("capabilities") or {}), "builtin_tools": False}
        params = record.get("params") or {}
        params.setdefault("max_tokens", 256)
        request(base, "/api/v1/models/model/update", token=admin, body={
            "id": model, "base_model_id": record.get("base_model_id"),
            "name": record["name"], "meta": meta, "params": params,
            "is_active": record.get("is_active", True),
        })
        print(f"PASS: {model} uses plain chat without automatic builtin tools; output default is bounded")


def main():
    data = credentials()
    REDACTIONS.update(data.values())
    with forward("agents", "open-webui", 8080) as base:
        config = json.loads(request(base, "/api/config"))
        if config.get("onboarding"):
            result = json.loads(request(base, "/api/v1/auths/signup", body={
                "name": "IntentLatch Admin", "email": data["admin-email"],
                "password": data["admin-password"],
            }))
            REDACTIONS.add(result["token"])
            assert result["role"] == "admin", "First WebUI account was not an administrator"
        admin = signin(base, data, "admin")["token"]
        try:
            signin(base, data, "judge")
        except RuntimeError as error:
            if "HTTP 400" not in str(error):
                raise
            result = json.loads(request(base, "/api/v1/auths/add", token=admin, body={
                "name": "HackYeah Judge", "email": data["judge-email"],
                "password": data["judge-password"], "role": "user",
            }))
            REDACTIONS.add(result["token"])
            assert result["role"] == "user", "Judge must have the ordinary user role"
        judge = signin(base, data, "judge")
        configure_models(base, admin, judge["id"])
    # Persist the closed signup setting in Helm, including after pod restarts.
    upgrade("webui.enableSignup=false")
    with forward("agents", "open-webui", 8080) as base:
        config = json.loads(request(base, "/api/config"))
        assert config["features"]["auth"] and not config["features"]["enable_signup"], "WebUI login/signup settings did not persist"
        token = signin(base, data, "judge")["token"]
        models = json.loads(request(base, "/api/models?refresh=true", token=token))
        assert {"corporate-a", "corporate-b"} <= {model["id"] for model in models["data"]}, "Judge cannot see both corporate models"
        for model in ("corporate-a", "corporate-b"):
            result = json.loads(request(base, "/api/chat/completions", token=token, body={
                "model": model, "stream": False,
                "messages": [{"role": "user", "content": "Reply with OK only."}],
            }))
            assert result["choices"][0]["message"]["content"], "Empty WebUI model response"
            print(f"PASS: WebUI judge login and {model} prompt through the gateway")
    print("PASS: admin and judge accounts ready, signup disabled, passwords sealed in agents/webui-accounts")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, AssertionError, OSError, KeyError, ValueError) as error:
        print(safe(f"FAIL: {error}"), file=sys.stderr)
        sys.exit(1)
