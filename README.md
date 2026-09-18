# GridWise Energy Optimizer

BUP CSE Fest 2026 Hackathon, Online Preliminary.

An HTTP API that reads campus operator notes with a language model, turns them into validated structured directives, and computes the minimum-cost 24-hour grid / solar / battery schedule that respects every directive and battery rule.

| | |
|---|---|
| Health | `GET /health` → `{"status":"ok"}` |
| Main endpoint | `POST /optimize-energy` |
| Live base URL | `<FILL IN: deployed URL>` |
| Docker image | `ghcr.io/arafatislam123/gridwise:v1.0.0` (digest: `sha256:e30d0df8f4a064fbbe887f32b69e1435990943d1ba38f5921202154d123bae00`) |
| Language model used for judging | Groq, `llama-3.3-70b-versatile` (backup: Groq `llama-3.1-8b-instant`) |
| Optimizer | Linear programming, HiGHS solver via `scipy.optimize.linprog` |
| Port | `8000` (override with `PORT`) |

---

## 1. Quickstart (local, from a clean machine)

Requirements: Python 3.11+ and git.

```bash
git clone https://github.com/arafatislam123/Team_OncodoseRx1.git gridwise
cd gridwise

python -m venv .venv
source .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

cp .env.example .env                 # Windows: copy .env.example .env
# edit .env: set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL (see section 3)

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In a second terminal:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl -X POST http://localhost:8000/optimize-energy \
     -H "Content-Type: application/json" \
     -d @samples/request_sample01.json

python scripts/run_samples.py --url http://localhost:8000
```

Expected result of the last command: `10/10 cases passed`, with every case showing `ok` for interpretation and plan, and each cost equal to the reference cost.

To run the official public sample file instead of the bundled copy:

```bash
python scripts/run_samples.py --url http://localhost:8000 --file path/to/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json
```

Unit and API tests (no model key needed, the model is mocked):

```bash
python -m pytest -q
```

More checks:

```bash
# model key and model id valid, one real interpretation call with latency
python scripts/check_model.py

# interpretation accuracy of the configured model on 32 reworded notes (no server needed)
python scripts/run_paraphrases.py

# load test: status codes and p50/p95 latency under concurrency
python scripts/load_test.py --url http://localhost:8000 --requests 30 --concurrency 5

# optimizer only: feed each sample its expected directives, compare with the reference cost
python scripts/check_solver.py
```

---

## 2. Docker fallback

```bash
docker pull ghcr.io/arafatislam123/gridwise:v1.0.0
docker run --rm -p 8000:8000 \
  -e LLM_BASE_URL=<provider base url> \
  -e LLM_API_KEY=<your key> \
  -e LLM_MODEL=<model id> \
  ghcr.io/arafatislam123/gridwise:v1.0.0

curl http://localhost:8000/health
```

Or with an env file: `docker run --rm -p 8000:8000 --env-file .env ghcr.io/arafatislam123/gridwise:v1.0.0`.

The image listens on `0.0.0.0:8000`, has a built-in `HEALTHCHECK`, runs as a non-root user, and contains **no credentials**; keys are only passed at run time.

The published image is built by GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)): every push runs the tests, builds the image, starts it, checks `/health` and one public sample inside the container, and only then pushes it to GHCR. A `v*` git tag publishes the image under that tag.

Build it locally instead:

```bash
docker build -t gridwise:local .
docker run --rm -p 8000:8000 --env-file .env gridwise:local
```

### Hosting

Any always-on container host works (the service only needs the env vars from section 3).

- **Render:** `render.yaml` is included. New → Blueprint → select the repo, enter the `LLM_*` values when asked. Health check path is `/health`.
- **Railway / Fly.io / a VM:** deploy the Dockerfile, set the `LLM_*` variables; the container reads `PORT` if the platform sets one.
- **Buildpack platforms:** `Procfile` is included.

Avoid free tiers that sleep when idle; a cold start during judging hurts both the readiness and the latency checks.

---

## 3. Configuration

All configuration is through environment variables (a local `.env` file is also read if present; real environment variables take priority).

