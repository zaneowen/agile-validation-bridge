# Agile-to-Validation Bridge

Turn agile user stories into risk-rated, CSA-aligned validation evidence as you work.

Paste a user story and the app:

1. **Rates its GxP impact** (High / Medium / Low) with a rationale, and flags Part 11 / Annex 11 relevance
2. **Drafts test cases** scaled to that risk: scripted for High, unscripted for Medium, ad hoc for Low
3. **Requires a human decision**: approve, override (with a comment) or reject
4. **Logs everything** to an append-only audit trail (SQLite triggers block edits and deletes)
5. **Exports a traceability matrix** (requirement → story → criterion → test) to Excel or CSV

This is the standalone prototype. The plan is to put the same backend behind a Jira Forge app later.

> AI output here is a draft for a qualified reviewer. It is not validation evidence until a human approves it.

## Run it on your PC

You need Python 3.11 or newer ([python.org](https://www.python.org/downloads/); on Windows tick "Add python.exe to PATH").

**Windows:** double-click `run.bat`.
**macOS / Linux:** `./run.sh`

The first run creates a `.venv`, installs dependencies and copies `.env.example` to `.env`. Your browser then opens http://127.0.0.1:8000. Click **Load sample**, then **Analyze story**.

To use Claude instead of the keyword-based mock, put your key in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
```

Then restart. The badge in the top right changes from "Mock AI" to "Claude connected". Your data lives in `avb.db` next to the code.

<details><summary>Manual setup</summary>

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                # then edit it
uvicorn app.main:app --reload
```
</details>

Run the tests (they use the mock AI, so no key is needed):

```bash
pytest -q
```

## Put it on the web

The repo ships a `Dockerfile` (works on any container host) and a `render.yaml` for [Render](https://render.com).

**Before you deploy, set `AVB_USERS`.** Without it, anyone with the URL can use the app and spend your API credits. It takes comma-separated `user:password` pairs:

```
AVB_USERS=qa.lead:a-long-password,dev1:another-long-password
```

When login is on, the audit trail records the logged-in username, not the free-text name field. That gives you attributable records, which is closer to what Part 11 expects.

### Render (simplest)

1. Push this repo to GitHub.
2. In Render: **New → Blueprint**, then pick the repo. Render reads `render.yaml`.
3. Enter `ANTHROPIC_API_KEY` and `AVB_USERS` when prompted.
4. Open the `https://….onrender.com` URL and log in.

The blueprint uses the Starter plan because SQLite needs a persistent disk mounted at `/data`. For a free throwaway demo, change `plan: starter` to `plan: free` and delete the `disk:` block. Data is then wiped on every redeploy and restart.

### Any other host (Fly.io, Railway, Azure, a VM)

```bash
docker build -t avb .
docker run -p 8000:8000 -v avb-data:/data \
  -e ANTHROPIC_API_KEY=sk-ant-... -e AVB_USERS=qa.lead:pw avb
```

Mount a volume at `/data` so the database survives restarts. The container listens on `$PORT` if the host sets it.

### Hosted-demo caveats

- Use made-up stories only. Don't paste real company or patient data into a public demo.
- SQLite with one server process is fine for a handful of users. Move to Postgres (on the roadmap) before a team uses it for real.
- This is a proof of concept, not a validated system.

## API

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/api/stories` | Create a story and run the AI analysis |
| GET | `/api/stories` | List stories with their latest analysis |
| GET | `/api/me` | Logged-in user (null when login is off) |
| POST | `/api/analyses/{id}/review` | Approve, override or reject a suggestion |
| GET | `/api/audit` | Audit trail, newest first (`?story_id=` to filter) |
| GET | `/api/rtm.xlsx`, `/api/rtm.csv` | Traceability matrix export |

Interactive docs: http://127.0.0.1:8000/docs

## Layout

```
app/
  main.py      FastAPI routes, review rules, RTM export
  ai.py        Claude call (forced tool use = structured JSON) + offline mock
  prompts.py   System prompt and output schema (public sources only)
  db.py        SQLite schema, append-only audit log
  auth.py      Optional login (AVB_USERS) for hosted copies
  static/      Single-page UI
tests/         API tests (mock AI)
```

## Roadmap

- [ ] Jira Forge app: panel on each story calling this backend
- [x] Basic login for hosted copies (`AVB_USERS`)
- [ ] Postgres + real user accounts (SSO) in place of SQLite and shared passwords
- [ ] Re-run analysis when a story changes, with a diff in the audit trail
- [ ] Test execution results (pass/fail + evidence upload) feeding the RTM
- [ ] PDF validation summary report

## IP note

Everything in this repo is built from public standards (FDA CSA guidance, GAMP 5). Do not add employer templates, SOPs or data.
