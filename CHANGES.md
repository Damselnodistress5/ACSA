# ACSA backend: root causes and fixes

The architecture, file layout and node names are the same as before (Analyst -> Architect -> Writer -> Tester -> Reviewer).
`templates.py`, `file_tools.py` and `zip_service.py` have not changed.

## Root causes

1. **The Writer asked for all 7 files in one LLM call, with no `max_tokens` set** (`nodes.py: code_writer_node`, `llm.py: get_llm`).
   gpt-oss-20b is a reasoning model, so its reasoning tokens share the output budget with the code. The response stopped in the middle of `services.py` (`.order_by(...)` followed by a lone `.`).
   Because of this, the `### FILE: app/main.py` block never arrived.
2. **Missing or unparseable files were quietly filled in from templates** (the "Build final files dictionary" loop and the "Guarantee all 7 core files exist" step in `code_writer_node`, which call `synthesize_complete_file`).
   As a result, `main.py` came from the generic `Student` template, which imports `StudentModel`, `StudentResponse` and `student_service`. It was mixed with LLM-written `models.py` and `services.py`, and those files don't define any of these names.
   The only validation was "at least 50 characters and 3 code lines", so a truncated file still passed.
3. **The Writer never saw the Architect's interfaces.** The file plan carried only path and description, with no class or function names, and each file never saw the real code it imports. Imports between files could therefore drift.
4. **The Tester did not run anything.** `tester_node` wrote a fixed 3-test template (`/`, `/health`, `GET /students`), never executed it, and ignored the requirements, so the subject and attendance endpoints were never checked.
   The template tests also used `TestClient(app)` without `with`, so lifespan/`create_all` didn't run.
5. **The Reviewer was hardcoded.** `code_reviewer_node` always returned score 96, "Production Ready", `status: completed`.
6. **Errors were swallowed everywhere.** A 401 "Invalid API Key" in the Analyst or Architect became "fallback triggered due to parsing". In the Writer it became a full template project with an `info`-level log.
   `project_service._run_workflow_sync` also forced `status = "completed"` at the end, whatever had happened.
7. **API key loading** (`config.py`):
   - `env_file=(".env", "../.env")` is resolved relative to the **current working directory**, not to the backend folder. Which file gets read depends on where uvicorn is started from.
   - In pydantic-settings, **later files override earlier ones**. A stale `.env` in the project root (`../.env`) silently beat `backend/.env`. This was verified with temporary files.
   - A `GROQ_API_KEY` exported in the shell overrides both files. Nothing logged where the key came from, so an old key in the shell or the root `.env` would give 401s even after fixing `backend/.env`.
8. Smaller issues:
   - Nodes returned keys (`requirements`, `code_files`) that weren't in `AgentState`, and LangGraph drops them silently.
   - The startup log printed the wrong storage path.
   - `/api/health` had the model name hardcoded.

## Fixes per file

- **`app/llm.py`**
  - `get_llm(..., max_tokens=None)` now passes `max_tokens` (default `GROQ_MAX_TOKENS=8192`) and `reasoning_effort` (default `low`, for gpt-oss only).
  - Added `LLMFatalError` and `is_fatal_llm_error()`, which detect 401, an invalid key or a missing key.
  - Added `was_truncated()` (`finish_reason == "length"`) and `extract_code_block()`.
- **`app/config.py`**
  - The `.env` paths are now absolute and based on `__file__`. The order is `(<root>/.env, backend/.env)`, so **backend/.env wins**.
  - `clean_api_key` validator strips stray spaces and quotes.
  - New `groq_api_key_source` property reports where the key came from without showing the key.
  - New settings: `GROQ_MAX_TOKENS`, `GROQ_REASONING_EFFORT`, `ALLOW_TEMPLATE_FALLBACK` (default false), `MAX_REPAIR_ATTEMPTS` (default 1), `TEST_USE_VENV`, `TEST_INSTALL_TIMEOUT`, `TEST_TIMEOUT`.
- **`app/graph/state.py`**
  - `PlannedFile` gains `exports` and `imports_from`.
  - `AgentState` gains `test_report`, `repair_attempts`, `degraded`, `warnings`, `requirements` and `code_files`.
