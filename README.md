# IntentLatch

A self-hosted AI control layer: one gateway between a company's employees and
its corporate LLMs, governed by policies the security team writes and changes
live. Built for the HackYeah 2026 "AI Control Layer" challenge.

## Layout

- `gateway/` - the control gateway (Python, FastAPI)

## Run the gateway locally

Prerequisites: Python 3.12+, Docker, and access to the cluster's Ollama.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ./gateway
cp gateway/.env.example gateway/.env

docker run -d --name intentlatch-pg -e POSTGRES_USER=intentlatch \
  -e POSTGRES_PASSWORD=intentlatch -e POSTGRES_DB=intentlatch \
  -p 127.0.0.1:5432:5432 postgres:17          # later: docker start intentlatch-pg

kubectl port-forward -n upstreams svc/ollama 11434:11434   # separate terminal

cd gateway && ../.venv/bin/uvicorn intentlatch.main:app --reload --port 8080 --env-file .env
```

Then point any OpenAI- or Ollama-compatible client at `http://localhost:8080`:

```bash
curl -s localhost:8080/healthz
curl -s localhost:8080/v1/chat/completions -H 'content-type: application/json' \
  -d '{"model": "corporate-a", "messages": [{"role": "user", "content": "Hello"}]}'
```

Models are `corporate-a` and `corporate-b`. Until identity tokens land, every
endpoint is open, so keep the gateway on `127.0.0.1`.
