# IntentLatch

A self-hosted AI control layer: one gateway between a company's employees and
its corporate LLMs, governed by policies the security team writes and changes
live. Built for the HackYeah 2026 "AI Control Layer" challenge.

## Layout

- `gateway/` - the control gateway (Python, FastAPI)
- `console/` - the standalone management console shell (React, Vite, TypeScript)

## Run the console locally

Use Node 22.22+ and npm. From the repository root:

```bash
npm --prefix console ci
npm --prefix console run dev -- --host 127.0.0.1
```

Open the local URL printed by Vite, normally `http://127.0.0.1:5173`.
The frontend runs independently of the gateway, its `.env`, and the cluster.
Geist fonts load from Google Fonts, with system font fallbacks when unavailable.

```bash
npm --prefix console run typecheck
npm --prefix console run build
```

The production files are written to `console/dist/`. Future hosting must serve
`index.html` for console routes to support direct links and reloads.

| Route | Page |
| --- | --- |
| `/` | Redirects to Metrics |
| `/metrics` | Metrics |
| `/teams` | Teams & identities |
| `/policies` | Policies |
| `/tests` | Test cases |
| `/reports` | Reports |
| `/login` | Standalone login presentation |

The five management pages contain placeholders for later features. Unknown
routes show a not-found page with a link to Metrics. The sidebar can collapse,
and the theme control stores only `intentlatch.theme` (`dark` or `light`) in
localStorage. Missing, invalid or blocked storage defaults to dark.

This is feature 14a: the frontend shell. Authentication, sessions, accounts and
server-enforced roles are deferred to 14b. The login form checks a nonblank
username and a nonempty password, then announces "Sign-in is unavailable."
It stays on `/login`; credentials are neither submitted nor persisted by the
application. The console has no trusted current user or gateway connection yet.

For a visual review, open Metrics and Login at 1440x900 and 1280x900 in each
theme. Visit all five links, reload a direct route, and use browser Back/Forward.
Use Tab to reach the skip link, navigation, collapse and theme controls. On
Login, submit empty fields, a whitespace-only username, and then nonempty
fields; check focus, feedback and the password visibility control. Leave and
return to confirm the form is empty. Reload to check theme persistence.

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
# -> {"policies": [{"code": "AUTH-MODEL", ...}, {"code": "RGX-CARD", ...}, ...], "version": 1}

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

## Regex policies

A regex policy's `pattern` uses Python `re` syntax and is case-sensitive unless it
starts with `(?i)`. `applies_to` decides whether it runs on the prompt, on the
model's answer, or on both:

- **The prompt** is every message in a chat request, whatever its role (`system`,
  `user`, `assistant` or `tool`), because the client controls the whole history.
  Each message's text and its tool-call arguments are checked. `/check` treats
  `prompt` as one user message.
- **The answer** is the model's text and tool-call arguments. It is checked
  before anything reaches the caller; streamed answers are buffered first.

Before matching, the gateway strips zero-width and bidi characters (every Unicode
format character) and decodes URL-encoded text and Base64 runs of 12 or more
characters. Patterns run on the text and on each decoded form, so an encoding
trick does not slip past them. What is forwarded never changes.

A violated `block` policy answers 403 `policy_blocked` with its code, and a
blocked answer is discarded. An `edit` match goes to the control agent, which
rewrites the text (see below). The reasoning never repeats the matched text.

A pattern with a group named `luhn` counts a match only when the digits that
group captured pass the Luhn checksum, which is how `RGX-CARD` skips most random
long numbers. Patterns run without a timeout, so avoid nested unbounded repeats
such as `(a+)+`.

| Seeded policy | Finds | Action | Applies to |
|---|---|---|---|
| `RGX-EMAIL` | email addresses | edit | both |
| `RGX-PHONE` | `+` international numbers, and Polish numbers written `600 700 800` | edit | both |
| `RGX-IBAN` | IBANs, grouped in fours or not | edit | both |
| `RGX-PESEL` | PESEL numbers (birth-date structure, no checksum) | edit | both |
| `RGX-CARD` | Luhn-valid card numbers | edit | both |
| `RGX-CLOUD-KEY` | AWS access key IDs and Google API keys | block | both |
| `RGX-GIT-TOKEN` | GitHub and GitLab tokens | block | both |
| `RGX-PRIVATE-KEY` | PEM, OpenSSH and PGP private keys | block | both |
| `RGX-EXFIL-IMAGE` | markdown images whose URL carries a query string | block | response |

## Limit policies

A limit policy caps the tokens a team may use in a time window:

```bash
curl -s localhost:8080/admin/policies -H "$ADMIN" -H 'content-type: application/json' \
  -d '{"code": "LIM-PAYMENTS", "ai": false, "kind": "limit",
       "params": {"max_tokens": 20000, "window_seconds": 3600, "team": "payments"},
       "action": "block", "enabled": true}'
```

- **Who it applies to.** `team` names one team. `null` applies to every team,
  each against its own usage. Every limit that applies is checked, so the
  strictest one decides.
