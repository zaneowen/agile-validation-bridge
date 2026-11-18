# Notes for Claude Code

- Python 3.11+, FastAPI, SQLite. Run `pytest -q` after changes; tests use `AVB_MOCK_AI=1`.
- `audit_log` is append-only by design. Never add code that updates or deletes audit rows.
- Every AI output must stay a draft until a human review is recorded via `/api/analyses/{id}/review`.
- Overrides and rejections require a comment. Keep that rule.
- Prompts and test fixtures must use public standards only (FDA CSA guidance, GAMP 5). No employer templates or data.
- Keep the UI a single dependency-free HTML file until the Jira Forge port.