- **`app/graph/nodes.py`**
  - New `_fail()`: sets `status="failed"` and records the error. Used for fatal LLM errors in every node.
  - `requirements_analyst_node` and `code_architect_node`: a fatal key error now fails the run. Other errors still use her fallback, but the run is marked `degraded` with a warning.
  - The Architect prompt now asks for `exports` and `imports_from` for every file, and requires every analysed endpoint to be implemented.
  - `code_writer_node` was rewritten:
    - It makes **one LLM call per file**, in dependency order (config -> database -> models -> services -> main -> requirements -> README), via `_generate_one_file()`.
    - Each call gets the file plan and the interface contract (`_interface_contract`) plus the **actual code of the files already written**.
    - Output goes between BEGIN/END markers, so a missing END marker or `finish_reason=length` counts as truncated. Every `.py` file is checked with `ast.parse`, and a rejected file is **retried once** with the error attached.
    - After all files are written, `check_internal_imports()` checks every `from app.X import Y` against what `X` really defines. Any mismatching file gets one fix call.
    - If a file still fails, the **run fails** and the error names the file and reason. Templates are used only when `ALLOW_TEMPLATE_FALLBACK=true`, and then for the **whole** project (never mixed), with the run marked `degraded`.
    - It adds `app/__init__.py`.
    - **Repair mode**: when the Reviewer sends the run back, the Writer rewrites the app files named in the pytest traceback (or all app modules if only the tests show the failure), sending the pytest output as feedback. It changes the test file only for collection/fixture errors, never just to make assertions pass.
  - `tester_node`:
    - The LLM writes `tests/test_api.py` from the **real generated code** and the endpoint list. The tests use a `with TestClient(app)` fixture and cover the happy path plus 404/422 cases.
    - New `run_pytest_on_project()` copies the project to a temp dir, creates a **fresh venv**, installs `requirements.txt` + pytest + httpx (with a timeout), then runs `python -m pytest -q` in a subprocess with a timeout. If the install fails, it falls back to the current interpreter and logs a warning.
    - `GROQ_API_KEY` is removed from the child process's environment.
    - The result goes to `state["test_report"]`: passed/failed/error counts, summary, output tail, install error.
    - If the LLM can't write tests, her smoke tests are used and the run is marked `degraded`.
  - `code_reviewer_node`:
    - Says **"Ready to ship" only if pytest passed**.
    - If the tests fail and a repair round is left, it sets `repair_attempts += 1` and sends the run back to the Writer.
    - Otherwise it sets `status="failed"` with the verdict "Not ready - tests failing" and the pytest tail. The zip is still built so the output can be inspected.
- **`app/graph/workflow.py`**
  - Conditional edges stop the graph at `END` as soon as a node sets `status == "failed"`.
  - Added the edge `reviewer -> writer` for the repair loop.
- **`app/services/project_service.py`**
  - The final status is no longer forced to `completed`, so a `failed` status is kept.
  - `get_status` and `get_result` now also return `degraded`, `warnings` and `test_report`.
- **`app/main.py`**: logs the real storage path and where `GROQ_API_KEY` came from (never the value). Logs an error if the key is empty.
- **`app/api/routes.py`**: `/api/health` reports `settings.groq_model`.
- **`.env.example`**: documents the new settings. **`requirements.txt`**: adds pytest.
- **`tests/test_pipeline_offline.py`** (new): 8 offline tests that use a fake LLM, so no Groq calls are needed. They cover:
  - happy path, where the project ships only after real pytest passes
  - truncated file gets retried
  - syntax error twice means a failed run with no templates
  - a 401 fails the run immediately
  - an import mismatch is detected and fixed
  - failing tests trigger one repair, then ship
  - tests still failing after the repair mean a failed run
  - `check_internal_imports` unit test

## How to use

1. Put the key in `backend/.env` as `GROQ_API_KEY=gsk_...`.
2. Make sure there is no stale `GROQ_API_KEY` exported in your shell and no old key in a root `.env`. Check with `echo $GROQ_API_KEY` and `unset GROQ_API_KEY`. The startup log shows which source is being used.
3. Restart uvicorn after changing `.env`.
4. Run the offline tests with `cd backend && GROQ_API_KEY=dummy python -m pytest -q tests`.

**Note:** the Tester runs LLM-generated code on your machine (in a temp folder and venv, not a sandbox). Only run prompts you trust.

**Groq free tier note:** per-file generation makes about 9–15 smaller calls per run instead of 1 huge one. ChatGroq retries 429 errors with backoff (`max_retries=6`). If you see "Request too large", lower `GROQ_MAX_TOKENS` to 4096.

---

# Round 2 (Oct 21): made it work end to end against the real Groq API

Tested with real runs through the API (`POST /api/start-project`, polling `/api/status/{id}`).
Result: **two different prompts reached "Ready to ship" with their generated pytest suites passing**
(attendance tracker: 10/10 tests, 192 s; todo API: 12/12 tests, 98 s). Both were re-verified by unzipping
the download and running `pip install -r requirements.txt && pytest` in a fresh venv.

## What was still breaking (found in real runs)

1. **Groq free-tier limits.** gpt-oss-20b allows 8,000 tokens/minute and 200,000 tokens/day. A single request
   above 8k is rejected with **413 "Request too large"**. Repair prompts contained every file in full (~10-12k
   tokens), so every repair call failed with 413.
2. **The LLM-written requirements.txt pinned old versions** (pydantic-core that needs a Rust build and can't
   build on Python 3.13), so the clean test venv failed to install. It also forgot `email-validator`.
