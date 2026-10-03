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

Models are `corporate-a` and `corporate-b`. Until the authority policy lands,
any valid token can call both. Reissue a token with
`POST /admin/employees/<employee-id>/token`; revoke an employee with
`POST /admin/employees/<employee-id>/revoke`.