- **The window.** Windows are fixed and aligned to multiples of `window_seconds`
  since the Unix epoch (UTC): `3600` is the clock hour and `86400` the UTC day.
  The database clock decides, so every gateway replica sees the same windows.
- **What counts.** When the corporate LLM answers, the prompt and answer tokens it
  reports are added to the caller's team, one row per request in the
  `token_usage` table. An answer that a response policy then blocks still counts.
  A failed upstream call and `/check` count nothing.
- **When it blocks.** A team that has used `max_tokens` or more in the current
  window is refused before anything is forwarded. A request admitted below the
  limit is served in full and counted afterwards, so usage can end above
  `max_tokens`.

Creating, editing or disabling a limit applies from the next request, against the
usage already counted in its window. A block by limit policies alone answers 429
`limit_exceeded`; if an authority policy is violated too, the answer is the usual
403 `policy_blocked`, naming both.

```bash
curl -s localhost:8080/v1/chat/completions -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"model": "corporate-a", "messages": [{"role": "user", "content": "Hello"}]}'
# -> 429 {"error": {"code": "limit_exceeded", "message": "Blocked by LIM-PAYMENTS: Team payments has used 20412 of 20000
#          tokens in the 3600-second window ending 2026-10-04T02:00:00+00:00.", "policies": [{"code": "LIM-PAYMENTS",
#          "kind": "limit", "reasoning": "..."}], "policy_version": 4}}
```

## Control agent and AI policies

An AI policy is a rule in plain language that the control agent (Qwen2.5 3B on
Ollama) checks. The gateway calls the agent at most once per direction, and only
when an AI policy applies to that direction or an `edit` regex policy matched.
Authority, limit and `block` regex violations end the request before it. The
agent gets the text pieces, the verified identity (employee, team and authorized
models, never the token), the AI policies and the values the `edit` regex
policies matched. It answers `pass`, `blocked` or `modified`, with a rewrite of
every piece when it changed something.

The gateway applies each violated policy's own action, whatever the agent's
status says:

- **Block.** A violated `block` policy blocks with 403 `policy_blocked`.
- **Edit.** A violated `edit` policy forwards the rewritten prompt, or returns
  the rewritten answer (as JSON or replayed as a stream). Just before that, the
  matched `edit` regex policies run again on the rewrite; if one still matches,
  the request is blocked under that policy's code.
- **Tool calls.** Tool-call arguments are never rewritten, so an `edit` match
  there blocks through that re-check.

A timeout, an unreachable agent, an unparseable verdict or a missing rewrite is a
control-agent failure. With `INTENTLATCH_ENVIRONMENT=production` (the default) the
request is blocked with 503 `control_agent_failed`; with `development` it goes
through unchanged and the AI policy results show `error`. The agent runs on
`INTENTLATCH_CONTROL_AGENT_URL` with `INTENTLATCH_CONTROL_AGENT_MODEL`, which
default to the Ollama URL and corporate A's model.

```bash
curl -s localhost:8080/check -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"model": "corporate-a", "prompt": "Write to jan.kowalski@firma.pl"}'
# -> {"outcome": "edited", "policy_version": 4, "policy_results": [...], "rewritten": "Write to [removed]",
#      "control_agent_status": "modified", "responsible": ["RGX-EMAIL"]}
```

| Seeded AI policy | Rule | Action | Applies to |
|---|---|---|---|
| `AI-NO-CREDENTIALS` | no asking for or sharing passwords, keys or tokens | block | both |
| `AI-NO-JAILBREAK` | no attempts to make the AI ignore its instructions | block | prompt |
| `AI-NO-COMMITMENTS` | no binding price, discount or refund promises | edit | response |

With these seeds enabled every chat request calls the agent, which takes roughly
5 to 15 s per call on CPU. Disable a seed to skip it.

## Check a prompt

`POST /check` runs a prompt through the policy pipeline as the token's employee
and returns every policy's decision without forwarding anything to a model. It
answers 200 even when the outcome is `blocked`.

```bash
curl -s localhost:8080/check -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"model": "corporate-b", "prompt": "Hello"}'
# -> {"outcome": "blocked", "policy_version": 1, "policy_results": [{"code": "AUTH-MODEL", "kind": "authority",
#      "ai": false, "action": "block", "result": "violated", "reasoning": "...", "latency_ms": 0.002}], "rewritten": null,
#      "control_agent_status": "skipped", "responsible": ["AUTH-MODEL"]}

curl -s localhost:8080/check -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"model": "corporate-a", "prompt": "My key is AKIA​IOSFODNN7EXAMPLE"}'
# -> {"outcome": "blocked", "policy_version": 1, "policy_results": [{"code": "AUTH-MODEL", "result": "pass", ...},
#      {"code": "RGX-CARD", "result": "pass", ...}, {"code": "RGX-CLOUD-KEY", "kind": "regex", "ai": false,
#      "action": "block", "result": "violated", "reasoning": "The prompt matches this policy's pattern.", ...}, ...],
#      "rewritten": null, "control_agent_status": "skipped", "responsible": ["RGX-CLOUD-KEY"]}
```

An authority or limit block stops the pipeline, so the first answer has no regex
results. `/check` reports limit policies but never counts tokens.
