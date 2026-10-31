# ACSA - Autonomous Coding & Shipping Agent

ACSA turns a plain-English software request into a working FastAPI project, complete with a pytest test suite and a review report, using a multi-agent LangGraph pipeline.

## How it works

A LangGraph `StateGraph` runs five specialised LLM agents in order:

1. **Requirements Analyst** reads the prompt and lists the entities and endpoints to build.
2. **Code Architect** plans every file, including the classes and functions each one exports and imports.
3. **Code Writer** writes one file per LLM call in dependency order (config, database, models, services, main, requirements, README). Each file sees the interface contract and the code already written. Python files are checked with `ast.parse`, truncated output is retried, and cross-file imports are verified.
4. **Tester** writes `tests/test_api.py` from the real generated code, installs the project into a fresh virtual environment and runs `pytest`.
5. **Code Reviewer** reads the test report. If tests fail, it sends the run back to the Writer with the pytest output for a repair round.

The finished project is packaged as a zip you can download.

## Tech stack

Python, LangGraph, LangChain (`langchain-groq`), Groq (`openai/gpt-oss-20b`), FastAPI, Pydantic, pydantic-settings, `json-repair`, pytest

## API

All routes are under `/api`.

| Method | Route | What it does |
|---|---|---|
| POST | `/api/start-project` | Start a run with `{"prompt": "...", "project_name": "..."}`; returns a `run_id` |
| GET | `/api/status/{run_id}` | Poll the run's progress |
| GET | `/api/result/{run_id}` | Get the generated files, test report and review |
| GET | `/api/download/{run_id}` | Download the generated project as a zip |
| GET | `/api/health` | Health check |

## Run it locally

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then add your GROQ_API_KEY
python -m app.main     # serves on http://localhost:8000, docs at /docs
```

Example request:

```bash
curl -X POST http://localhost:8000/api/start-project \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Build a student attendance tracker API with subjects and attendance records", "project_name": "student_tracker"}'
```

## Configuration

Key settings in `backend/.env` (see `.env.example`):

- `GROQ_API_KEY`, `GROQ_MODEL` and an optional `GROQ_FALLBACK_MODEL`
- `GROQ_MAX_TOKENS` and `GROQ_INPUT_TOKEN_BUDGET` to stay within Groq free-tier limits
- `WRITER_MAX_ATTEMPTS` and `MAX_REPAIR_ATTEMPTS` for retries and repair rounds
- `TEST_USE_VENV`, `TEST_INSTALL_TIMEOUT`, `TEST_TIMEOUT` for the test runner
- `ALLOW_TEMPLATE_FALLBACK` (off by default) to ship a template project marked degraded if the Writer fails

## Tests

```bash
cd backend
pytest tests/test_pipeline_offline.py
```

## Author

Shivatmika Baranwal, B.Tech Computer Engineering, NMIMS Indore - [GitHub](https://github.com/Damselnodistress5)
