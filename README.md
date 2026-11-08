# GeoChronos Time-Travel Concierge

A chat app for a fictional time-travel travel service whose information is real. Ask about ancient sites, find what was built in a time window, or ask to "visit" a site in a given year: if the year does not fit the research record, the assistant says so and suggests a valid one. Data comes from the GeoChronos research project (95 sites, dating claims with status and confidence, citations). Built from the class Bakery Copilot project: chat UI, backend with an agent loop and five tools, Postgres, and a morning digest, all on Railway.

The plan lives in five files: [spec.md](spec.md) (what), [architecture.md](architecture.md) (how and where), [practice.md](practice.md) (how we work), [runbook.md](runbook.md) (when it breaks), [docs/tickets.md](docs/tickets.md) (the work, as issues). `CLAUDE.md` points Claude Code at the first three.

## Run it locally

macOS and Linux:

```bash
cp .env.example .env         # set APP_PASSWORD, APP_SECRET, ANTHROPIC_API_KEY
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Windows, in PowerShell 7 with Python 3.12 (`winget install Python.Python.3.12`):

```powershell
Copy-Item .env.example .env  # set APP_PASSWORD, APP_SECRET, ANTHROPIC_API_KEY
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

If PowerShell refuses to run `Activate.ps1` ("running scripts is disabled on this system"), run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` once, or skip activation and start each command with `.venv\Scripts\python -m`, for example `.venv\Scripts\python -m uvicorn app.main:app --reload`.

Open http://localhost:8000, type a name and the password. The first start seeds SQLite with the GeoChronos export: 95 sites, 280 dating claims, 245 citations (a few seconds).

Try: "When was the Great Pyramid built?", "I'd like to visit the Great Pyramid around 2550 BC", or "I'd like to visit the Great Pyramid around 25500 BC" (too early: the assistant suggests a valid year).

## Tests and evals

```bash
pytest -q                    # model mocked; seconds; no key needed
python -m evals.run          # real model; 12 cases; appends evals/history.csv
python -m evals.run --judge  # also grades wording with a cheaper model
```

The same three commands work in PowerShell once `.venv` is active.

Tests prove the deterministic parts (parsing, dedup, limits, streaming, the tool loop with a scripted model). Evals prove the agent still behaves after a prompt, tool or model change. CI runs tests on every PR, and evals when the `ANTHROPIC_API_KEY` secret is set.

## Deploy on Railway

The short version follows; the full walkthrough for macOS, Linux and Windows, with every variable and secret, the CLI commands, the dashboard path, day-two operations and troubleshooting, is [docs/deploy-railway.md](docs/deploy-railway.md).

1. **Repo.** Push this folder to a private GitHub repo. Create the issues with `scripts/create_issues.sh` if you want the board (a bash script: on Windows, run it from Git Bash).
2. **Project and services.** With the CLI (`brew install railway` on macOS, `scoop install railway` on Windows): `railway login`, `railway init --name bakery-copilot`, `railway add --database postgres`, then `railway add --service web --repo <owner>/bakery-copilot --branch main` and the same with `--service digest`. Railway's GitHub app needs access to the repo (Railway → Account Settings → Integrations → GitHub), or builds from GitHub fail with "repository not accessible".
3. **Settings from code.** Once: `npm install --prefix .railway`. Then from the repo root `railway config plan` and `railway config apply`: `.railway/railway.ts` sets both start commands, the `/health` check, restart policies and the digest's cron schedule (`30 4 * * *`, UTC; 07:30 Israel in summer, 06:30 in winter). Railpack builds from `requirements.txt` with the Python version in `.python-version`, no Dockerfile.
4. **Variables** on the web service: `APP_ENV=production`, `APP_PASSWORD`, `APP_SECRET` (`openssl rand -hex 32`, or the PowerShell generator in the guide), `ANTHROPIC_API_KEY`, `DATABASE_URL` as a reference to `${{Postgres.DATABASE_URL}}`, `MODEL=claude-opus-5-5`, `DAILY_CAP_USD=3.00`, `CRON_SECRET`, optionally `DIGEST_WEBHOOK_URL`, `TIMEZONE=Asia/Jerusalem`. Redeploy.
5. **Digest variables.** The digest service references the web service's values: `APP_ENV=${{web.APP_ENV}}`, and the same for `APP_PASSWORD`, `APP_SECRET`, `ANTHROPIC_API_KEY`, `CRON_SECRET`, `MODEL` and `TIMEZONE`, plus `DATABASE_URL=${{Postgres.DATABASE_URL}}`. Set `DIGEST_WEBHOOK_URL` here if the digest should post somewhere. The service runs for seconds and exits.
6. **First check.** Open the URL, sign in, ask about a site, request a visit. `GET /health` must be 200. Then run through spec.md §7.

Deploy on push to `main` is on by default. Rollback is Deployments → previous deploy → Redeploy.

## Cost (as of October 2026)

| Item | Estimate |
|---|---|
| Railway web service (0.05–0.1 vCPU, ~200 MB) | $3–4 / month |
| Railway Postgres (small, 1 GB) | $1–2 / month |
| Cron service (seconds a day) | cents |
| **Railway total** | **$4–6 / month**, inside the Hobby plan's $5 most months |
| Claude, 300 turns a day with history (Opus 5.5) | ~$18 / month; Sonnet 5.5 ~$9 |

The model is the bill. `HISTORY_LIMIT`, `MODEL_EFFORT` and prompt caching are the three levers; `DAILY_CAP_USD` is the brake.

## Layout

```
app/            backend: main.py (API), agent.py (loop), tools/, db.py, auth.py, limits.py, costs.py, digest.py
app/prompts/    system.md — the agent's job description
static/         the chat UI (plain HTML, CSS, JS)
data/           geochronos_*.csv (read-only export of the research records); bakery CSVs from the class project, unused
scripts/        export_geochronos.py (rebuilds the CSVs from a GeoChronos build)
tests/          pytest, model mocked
evals/          cases.yaml, run.py, history.csv
docs/           tickets.md, watch.md, deploy-railway.md
```

## Data sources and licences

The records are exported from the author's GeoChronos repository (`scripts/export_geochronos.py`). They combine facts from Wikidata (CC0), Pleiades (CC BY 3.0) and English Wikipedia (CC BY-SA 4.0). Only facts, dating claims and citations are stored; Wikipedia text is not copied. Dates are scholarly estimates and are always shown with their range, status and confidence. The "time travel" is fiction: nothing is booked or paid.
