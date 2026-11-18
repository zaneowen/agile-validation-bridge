# Agile-to-Validation Bridge

Turn agile user stories into risk-rated, CSA-aligned validation evidence as you work.

Paste a user story and the app:

1. **Rates its GxP impact** (High / Medium / Low) with a rationale, and flags Part 11 / Annex 11 relevance
2. **Drafts test cases** scaled to that risk: scripted for High, unscripted for Medium, ad hoc for Low
3. **Requires a human decision**: approve, override (with a comment) or reject. With login on, the decision is an **electronic signature**: the reviewer re-enters their password and picks a meaning (Approved / Reviewed; rejections are signed as Rejected). The signature records name, UTC time and meaning plus a SHA-256 hash of exactly what was signed, can't be edited or deleted, and each analysis can be signed only once
4. **Logs everything** to an append-only audit trail (SQLite triggers block edits and deletes)
5. **Records test execution**: after approval, testers mark each test Pass, Fail or Blocked (a note is required for Fail and Blocked) and attach evidence: a screenshot, PDF or text log up to 4 MB. Evidence is stored in the database with its SHA-256, served only to signed-in users, and like test runs can't be edited or deleted; re-running a test adds a new record
6. **Exports a traceability matrix** (requirement → story → criterion → test → result → evidence) to Excel or CSV

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

The hosted copy runs on [Vercel](https://vercel.com) with a [Neon](https://neon.com) Postgres database. When `DATABASE_URL` is set the app uses Postgres; without it, it uses a local SQLite file. Both block edits and deletes on the audit trail.

**Before you deploy, set `AVB_USERS`.** Without it, anyone with the URL can use the app and spend your API credits. It takes comma-separated `user:password` pairs:

```
AVB_USERS=qa.lead:a-long-password,dev1:another-long-password
```

When login is on, people sign in on a sign-in page (sessions last 12 hours), and the audit trail records the logged-in username, not the free-text name field. Scripts can send the same username and password as HTTP Basic auth. After 5 wrong passwords for a username (or 30 from one IP address) in 15 minutes, sign-in is refused until the window passes. That gives you attributable records, which is closer to what Part 11 expects.

### Vercel

Vercel finds the FastAPI app in `app/main.py` with no extra config.

1. Import the GitHub repo in Vercel (or run `vercel link`).
2. Add a Neon database: `vercel integration add neon` (the free plan is enough). This sets `DATABASE_URL`.
3. Add `ANTHROPIC_API_KEY` and `AVB_USERS` under **Settings → Environment Variables**.
4. Optional: connect a second Neon database to Preview and Development with the prefix `PREVIEW` (`vercel integration resource connect <db> <project> -e preview -e development --prefix PREVIEW`). Previews then use `PREVIEW_DATABASE_URL`, so test data never reaches the live site.
5. Push to `main`. Pull requests get preview deployments, and GitHub Actions runs the tests (SQLite and Postgres) on every PR.

The tables are created on first start. To run the Postgres test locally, set `TEST_DATABASE_URL` to an **unpooled** connection string (`DATABASE_URL_UNPOOLED`); it works in a throwaway schema and drops it afterwards.

### Any other host (Docker)

```bash
docker build -t avb .
docker run -p 8000:8000 -v avb-data:/data -e ANTHROPIC_API_KEY=sk-ant-... -e AVB_USERS=qa.lead:pw avb
```

Mount a volume at `/data` so the SQLite database survives restarts, or pass `DATABASE_URL` to use Postgres. The container listens on `$PORT` if the host sets it.

### Hosted-demo caveats

- Use made-up stories only. Don't paste real company or patient data into a public demo.
- Shared passwords in `AVB_USERS` are fine for a demo. Use real accounts (SSO) before a team relies on it.
- This is a proof of concept, not a validated system.

## API

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/api/stories` | Create a story and run the AI analysis |
| GET | `/api/stories` | List stories with their latest analysis |
| GET | `/api/me` | Logged-in user (null when login is off) |
| POST | `/api/analyses/{id}/review` | Approve, override or reject a suggestion |
| POST | `/api/evidence` | Upload an evidence file (raw body, `X-Filename` header) |
| GET | `/api/evidence/{id}` | Download an evidence file |
| POST | `/api/analyses/{id}/tests/{test_id}/runs` | Record a test result |
| GET | `/api/audit` | Audit trail, newest first (`?story_id=` to filter) |
| GET | `/api/rtm.xlsx`, `/api/rtm.csv` | Traceability matrix export |

Interactive docs: http://127.0.0.1:8000/docs

## Layout

```
app/
  main.py      FastAPI routes, review rules, RTM export
  ai.py        Claude call (structured outputs = schema-checked JSON) + offline mock
  prompts.py   System prompt and output schema (public sources only)
  db.py        Postgres (DATABASE_URL) or SQLite, append-only audit log
  auth.py      Optional login (AVB_USERS) for hosted copies
  static/      Single-page UI
tests/         API tests (mock AI)
```

## Roadmap

- [ ] Jira Forge app: panel on each story calling this backend
- [x] Basic login for hosted copies (`AVB_USERS`)
- [x] Postgres for the hosted copy (Vercel + Neon)
- [ ] Real user accounts (SSO) in place of shared passwords
- [ ] Re-run analysis when a story changes, with a diff in the audit trail
- [x] Test execution results (pass/fail + evidence upload) feeding the RTM
- [x] Electronic signatures on reviews
- [ ] PDF validation summary report

## IP note

Everything in this repo is built from public standards (FDA CSA guidance, GAMP 5). Do not add employer templates, SOPs or data.
