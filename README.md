# IntentLatch

A self-hosted AI control layer: one gateway between a company's employees and
its corporate LLMs, governed by policies the security team writes and changes
live. Built for the HackYeah 2026 "AI Control Layer" challenge.

## Layout

- `gateway/` - the control gateway (Python, FastAPI)
- `console/` - the management console (React, Vite, TypeScript)

## Run the console locally

Use Node 22.22+ and npm. The console signs in against the gateway, so start
the gateway first (see below) and create a console account. From the
repository root:

```bash
npm --prefix console ci
npm --prefix console run dev -- --host 127.0.0.1
```

Open the local URL printed by Vite, normally `http://127.0.0.1:5173`. Vite
proxies `/console/*` and `/admin/*` to the gateway at `http://127.0.0.1:8080`;
set `INTENTLATCH_GATEWAY_URL` to use another address, for example when port
8080 is taken by a `kubectl port-forward` to the cluster:

```bash
INTENTLATCH_GATEWAY_URL=http://127.0.0.1:8081 npm --prefix console run dev -- --host 127.0.0.1
```

Geist fonts load from Google Fonts, with system font fallbacks when unavailable.

```bash
npm --prefix console run typecheck
npm --prefix console run build
```

The production files are written to `console/dist/`. Hosting must serve
`index.html` for console routes, and must route `/console/*` and `/admin/*` to
the gateway on the same origin as the console.

| Route | Page |
| --- | --- |
| `/` | Redirects to Metrics |
| `/metrics` | Metrics |
| `/teams` | Teams & identities |
| `/policies` | Policies |
| `/tests` | Test cases |
| `/reports` | Reports |
| `/login` | Sign in |

Every route except `/login` needs a signed-in console session; without one the
console opens `/login` and returns to the requested page after sign-in. Unknown
routes show a not-found page with a link to Metrics. The sidebar can collapse,
and the theme control stores only `intentlatch.theme` (`dark` or `light`) in
localStorage. Missing, invalid or blocked storage defaults to dark.

## Console accounts and roles

There are no default accounts. Create the first administrator with the
`intentlatch` command, which prompts for the password (12 to 256 characters):

```bash
set -a && . gateway/.env && set +a          # provides INTENTLATCH_DATABASE_URL
.venv/bin/intentlatch console-user create alice --role admin
.venv/bin/intentlatch console-user list
.venv/bin/intentlatch console-user password alice   # new password; ends alice's sessions
.venv/bin/intentlatch console-user delete alice
```

| Role | May |
| --- | --- |
| `viewer` | Read every console page |
| `analyst` | Everything a viewer may, plus add, edit and run test cases, and read reports |
| `admin` | Everything, including changing teams, employees and policies |

The gateway enforces roles, not the console: a console session reaches the
`/admin` API, where `GET` needs `viewer` and every change needs `admin`, except
test cases and runs, which need `analyst`, and reports, which need `analyst`
even to read because they hold prompt and response text. The admin API key keeps working for
scripts.

Sign-in creates a session that lasts 8 hours. The browser holds it in the
`intentlatch_session` cookie (HttpOnly, SameSite=Strict, and Secure over
HTTPS); the database stores only a SHA-256 of it. Passwords are stored as
salted scrypt hashes. A wrong username and a wrong password get the same
answer. Console requests that change something must carry the
`X-IntentLatch-Console` header, which a cross-site page cannot send.

| Endpoint | Does |
| --- | --- |
| `POST /console/session` | Signs in with `{"username", "password"}`; sets the cookie |
| `GET /console/session` | Returns the signed-in `{"user": {"id", "username", "role"}}` |
| `DELETE /console/session` | Signs out and clears the cookie |

## Teams & identities page

Lists teams with their authorized models. A viewer sees everything read-only.
An admin can:

- create a team with a name and at least one model; names are unique,
  ignoring case
- change a team's authorized models, which applies from its employees' next
  request
- add an employee, which shows the new employee token once, with a copy button
- reissue an employee's token, which stops the old one and shows the new one
  once
- revoke an employee, after which that employee cannot get a token again

The selected team is kept in the URL (`/teams?team=<id>`), so it survives a
reload and works with Back and Forward.

## Policies page

