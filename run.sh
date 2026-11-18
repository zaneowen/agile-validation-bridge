#!/usr/bin/env bash
# macOS / Linux: ./run.sh
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d .venv ]; then echo "First run: creating virtual environment..."; python3 -m venv .venv; fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; echo "Created .env - add your ANTHROPIC_API_KEY there for real AI (mock AI runs without it)."; fi
( sleep 2; (command -v open >/dev/null && open http://127.0.0.1:8000) || (command -v xdg-open >/dev/null && xdg-open http://127.0.0.1:8000) || true ) &
exec .venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
