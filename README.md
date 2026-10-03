# IntentLatch

A self-hosted AI control layer: one gateway between a company's employees and
its corporate LLMs, governed by policies the security team writes and changes
live. Built for the HackYeah 2026 "AI Control Layer" challenge.

## Layout

- `gateway/` - the control gateway (Python, FastAPI)

## Run the gateway locally

Prerequisites: Python 3.12+, Docker, and access to the cluster's Ollama.

```bash
python3 -m venv .venv && .venv/bin/pip install -e "./gateway[test]"
.venv/bin/pytest gateway                      # unit tests

cp gateway/.env.example gateway/.env
# Fill in the two required secrets with fresh random values:
for name in INTENTLATCH_TOKEN_SIGNING_KEY INTENTLATCH_ADMIN_API_KEY; do
  sed -i "s|^$name=.*|$name=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')|" gateway/.env
done

docker run -d --name intentlatch-pg -e POSTGRES_USER=intentlatch \
  -e POSTGRES_PASSWORD=intentlatch -e POSTGRES_DB=intentlatch \
  -p 127.0.0.1:5432:5432 postgres:17          # later: docker start intentlatch-pg

kubectl port-forward -n upstreams svc/ollama 11434:11434   # separate terminal

cd gateway && ../.venv/bin/uvicorn intentlatch.main:app --reload --port 8080 --env-file .env
```

## Create a team and an employee

Admin endpoints take the admin API key from `gateway/.env`. The employee token
is shown only when the employee is created or the token is reissued; keep it.

```bash
ADMIN="Authorization: Bearer $(grep '^INTENTLATCH_ADMIN_API_KEY=' gateway/.env | cut -d= -f2-)"

curl -s localhost:8080/admin/teams -H "$ADMIN" -H 'content-type: application/json' \
  -d '{"name": "payments", "authorized_models": ["corporate-a"]}'
# -> {"id": "<team-id>", ...}

curl -s localhost:8080/admin/teams/<team-id>/employees -H "$ADMIN" \
  -H 'content-type: application/json' -d '{"name": "Ana"}'
# -> {"employee": {...}, "token": "<employee-token>"}
```

## Call a model

Every model endpoint needs an employee token. OpenAI clients send it as their
API key.

```bash
TOKEN="<employee-token>"
curl -s localhost:8080/v1/models -H "Authorization: Bearer $TOKEN"
curl -s localhost:8080/v1/chat/completions -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"model": "corporate-a", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Check what a token may do

Any system can ask what a token is allowed to do. The answer comes from the
database at that moment.

```bash
curl -s localhost:8080/authority -H 'content-type: application/json' -d "{\"token\": \"$TOKEN\"}"
# -> {"valid": true, "employee": {...}, "team": {...}, "authorized_models": ["corporate-a"]}
# -> {"valid": false, "reason": "token_revoked"}   (or "token_invalid"); nothing else is revealed
```

Models are `corporate-a` and `corporate-b`. A team can only call the models it
is authorized for: the `AUTH-MODEL` policy answers any other model with 403
`policy_blocked`, naming the policy and its reason, and nothing is forwarded.
Disabling `AUTH-MODEL` lets every valid token call both models from the next
request. Reissue a token with `POST /admin/employees/<employee-id>/token`;
revoke an employee with `POST /admin/employees/<employee-id>/revoke`.

```bash
curl -s localhost:8080/v1/chat/completions -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"model": "corporate-b", "messages": [{"role": "user", "content": "Hello"}]}'
# -> 403 {"error": {"code": "policy_blocked", "message": "Blocked by AUTH-MODEL: Team payments is not authorized for corporate-b.",
#          "policies": [{"code": "AUTH-MODEL", "kind": "authority", "reasoning": "..."}], "policy_version": 1}}
```

## Manage policies

A policy is either an AI policy (`ai: true` with a `text` rule) or a non-AI
policy of one `kind`: `authority`, `limit` or `regex`. Codes are unique and
uppercased (`rgx-example` becomes `RGX-EXAMPLE`); a policy's `code`, `ai` and `kind`
cannot change after creation, and there is no delete: disable it instead. Every
create and edit bumps one global policy `version`. The gateway seeds missing
defaults from `gateway/src/intentlatch/policy_seeds.json` at startup and never
overwrites an existing policy.

```bash
curl -s localhost:8080/admin/policies -H "$ADMIN"
# -> {"policies": [{"code": "AUTH-MODEL", ...}], "version": 1}

curl -s localhost:8080/admin/policies -H "$ADMIN" -H 'content-type: application/json' \
  -d '{"code": "RGX-EXAMPLE", "ai": false, "kind": "regex", "params": {"pattern": "(?i)\\bPL\\d{26}\\b"},
       "action": "edit", "applies_to": "both", "enabled": true}'
# -> {"policy": {"code": "RGX-EXAMPLE", ...}, "version": 2}

curl -s -X PATCH localhost:8080/admin/policies/RGX-EXAMPLE -H "$ADMIN" \
  -H 'content-type: application/json' -d '{"enabled": false}'
# -> {"policy": {"code": "RGX-EXAMPLE", "enabled": false, ...}, "version": 3}
```

Limit policies take `{"max_tokens": int, "window_seconds": int, "team": "<team name>" | null}`;
authority and limit policies always `block` and apply to prompts only.

## Check a prompt

`POST /check` runs a prompt through the policy pipeline as the token's employee
and returns every policy's decision without forwarding anything to a model. It
answers 200 even when the outcome is `blocked`.

```bash
curl -s localhost:8080/check -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"model": "corporate-b", "prompt": "Hello"}'
# -> {"outcome": "blocked", "policy_version": 1, "policy_results": [{"code": "AUTH-MODEL", "kind": "authority",
#      "ai": false, "action": "block", "result": "violated", "reasoning": "...", "latency_ms": 0.002}], "rewritten": null}
```
