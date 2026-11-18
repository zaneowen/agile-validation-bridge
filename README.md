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

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...                  # optional; without it a keyword-based mock runs
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000, click **Load sample**, then **Analyze story**.

Run the tests (they use the mock AI, so no key is needed):

```bash
pytest -q
```

## API

| Method | Path | What it does |
| --- | --- | --- |
| POST | `/api/stories` | Create a story and run the AI analysis |
| GET | `/api/stories` | List stories with their latest analysis |
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
  static/      Single-page UI
tests/         API tests (mock AI)
```

## Roadmap

- [ ] Jira Forge app: panel on each story calling this backend
- [ ] Postgres + user accounts in place of SQLite and free-text names
- [ ] Re-run analysis when a story changes, with a diff in the audit trail
- [ ] Test execution results (pass/fail + evidence upload) feeding the RTM
- [ ] PDF validation summary report

## IP note

Everything in this repo is built from public standards (FDA CSA guidance, GAMP 5). Do not add employer templates, SOPs or data.