| Variable | Required | Default | Meaning |
|----------|----------|---------|---------|
| `LLM_BASE_URL` | yes | – | Base URL of an OpenAI-compatible chat completions API (without `/chat/completions`) |
| `LLM_API_KEY` | yes* | – | API key for that provider (*not needed for a local server) |
| `LLM_MODEL` | yes | – | Model id |
| `LLM_BACKUP_BASE_URL` | no | – | Backup provider, used if the primary fails |
| `LLM_BACKUP_API_KEY` | no | – | Backup key |
| `LLM_BACKUP_MODEL` | no | – | Backup model id |
| `LLM_TIMEOUT_S` | no | `8` | Timeout per model call |
| `REQUEST_DEADLINE_S` | no | `20` | Total time budget per request (judge limit is 30 s) |
| `ENABLE_DEGRADED_FALLBACK` | no | `true` | Outage fallback, see section 5 |
| `CACHE_SIZE` | no | `512` | Number of cached note interpretations |
| `LOG_LEVEL` | no | `INFO` | Log level |
| `PORT` | no | `8000` | HTTP port |
| `WEB_CONCURRENCY` | no | `2` | Uvicorn workers (Docker image) |

The client talks to any OpenAI-compatible endpoint, so the provider can be switched without code changes. Example base URLs:

| Provider | `LLM_BASE_URL` |
|----------|----------------|
| OpenAI | `https://api.openai.com/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Google Gemini (OpenAI-compatible) | `https://generativelanguage.googleapis.com/v1beta/openai` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Ollama (local) | `http://localhost:11434/v1` |

Pick a fast instruction-following model that can return JSON. The service asks for JSON mode and automatically retries without it if a provider rejects that option.

---

## 4. API

### `GET /health`

`200 {"status":"ok"}`. It does not depend on the model provider, so a provider hiccup never marks the service as not ready.

### `POST /optimize-energy`

Request: `scenario_id`, `operator_notes` (1-3 non-empty strings), `hours` (24 entries, hours 0-23 once each, any order), `battery` (5 fields). Exactly as in the Problem Statement.

Response (hourly plan shortened):

```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {"note_index": 0, "applies": true, "directive_type": "solar_reduction",
     "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
     "explanation": "..."},
    {"note_index": 1, "applies": false, "directive_type": "no_op",
     "structured_adjustment": null, "explanation": "..."}
  ],
  "hourly_plan": [
    {"hour": 0, "grid_kwh": 90.0, "solar_used_kwh": 0.0, "battery_action": "idle",
     "battery_kwh": 0.0, "battery_energy_after_kwh": 110.0},
    {"hour": 1, "grid_kwh": 45.0, "solar_used_kwh": 0.0, "battery_action": "discharge",
     "battery_kwh": 40.0, "battery_energy_after_kwh": 70.0}
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365.0,
  "peak_grid_kwh": 175.0,
  "plan_summary": "Applied 1 operator directive(s) (solar_reduction). Ignored 1 note(s) ..."
}
```

Status codes:

| Code | When |
|------|------|
| 200 | Success |
| 400 | Malformed JSON (including `NaN`/`Infinity`), missing fields, wrong types, wrong array sizes, duplicate or missing hours, empty notes |
| 422 | Well-formed but impossible values (negative demand, initial energy outside `[minimum, capacity]`, ...) |
| 500 | Controlled internal error: `{"error":"internal error","details":{"ref":"..."}}`. No stack traces or configuration are ever returned |

---

## 5. How it works

```
request → validation → note interpreter (language model) → normalizer → guardrails
        → constraint builder → LP optimizer (HiGHS) → plan builder → replay validator → response
```

Full design notes: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

**Role of the language model.** All notes of a request are sent in one call. For each note the model decides whether it applies to today's schedule, which of the six directive types it is, the time window(s) as stated, and the value with its unit (for example `{"amount": 80, "unit": "percent_reduction"}` or `{"amount": 50, "unit": "percent_of_capacity"}`). The model's output is the only source of the directive; nothing reaches the optimizer without it passing the checks below.

**Deterministic normalizer.** Code, not the model, does the arithmetic: windows `[start, end)` become sorted unique hour lists (including windows that cross midnight), `percent_reduction` becomes `factor = 1 - p/100`, `percent_of_capacity` becomes kWh using the request's battery capacity.