Lists every policy with its type, rule, action, what it applies to, whether it
is enabled, and the global policy version. Search matches codes and rules;
filters narrow by type and status. A viewer can open any policy read-only. An
admin can:

- create a policy. The form follows the choices: an AI policy asks for a
  plain-language rule; a non-AI policy asks for its kind, then a regex pattern,
  a limit's token budget, window and team, or nothing for authority. Authority
  and limit policies are fixed to block prompts.
- see as they type whether a code is valid and unused; it is stored in upper
  case. The gateway's own checks, such as an invalid Python regex or an unknown
  team, appear on the field they concern.
- edit a policy's rule, settings, action, scope and status. The code, type and
  kind cannot change, and the console sends only the fields that changed.
- enable or disable a policy from the list. Disabling asks for confirmation,
  and disabling `AUTH-MODEL` warns that every token can then call every model.

## Metrics page

The gateway's own metrics are described under [Metrics](#metrics) below.
The console's Metrics page reads these back through the gateway:
`GET /admin/metrics?range=1h|6h|24h|7d` (viewer and up) runs a fixed set of
PromQL queries against `INTENTLATCH_PROMETHEUS_URL` and returns the page's
panels. The browser never sends PromQL, so the console cannot read the rest of
the cluster's metrics. Without the setting, the endpoint answers 503
`metrics_unavailable` and the page explains what to set.

The page shows request, block, edit and error counts, p95 response time and
rejected credentials; requests by outcome over time; the policies that block and
edit most; p95 response time over time; and tables by team (with tokens), model,
stage, control agent verdict and credential failure reason. Every chart has a
table view. It refreshes every 30 seconds.

To try it locally, run a Prometheus that scrapes the local gateway, and point
the gateway at it:

```bash
cat > /tmp/prometheus.yml <<'YAML'
global: {scrape_interval: 5s}
scrape_configs:
  - job_name: intentlatch-gateway
    static_configs: [{targets: ["127.0.0.1:8080"]}]
YAML
docker run -d --name intentlatch-prometheus --network host \
  -v /tmp/prometheus.yml:/etc/prometheus/prometheus.yml:ro,Z prom/prometheus:v3.5.0 \
  --config.file=/etc/prometheus/prometheus.yml --web.listen-address=127.0.0.1:9090
echo INTENTLATCH_PROMETHEUS_URL=http://127.0.0.1:9090 >> gateway/.env   # then restart the gateway
```

In the cluster, set `INTENTLATCH_PROMETHEUS_URL` to the monitoring stack's
Prometheus service, and allow the gateway to reach it.

## Test cases page

Built on the gateway's [test cases](#test-cases) API. Analysts and admins can
add a case, edit any case (predefined ones included; codes are permanent),
duplicate one, and run one case or all of them. A run answers only when its last
case ends, so while it runs the page finds it in the run history and polls it:
results appear as each case finishes. Each case shows its latest result from
the recent runs, and the Run history tab shows every result with the policies
that fired. Viewers see everything read-only.

The form follows the expected outcome: a policy that must fire for `EDIT` and
`BLOCK`, and for `EDIT` the strings the rewrite must not contain. A case runs as
any active employee, by default the seeded test runner, whose team's models
apply.

## Reports page