3. **Writer rejected good files** when the model forgot the END marker; README/requirements calls also wasted ~10k tokens.
4. **Misleading syntax errors.** `ast.parse(..., filename="app/main.py")` made Python show the line from the
   *backend's own* app/main.py, and that wrong line was sent back to the LLM as "the error".
5. **Repair loop didn't converge:** pytest output was 90% FastAPI/Starlette library frames (the real error was cut
   off), the repair rewrote the wrong files (even empty `__init__.py`), and the test-revision step "fixed" tests by
   weakening them (e.g. changing an expected 404 into 200).
6. Common generated-code bugs: `date: date` Pydantic field clash, `@contextmanager` on `get_db`, in-memory SQLite
   without `StaticPool`, missing `Integer` import, parameter-order SyntaxErrors, and **route shadowing**
   (`/students/{id}` registered before `/students/below-threshold` gives 422).
7. The pytest `passed` count overwrote the boolean `passed` flag in the test report.
8. The offline tests failed on a fresh venv (sqlalchemy wasn't in the backend's requirements).

## Fixes per file

- **`app/graph/codegen_utils.py` (new)**
  - `stub_source()`: an interface-only view of a file (imports, class fields, decorators, signatures, plus what
    each function raises and whether it returns None). Used instead of full files in prompts.
  - `build_requirements()`: requirements.txt is built from the real imports, unpinned lower bounds; adds
    email-validator / python-multipart when needed. No LLM call.
  - `build_readme()`: README built from the analysis and the real routes. No LLM call.
  - `fix_route_shadowing()`: moves shadowed FastAPI routes above the generic route (text-based, keeps comments).
  - `undefined_names()`: pyflakes check, so `Integer` used without import is caught before tests run.
  - `COMMON_PITFALLS` (sent with every Writer call) and `known_error_hints()` (pytest error -> concrete fix).
- **`app/graph/nodes.py`**
  - `_generate_one_file()`: prompts are kept under `GROQ_INPUT_TOKEN_BUDGET` (full source only for the files
    it imports, stubs for the rest, then trimmed). On 413 it shrinks the context and retries without spending
    an attempt. Attempts are configurable (`WRITER_MAX_ATTEMPTS`, default 3).
  - `_extract_file_body()`: accepts a missing END marker when the response wasn't truncated.
  - `_validate_file()`: fake filename (no more wrong lines), shows the actual generated lines around the
    error, rejects undefined names, and rejects a "test file" that has no `def test_`.
  - Writer: requirements.txt and README.md are deterministic; route shadowing is auto-fixed.
  - Repair round:
    - It targets the file that raised the error (or the file a known hint names), otherwise main/services.
    - It sends focused pytest output (only app/tests frames and the `E` lines) plus the source of the failing tests.
    - main.py and services.py see each other in full when the budget allows.
    - Tests are revised only from round 2 onward and only for genuine test mistakes. A deterministic guard
      (`_tests_weakened`) rejects any revision that drops error-case assertions, tests or asserts.
  - Analyst prompt: endpoints carry success/error codes; no ambiguous literal vs param paths.
  - `run_pytest_on_project()`: keeps 40k chars of output; `passed_count` no longer overwrites `passed`.
  - Fatal errors: daily quota exhausted now fails fast with a clear message (not "check GROQ_API_KEY").
- **`app/llm.py`**
  - `ResilientChat`: when the main model's **daily** quota is used up, it switches to `GROQ_FALLBACK_MODEL`
    (default `openai/gpt-oss-120b`, a separate 200k/day quota).
  - New helpers: `is_quota_exhausted`, `is_request_too_large`, `describe_fatal_llm_error`.
  - It strips `<think>` blocks.
- **`app/config.py`**:
  - Defaults: `GROQ_MAX_TOKENS` is 6144 and `MAX_REPAIR_ATTEMPTS` is 3.
  - New settings: `GROQ_INPUT_TOKEN_BUDGET` (6000), `GROQ_MAX_RETRIES` (8), `GROQ_FALLBACK_MODEL` and `WRITER_MAX_ATTEMPTS` (3).
- **`requirements.txt`**: + sqlalchemy (offline tests / fallback interpreter), + pyflakes.
- **`.env.example`**: documents the new settings.
- **`tests/test_pipeline_offline.py`**: 7 new offline tests (15 total), covering:
  - deterministic requirements
  - route shadowing
  - undefined-name retry
  - 413 shrink-and-retry
  - quota message
  - the anti-weakening guard
  - correct syntax-error text

## Known limits

- A full run uses about 25-45k tokens, so the free tier gives roughly 4-6 runs/day per model. With the 120b fallback, that's about 8-12.
- Generated code is LLM output. When a run's tests don't pass after 3 repair rounds, it is marked **failed** (never shipped as "ready").
- If the Architect call itself fails (not a key/quota error), the run uses the generic file plan and is marked
  "Ready to ship (degraded)" even if its tests pass. This happened on the todo test run.
