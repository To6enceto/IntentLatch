# AGENTS.md

Instructions for AI coding agents working in this repository.

AI tools must not add AI attribution to commits or pull requests, including AI
`Co-Authored-By` trailers or generated-by signatures. Preserve genuine human
attribution.

## What this is

IntentLatch is a self-hosted AI control layer: one gateway between a company's
employees and its corporate LLMs. Every request carries an employee identity
token and passes policies before and after the model: non-AI policies
(authority, limit, regex) first, then AI policies checked by a small local
control agent. Every decision is recorded, exposed as Prometheus metrics and
available in time-range reports. Built for the HackYeah 2026 "AI Control Layer"
challenge.

## Repository layout

- `gateway/` - the control gateway, Python package `intentlatch` (src layout)
- `console/` - the management console shell, React + Vite + TypeScript
- `deploy/` - Helm chart and cluster setup (not created yet)

## Proportional engineering

Build for established requirements, not hypothetical scale, threats, or future
flexibility. Reuse existing code, the standard library, native platform features,
and installed dependencies before adding machinery.

- Unknown scale or extensibility defaults to the smaller reversible design. Do
  not infer enterprise, multi-tenant, hostile-user, or compliance requirements.
- Derive trust and data-integrity boundaries from actual reachability: untrusted
  input, auth/session/ownership, shared persisted data, destructive operations,
  payments, secrets, and sensitive data.
- Ask only when an unknown materially changes behavior, architecture, persisted
  data, interoperability, a real security boundary, or cost. Otherwise choose the
  simplest repository-native implementation.
- Add an abstraction, dependency, service, configuration surface, compatibility
  layer, or security mechanism only for a current requirement.
- Simplicity never removes real trust-boundary validation, data-loss prevention,
  accessibility, explicit security requirements, configured tests, or project rules.
- Stack-specific template standards apply only when the project uses that stack.

## Conventions

- Python 3.12+ for the gateway; async FastAPI on the request path; Pydantic v2
  for every request, response, policy and config shape.
- Model endpoints require an employee token (`Authorization: Bearer`); admin
  endpoints under `/admin` require the admin API key. Authority comes from the
  database, never from token claims or a request body.
- `POST /authority` reports what a token may do (`valid`, employee, team,
  authorized models). An invalid token reveals only `valid: false` and a reason.
- Gateway-owned APIs live at the root (`/admin`, `/authority`, `/healthz`), not
  under `/v1` (OpenAI's namespace) or `/api` (Ollama's).
- Never log, echo or store an employee token, the signing key or the admin key.
- Never log or store raw identity tokens or raw sensitive values. Mask regex
  matches before writing decision records.
- Prometheus label values stay bounded: team, model, policy code, kind, outcome,
  reason. Never employee IDs, prompts, tokens or free text.
- No em dashes in generated docs, comments or commit messages.

## Commands

Run from the repository root unless noted.

- Python environment: `python3 -m venv .venv && .venv/bin/pip install -e "./gateway[test]"`
- Settings: `cp gateway/.env.example gateway/.env`, then fill in the two
  required secrets (`INTENTLATCH_TOKEN_SIGNING_KEY`, `INTENTLATCH_ADMIN_API_KEY`)
  with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
- Local PostgreSQL (development only), first time:
  `docker run -d --name intentlatch-pg -e POSTGRES_USER=intentlatch -e POSTGRES_PASSWORD=intentlatch -e POSTGRES_DB=intentlatch -p 127.0.0.1:5432:5432 postgres:17`;
  afterwards `docker start intentlatch-pg`
- Ollama: the cluster's, never a local install:
  `kubectl port-forward -n upstreams svc/ollama 11434:11434`
- Dev server (from `gateway/`):
  `../.venv/bin/uvicorn intentlatch.main:app --reload --port 8080 --env-file .env`
  (binds to 127.0.0.1; `README.md` shows the admin bootstrap flow)
- Health: `curl -s localhost:8080/healthz`
- Test: `.venv/bin/pytest gateway` (pytest; tests live in `gateway/tests/`,
  named `test_<module>.py`). This is the test gate for logic-bearing changes.
- Console setup (Node 22.22+): `npm --prefix console ci`
- Console dev server: `npm --prefix console run dev -- --host 127.0.0.1`
  (standalone frontend; no gateway or cluster credentials required).
- Console typecheck: `npm --prefix console run typecheck`
- Console build: `npm --prefix console run build`
- Lint: none configured.
- Policy test cases: `intentlatch test run` is planned with the test runner and
  is not available yet. This is a product feature, separate from the project's
  own unit tests.