Built on the gateway's [reports](#reports) API, which reads the
[decision log](#decision-log); the log stores only masked text. Besides the regex
masking described there, any run of 32 or more encoding characters that mixes
letters and digits is stored as `[encoded value]`: a policy can match only a
secret's first line, as with a PEM `BEGIN ... PRIVATE KEY` header, and the key
material follows it.

The page picks a range (last hour, 24 hours, 7 or 30 days, or a custom range),
then filters the report's items by outcome, side, team, policy and text, newest
first, and keeps the filters in the URL. It shows totals, the policies that fired
and teams; opening an item shows each policy's reasoning, the control agent's
verdict, the masked text and the masked rewrite when the text was edited. The
items and totals CSV exports, and the JSON download, cover the whole range.
Reports need the analyst role, because they hold prompt text.

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

A chat body that cannot be passed on as plain JSON, for example one with a `NaN`
or `Infinity` number or an unpaired surrogate escape such as `\ud800`, answers
400 `invalid_request` before any policy runs.

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
  Each message's text and its tool-call arguments are checked, and so is every
  tool definition in `tools`, as JSON, because the model reads those too.
  `response_format` and `format` are not checked. `/check` treats `prompt` as
  one user message.
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
long numbers. A pattern wrapped in a lookahead, `(?=...)`, is tried at every
position and stands for what its `luhn` group captured, which is what gets
masked and handed to the control agent. `RGX-CARD` works this way, so a card
next to its expiry date, its CVV or a year still matches. A database seeded
before that change keeps its old `RGX-CARD` pattern until an admin replaces it
with `PATCH /admin/policies/RGX-CARD`, since seeding never overwrites a policy.
Patterns run without a timeout, so avoid nested unbounded repeats such as
`(a+)+`.

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
- **Tools.** Tool-call arguments and tool definitions are never rewritten, so an
  `edit` match there blocks through that re-check.

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
5 to 15 s per call on CPU. Disable a seed to skip it. Tool definitions go to the
agent too, so a request with many large tools makes its call slower and can
overflow the model's context window (4096 tokens in the cluster).

## Decision log

Every chat request that reaches a prompt decision leaves records in the
append-only `decision_records` table: one for the prompt and, when the answer was
checked, one for the response. A record holds the outcome, the control agent's
status, every policy's result and reasoning, the source text and any rewrite, the
employee, team and model, tokens, compute seconds, stage latencies and the policy
version. Tokens, upstream time and auth time sit on the prompt record, so totals
never count a request twice. `/check` writes nothing.

Every regex match is masked as `[CODE]` before anything is stored, reasoning
included. A value that only shows up once decoded (Base64, URL encoding, invisible
characters) masks its whole piece as `[masked: CODE]`, so raw sensitive values
never persist.

Records are hash-chained: each `hash` is the SHA-256 of the previous record's
`hash` followed by the record's canonical JSON (sorted keys, no spaces), starting
from 64 zeros. Triggers refuse UPDATE, DELETE and TRUNCATE. If the records cannot
be written, the request answers 503 `database_unavailable` instead of its answer.

```bash
docker exec intentlatch-pg psql -U intentlatch -c \
  "SELECT seq, direction, outcome, control_agent_status, source_masked FROM decision_records ORDER BY seq DESC LIMIT 5"
```

## Metrics

`GET /metrics` serves the Prometheus text format and needs no token. Label values
are only team names, models, policy codes, kinds, outcomes, directions, stages and
reasons: never employees, prompts, tokens or free text. Each chat request is
counted when it ends, from the same trace the decision log writes.

| Metric | Type | Labels |
|---|---|---|
| `intentlatch_requests_total` | counter | team, model, outcome |
| `intentlatch_policy_enforcements_total` | counter | policy_code, kind, action, outcome, direction, team, model |
| `intentlatch_policy_checks_total` | counter | policy_code, kind, result |
| `intentlatch_policy_check_duration_seconds` | histogram | kind |
| `intentlatch_request_duration_seconds` | histogram | stage (auth, non_ai, control_agent, upstream, total) |
| `intentlatch_tokens_total` | counter | team, model, direction |
| `intentlatch_compute_seconds_total` | counter | team, model |
| `intentlatch_team_token_usage_ratio` | gauge | team |
| `intentlatch_auth_failures_total` | counter | reason (missing, invalid, revoked, model_not_authorized) |
| `intentlatch_upstream_errors_total` | counter | model, reason |
| `intentlatch_control_agent_verdicts_total` | counter | direction, status |
| `intentlatch_control_agent_errors_total` | counter | reason (timeout, unreachable, unparseable, missing_rewrite) |
| `intentlatch_rewrite_rejections_total` | counter | policy_code |
| `intentlatch_test_cases_total` | counter | result (passed, failed) |
| `intentlatch_policies_active` | gauge | kind (authority, limit, regex, ai) |

`intentlatch_policy_enforcements_total` counts the policies responsible for each
blocked or edited prompt or response: for a block, every violated `block` policy,
plus an `edit` regex policy whose rewrite still matched (`action="edit"`,
`outcome="blocked"`); for an edit, every violated `edit` policy. The usage ratio
is the team's strictest limit, set whenever its usage loads. Histogram buckets run
from 5 ms to 120 s. The test runner fills `intentlatch_test_cases_total`.

```bash
curl -s localhost:8080/metrics | grep '^intentlatch_requests_total'
```

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

## Test cases

A test case states what must happen to a prompt: the prompt, the model, the
employee it runs as, the expected outcome (`ALLOW`, `EDIT` or `BLOCK`),
optionally the policy code that must fire, and for an `EDIT` case the strings the
rewritten prompt must not contain.

The gateway seeds a test team, `intentlatch-tests`, authorized for `corporate-a`
only, with one employee, `test-runner`, whose token is never issued. The
predefined cases run as that employee. Missing cases are inserted at startup, so
edits to a predefined case survive restarts.

| Predefined cases | Expected |
|---|---|
| `TC-BENIGN-QUESTION`, `TC-BENIGN-TASK` | `ALLOW` |
| `TC-AUTH-MODEL` (asks for `corporate-b`) | `BLOCK` by `AUTH-MODEL` |
| `TC-RGX-CLOUD-KEY`, `TC-RGX-CLOUD-KEY-HIDDEN` (zero-width split), `TC-RGX-CLOUD-KEY-BASE64` | `BLOCK` by `RGX-CLOUD-KEY` |
| `TC-RGX-GIT-TOKEN`, `TC-RGX-PRIVATE-KEY` | `BLOCK` by that policy |
| `TC-RGX-EMAIL`, `TC-RGX-PHONE`, `TC-RGX-IBAN`, `TC-RGX-PESEL`, `TC-RGX-CARD` | `EDIT` by that policy, with the value gone |
| `TC-AI-NO-JAILBREAK`, `TC-AI-NO-CREDENTIALS` | `BLOCK` by that policy |

Runs use check mode. Each case goes through `POST /check` as its employee, with a
token the gateway signs for 60 seconds and never shows, so revoking or reissuing
the employee applies to its cases too. Nothing is forwarded to a corporate LLM
and nothing reaches the decision log. A case passes when the outcome matches, the
expected policy is one of those responsible for it, no listed string is left in
the rewrite (ignoring case), and the control agent did not fail. Cases that reach
the control agent take seconds each on CPU and fail while it is unreachable.
Limit policies and the response-only `AI-NO-COMMITMENTS` and `RGX-EXFIL-IMAGE`
have no predefined case, because check mode counts no tokens and sees no answer.

```bash
curl -s localhost:8080/admin/test-cases -H "$ADMIN" -H 'content-type: application/json' \
  -d '{"code": "TC-IBAN-DE", "name": "A German IBAN is removed", "prompt": "Pay DE89 3704 0044 0532 0130 00 today",
       "model": "corporate-a", "expected": "EDIT", "expected_policy_code": "RGX-IBAN",
       "must_not_contain": ["DE89 3704 0044 0532 0130 00"]}'
# -> 201 {"test_case": {"code": "TC-IBAN-DE", ..., "run_as_employee_id": "7e57ca5e-...", "predefined": false, ...}}

curl -s -X PATCH localhost:8080/admin/test-cases/TC-IBAN-DE -H "$ADMIN" \
  -H 'content-type: application/json' -d '{"name": "IBAN, German format"}'
curl -s localhost:8080/admin/test-cases -H "$ADMIN"

curl -s localhost:8080/admin/test-runs -H "$ADMIN" -H 'content-type: application/json' \
  -d '{"cases": ["TC-AUTH-MODEL"]}'
# -> 201 {"test_run": {"id": "<run-id>", "mode": "check", "status": "passed",
#      "totals": {"total": 1, "passed": 1, "failed": 0}, "results": [{"case_code": "TC-AUTH-MODEL",
#      "expected": "BLOCK", "actual": "BLOCK", "fired_policy_codes": ["AUTH-MODEL"], "passed": true, "failure": null, ...}]}}
curl -s localhost:8080/admin/test-runs -H "$ADMIN"               # the newest 50 runs
curl -s localhost:8080/admin/test-runs/<run-id> -H "$ADMIN"      # one run with its results
```

`run_as_employee_id` defaults to the test employee. `expected_policy_code` is not
allowed on `ALLOW` cases and `must_not_contain` only on `EDIT` cases. Codes cannot
change and cases cannot be deleted. A run without `cases` runs every case in code
order. It answers when its last case ends, stores each result as soon as its case
finishes, and passes only when at least one case ran and every case passed. A
case that gets no decision, for example because its employee was revoked, has
`actual` `ERROR`. Each result counts in `intentlatch_test_cases_total`.

### `intentlatch test run`

The same run from a shell, for people and CI. It calls the running gateway with
the admin key from `INTENTLATCH_ADMIN_API_KEY` and exits 0 when every case
passed, 1 when any failed, and 2 when the run could not start (no key, gateway
unreachable, an error answer).

```bash
INTENTLATCH_ADMIN_API_KEY=$(grep '^INTENTLATCH_ADMIN_API_KEY=' gateway/.env | cut -d= -f2-) \
  .venv/bin/intentlatch test run          # options: --case TC-AUTH-MODEL (repeatable), --url http://host:8080
# Test run <run-id>: check mode, policy version 4
# PASS  TC-AUTH-MODEL  BLOCK -> BLOCK  fired AUTH-MODEL  2 ms
# ...
# 15 passed, 0 failed, 15 total: passed

kubectl exec -n intentlatch-system deploy/intentlatch-gateway -- intentlatch test run
```

The command ships with the gateway package, so an environment created before it
needs `.venv/bin/pip install -e "./gateway[test]"` again. It defaults to
`http://127.0.0.1:8080`, which is the gateway inside its container too.

## Reports

A report lists every edited and blocked prompt and response in a past time
range, read from the decision log, with the policies that fired and their
reasoning, plus totals by policy, team and model. Reports need the admin key, or
a console session with the analyst or admin role.

```bash
RANGE='from=2026-10-04T00:00:00Z&to=2026-10-04T06:00:00Z'
curl -s "localhost:8080/admin/reports?$RANGE" -H "$ADMIN"
# -> {"from": "2026-10-04T00:00:00+00:00", "to": "2026-10-04T06:00:00+00:00", "generated_at": "...",
#     "totals": {"blocked": 3, "edited": 5, "total": 8,
#                "by_policy": [{"code": "RGX-EMAIL", "blocked": 0, "edited": 4, "total": 4}, ...],
#                "by_team": [{"team": "payments", ...}, ...], "by_model": [{"model": "corporate-a", ...}, ...]},
#     "items": [{"ts": "...", "request_id": "...", "direction": "prompt", "outcome": "edited", "team": "payments",
#                "model": "corporate-a", "employee_id": "...", "employee": "Ana", "control_agent_status": "modified",
#                "policies": [{"code": "RGX-EMAIL", "kind": "regex", "ai": false, "action": "edit",
#                              "reasoning": "The prompt matches this policy's pattern."}],
#                "source_masked": "Write to [RGX-EMAIL]", "rewritten_masked": "Write to [removed]",
#                "policy_version": 4, "test_run_id": null}, ...]}

curl -sOJ "localhost:8080/admin/reports/items.csv?$RANGE" -H "$ADMIN"    # saves intentlatch-report-...-items.csv
curl -sOJ "localhost:8080/admin/reports/totals.csv?$RANGE" -H "$ADMIN"   # saves intentlatch-report-...-totals.csv
```

- **Range.** `from` and `to` are required ISO 8601 times with a timezone; `from`
  is included and `to` is not. Write UTC as `Z`, since a `+` in a query string
  reads as a space.
- **Items.** One per checked prompt or response that was edited or blocked,
  oldest first. The text is the decision log's masked text, and
  `rewritten_masked` is the edit. `policies` lists the violated policies; a block
  caused by a control-agent failure lists none and shows
  `control_agent_status: "error"`.
- **Totals** count those items: for each policy the items it fired on, and for
  each team and model their items, sorted with the most first.
- **CSV.** `items.csv` has one row per item, with the fired codes separated by
  spaces and one `CODE: reasoning` line per policy. `totals.csv` has
  `dimension, key, blocked, edited, total` rows, the `all` row first. Both are
  UTF-8. A cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return
  gets a leading `'`, so a spreadsheet shows the prompt text instead of running
  it as a formula.

Report answers are never cached (`Cache-Control: no-store`), and every matching
record comes back at once, with no paging.