**Guardrails** (Problem Statement section 8), applied to every note:
- exactly one entry per note, in `note_index` order, no duplicates or gaps
- `directive_type` must be one of the six supported values
- `applies` is derived from the type (`false` only for `no_op`), `no_op` always has `structured_adjustment = null`
- hours are integers 0-23, unique, ascending, non-empty
- solar `factor` in [0, 1]; reserve finite, ≥ 0 and ≤ capacity; grid cap finite and ≥ 0
- only the keys required for the type are returned; demand, tariff and battery data can never be changed by a note

**Failure chain for interpretation:** cache → primary model → one repair call (the model gets the exact validation errors) → backup model → degraded fallback → `no_op`. The degraded fallback is a conservative pattern extractor for plain phrasings that runs **only** when no model provider returns usable output (outage, quota, repeated invalid output). Its results pass through the same guardrails. It can be switched off with `ENABLE_DEGRADED_FALLBACK=false`. A note that cannot be interpreted safely becomes `no_op` rather than an invented constraint.

**Optimizer.** A linear program over 24 hours (grid, solar used, charge, discharge, battery energy per hour) minimizing `Σ tariff × grid`, with energy balance, battery transitions, capacity, minimum energy, rate limits, end-of-day neutrality, and the directives as hard constraints (effective solar, reserve floor, zero charge / discharge, grid cap). Solved with HiGHS. On all ten public samples it reaches exactly the reference cost. If directives ever conflict, it re-solves with only the directive limits softened (heavily penalized) so a physically valid plan is still returned, and the summary says which directive could not be met.

**Plan builder and replay.** Charge and discharge in the same hour are netted into one action, values are rounded, then grid and battery energy are recomputed from the rounded numbers so balance and transitions hold exactly. Totals come from the final rows. An independent replay validator re-checks every rule on every response.

---

## 6. Project layout

```
app/
  main.py               FastAPI app, routes, error handling
  config.py             environment settings
  service.py            pipeline orchestration
  schemas/              request, response and directive models
  interpret/            prompt, model client, parser, normalizer, guardrails, cache, fallback
  optimize/             constraint builder, LP solver, plan builder
  validate/replay.py    independent plan checker
samples/                public sample cases + a single request file for curl
scripts/run_samples.py      judge-style runner against a live URL
scripts/run_paraphrases.py  interpretation accuracy on reworded notes
scripts/load_test.py        concurrency / latency check
scripts/check_solver.py     solver check with the expected directives (no model needed)
scripts/check_model.py      verifies the model key, model id and latency
tests/                  unit + API tests (model mocked), tests/data/paraphrases.json
docs/ARCHITECTURE.md    requirement analysis and design
```

---

## 7. Security and secrets

- Keys are read from environment variables only. `.env` is in `.gitignore` and `.dockerignore`; `.env.example` contains names only.
- The Docker image contains no credentials.
- Logs never contain keys, request headers or prompts; error responses never contain stack traces, provider error bodies or configuration.
- Operator notes are treated as untrusted data. Whatever a note says, only the five supported directive shapes can come out of the guardrails.

---

## 8. Known limitations

- Interpretation quality depends on the configured model; test a new model with `scripts/run_samples.py` before switching.
- The degraded fallback only understands explicit phrasings (clock times, %, kWh). Spelled-out times like "from one until three" need the model; when the fallback can't read a time it returns `no_op` rather than guessing a window.
- A solar reduction given as an absolute kWh amount (rather than a fraction) cannot be expressed as a single `factor` and is not supported.
- The interpretation cache is in-memory per worker process and is cleared on restart.
- If two directives of the same type cover the same hour, the strictest reading is used (highest reserve, lowest cap, solar factors multiplied).

---

## 9. Dependencies and credits

| Package | Use |
|---------|-----|
| FastAPI, Starlette | HTTP API |
| Uvicorn | ASGI server |
| Pydantic | request / response validation |
| httpx | model API client |
| NumPy, SciPy (HiGHS solver) | linear programming |
| pytest | tests |
| Groq API (`llama-3.3-70b-versatile`, `llama-3.1-8b-instant`) | language model for note interpretation |

<!-- FILL IN: any other tools used, per the rulebook's credit requirement -->
