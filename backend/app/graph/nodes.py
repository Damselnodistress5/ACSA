import ast
import json
import logging
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings

from app.graph.state import AgentState, add_log
from app.graph.templates import (
    get_config_py,
    get_database_py,
    get_main_py,
    get_models_py,
    get_readme_md,
    get_requirements_txt,
    get_services_py,
)
from app.graph.codegen_utils import (
    COMMON_PITFALLS,
    build_readme,
    build_requirements,
    estimate_tokens,
    fix_route_shadowing,
    known_error_hints,
    stub_source,
    undefined_names,
)
from app.llm import (
    describe_fatal_llm_error,
    is_request_too_large,
    extract_code_block,
    extract_files_from_markdown,
    extract_json,
    get_llm,
    is_fatal_llm_error,
    was_truncated,
)
from app.services.zip_service import create_project_zip
from app.tools.file_tools import detect_language, save_code_files, save_project_files

logger = logging.getLogger("acsa.nodes")


def _fail(state: AgentState, logs: List[Dict[str, Any]], step: str, message: str) -> Dict[str, Any]:
    """Marks the run as failed and surfaces the error (no silent fallback)."""
    logs = list(logs)
    logs.append({
        "timestamp": logs[-1]["timestamp"] if logs else "",
        "step": step,
        "message": f"FAILED: {message}",
        "level": "error",
    })
    return {
        "status": "failed",
        "error": message,
        "current_step": step,
        "logs": logs,
        "warnings": list(state.get("warnings", [])) + [message],
    }


def requirements_analyst_node(state: AgentState) -> Dict[str, Any]:
    """Node 1: Requirements Analyst.
    Analyzes user requirements, identifies tech stack, features, and constraints.
    """
    run_id = state.get("run_id", "unknown")
    prompt = state.get("prompt", "")
    project_name = state.get("project_name", "")
    logger.info(f"[{run_id}] Running Requirements Analyst Node")

    logs = add_log(
        state,
        "analyst",
        "Requirements Analyst initiated: Analyzing user prompt and project scope...",
    )

    system_prompt = (
        "You are a Senior Technical Requirements Analyst and Product Architect.\n"
        "Your task is to analyze the user's project request and produce a comprehensive, structured technical specification.\n"
        "Tech Stack MUST include: FastAPI, SQLite, SQLAlchemy, Pydantic, and Uvicorn.\n"
        "API design rules: create endpoints return 201, delete returns 204, missing ids return 404, invalid bodies 422, "
        "duplicates 409. Avoid ambiguous paths: a path with a literal segment (e.g. /attendance/low) must never have the same "
        "shape as a parameterised one (e.g. /attendance/{student_id}); prefer query parameters for filters (e.g. GET /students/below-threshold?threshold=75).\n"
        "You MUST respond ONLY with a valid JSON object (no markdown code blocks, no other text) with the following exact keys:\n"
        "{\n"
        '  "project_name": "Concise and descriptive project name (e.g., TaskFlow API, ShortLink)",\n'
        '  "summary": "Clear, professional 2-3 sentence overview of what the application does",\n'
        '  "tech_stack": ["FastAPI", "SQLite", "SQLAlchemy", "Pydantic", "Uvicorn"],\n'
        '  "features": ["Bullet points of key functionality"],\n'
        '  "api_endpoints": [{"method": "GET/POST/PUT/DELETE", "path": "/path", "description": "endpoint role", "success_status": 200, "errors": {"404": "when the resource does not exist"}}],\n'
        '  "data_models": [{"name": "ModelName", "description": "Fields and role"}],\n'
        '  "security_considerations": ["Authentication, validation, rate-limiting, error shielding points"],\n'
        '  "user_stories": ["As a user, I want... so that..."]\n'
        "}"
    )

    user_message = f"User Request:\n{prompt}\n\nProject Name Hint: {project_name if project_name else 'Infer appropriate name'}"

    try:
        llm = get_llm(temperature=0.2)
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])

        analysis_data = extract_json(response.content)
        final_project_name = project_name or analysis_data.get("project_name", "ACSA Project")

        logs.append({
            "timestamp": logs[-1]["timestamp"],
            "step": "analyst",
            "message": f"Requirements finalized for '{final_project_name}'. Tech stack: {', '.join(analysis_data.get('tech_stack', []))}.",
            "level": "info",
        })

        return {
            "analysis": analysis_data,
            "requirements": analysis_data,
            "project_name": final_project_name,
            "current_step": "architect",
            "progress": 20,
            "logs": logs,
        }
    except Exception as e:
        logger.error(f"[{run_id}] Error in requirements_analyst_node: {e}", exc_info=True)
        if is_fatal_llm_error(e):
            return _fail(state, logs, "analyst", describe_fatal_llm_error(e))
        logs.append({
            "timestamp": logs[-1]["timestamp"] if logs else "",
            "step": "analyst",
            "message": f"DEGRADED: Analysis fallback triggered (LLM output unusable): {str(e)}",
            "level": "warning",
        })
        fallback_name = project_name or "ACSA Generated Project"
        fallback_analysis = {
            "project_name": fallback_name,
            "summary": f"Autonomously generated full-stack application with SQLite persistence for: {prompt[:150]}",
            "tech_stack": ["FastAPI", "SQLite", "SQLAlchemy", "Python 3.11", "Pydantic", "Uvicorn"],
            "features": [
                "Persistent SQLite database storage",
                "Complete CRUD API endpoints",
                "Data validation and schemas",
                "Structured logging and error handling",
                "Automated test coverage",
            ],
            "api_endpoints": [
                {"method": "GET", "path": "/", "description": "Root status and metadata"},
                {"method": "GET", "path": "/health", "description": "Health probe"},
            ],
            "data_models": [{"name": "CoreEntity", "description": "Primary domain entity backed by SQLite"}],
            "security_considerations": ["Input validation", "CORS policy configured", "SQL injection protection via SQLAlchemy"],
            "user_stories": [f"As a developer, I want {fallback_name} to provide robust SQLite REST endpoints."],
        }
        return {
            "analysis": fallback_analysis,
            "requirements": fallback_analysis,
            "project_name": fallback_name,
            "current_step": "architect",
            "progress": 20,
            "logs": logs,
            "degraded": True,
            "warnings": list(state.get("warnings", [])) + [f"Analyst used generic fallback requirements: {e}"],
        }


def code_architect_node(state: AgentState) -> Dict[str, Any]:
    """Node 2: Code Architect.
    Designs system architecture, directory structure, and plans all files to generate.
    Ensures real SQLite persistence with SQLAlchemy and hard-caps file plan to maximum 7 files.
    """
    run_id = state.get("run_id", "unknown")
    prompt = state.get("prompt", "")
    analysis = state.get("analysis", {})
    project_name = state.get("project_name", "ACSA Project")
    logger.info(f"[{run_id}] Running Code Architect Node")

    logs = list(state.get("logs", []))
    logs = add_log(
        state,
        "architect",
        f"Code Architect initiated: Designing modular architecture with SQLite persistence for '{project_name}'...",
    )

    system_prompt = (
        "You are a Principal Software Architect.\n"
        "Your task is to design a cohesive, production-ready FastAPI project architecture with SQLite database persistence.\n"
        "CRITICAL RULES:\n"
        "1. You MUST define a clean multi-file plan of EXACTLY 5 to 7 essential files (do NOT exceed 7 files).\n"
        "2. MUST include 'app/database.py' containing the SQLAlchemy SQLite engine, SessionLocal, Base, and get_db() dependency.\n"
        "3. Recommended standard 7 files:\n"
        "   - app/main.py: FastAPI entrypoint with endpoints, routers, CORS, and lifespan (Base.metadata.create_all)\n"
        "   - app/models.py: SQLAlchemy ORM models inheriting from Base and Pydantic schemas\n"
        "   - app/services.py: Business logic and SQLite CRUD operations using SQLAlchemy Session\n"
        "   - app/database.py: SQLite engine ('sqlite:///./app.db'), SessionLocal, Base, and get_db()\n"
        "   - app/config.py: Settings and database configuration\n"
        "   - requirements.txt: Python dependencies including fastapi, uvicorn, sqlalchemy, pydantic\n"
        "   - README.md: Comprehensive setup, SQLite details, run instructions, and API docs\n"
        "4. For EVERY python file, list its public interface in 'exports' (exact class, function and variable names other files will import),\n"
        "   and the planned files it imports from in 'imports_from'. These names are a CONTRACT: the Code Writer must use exactly these names.\n"
        "5. Every endpoint in the requirements analysis MUST be served by app/main.py and backed by functions in app/services.py.\n"
        "6. Use package-absolute imports (e.g. 'from app.models import Student'), and SQLAlchemy 2.x + Pydantic v2 APIs.\n"
        "You MUST respond ONLY with a valid JSON object with the following exact keys:\n"
        "{\n"
        '  "overview": "Detailed overview of the architectural design with SQLite persistence",\n'
        '  "design_patterns": ["Layered Architecture", "Repository Pattern", "Dependency Injection"],\n'
        '  "dependencies": ["fastapi", "uvicorn", "sqlalchemy", "pydantic", "pydantic-settings"],\n'
        '  "file_plan": [\n'
        '    {"path": "app/main.py", "description": "FastAPI entrypoint with routes, lifespan table creation, and get_db dependency", "language": "python", "exports": ["app"], "imports_from": ["app/database.py", "app/models.py", "app/services.py"]},\n'
        '    {"path": "app/models.py", "description": "SQLAlchemy ORM models and Pydantic validation schemas", "language": "python", "exports": ["<OrmModel>", "<OrmModel>Create", "<OrmModel>Read"], "imports_from": ["app/database.py"]},\n'
        '    {"path": "app/services.py", "description": "Business logic and SQLite database CRUD operations", "language": "python", "exports": ["create_<entity>", "get_<entity>", "list_<entities>"], "imports_from": ["app/models.py"]},\n'
        '    {"path": "app/database.py", "description": "SQLAlchemy SQLite engine, SessionLocal, Base, and get_db dependency", "language": "python", "exports": ["engine", "SessionLocal", "Base", "get_db"], "imports_from": ["app/config.py"]},\n'
        '    {"path": "app/config.py", "description": "Application settings and SQLite database URL configuration", "language": "python", "exports": ["settings"], "imports_from": []},\n'
        '    {"path": "requirements.txt", "description": "Pinned dependencies including sqlalchemy and fastapi", "language": "text"},\n'
        '    {"path": "README.md", "description": "Project documentation with SQLite details and run commands", "language": "markdown"}\n'
        "  ],\n"
        '  "run_instructions": "pip install -r requirements.txt && uvicorn app.main:app --reload"\n'
        "}"
    )

    user_message = (
        f"Project Name: {project_name}\n"
        f"Original Prompt: {prompt}\n"
        f"Requirements Analysis: {json.dumps(analysis, indent=2)}\n"
        "Plan a complete, runnable application using SQLite persistence (max 7 files)."
    )

    try:
        llm = get_llm(temperature=0.2)
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ])

        architecture_data = extract_json(response.content)
        file_plan = architecture_data.get("file_plan", [])
        if not file_plan:
            raise ValueError("File plan was empty in architect output")

        # Guarantee app/database.py is in the plan
        has_db_file = any("database.py" in f.get("path", "").lower() or "db.py" in f.get("path", "").lower() for f in file_plan)
        if not has_db_file:
            file_plan.append({
                "path": "app/database.py",
                "description": "SQLAlchemy SQLite engine, SessionLocal, Base, and get_db()",
                "language": "python",
            })

        # Hard-cap file plan to maximum 7 files, prioritizing the standard structure
        standard_names = ["main.py", "models.py", "services.py", "database.py", "config.py", "requirements.txt", "readme.md"]
        selected = []
        selected_bases = set()
        for std_name in standard_names:
            for f in file_plan:
                base = f.get("path", "").split("/")[-1].lower()
                if base == std_name and base not in selected_bases:
                    selected.append(f)
                    selected_bases.add(base)
                    break

        for f in file_plan:
            if len(selected) >= 7:
                break
            if f not in selected:
                selected.append(f)

        file_plan = selected[:7]
        architecture_data["file_plan"] = file_plan

        logs.append({
            "timestamp": logs[-1]["timestamp"],
            "step": "architect",
            "message": f"Architecture approved: Planned {len(file_plan)} modular files with SQLite persistence and pattern {', '.join(architecture_data.get('design_patterns', ['Modular']))}.",
            "level": "info",
        })

        return {
            "architecture": architecture_data,
            "file_plan": file_plan,
            "current_step": "writer",
            "progress": 40,
            "logs": logs,
        }
    except Exception as e:
        logger.error(f"[{run_id}] Error in code_architect_node: {e}", exc_info=True)
        if is_fatal_llm_error(e):
            return _fail(state, logs, "architect", describe_fatal_llm_error(e))
        logs.append({
            "timestamp": logs[-1]["timestamp"] if logs else "",
            "step": "architect",
            "message": f"DEGRADED: Architecture fallback triggered (no interface contract available): {str(e)}",
            "level": "warning",
        })
        fallback_plan = [
            {"path": "app/main.py", "description": "FastAPI application entrypoint with routes and lifespan", "language": "python"},
            {"path": "app/models.py", "description": "SQLAlchemy models and Pydantic validation schemas", "language": "python"},
            {"path": "app/services.py", "description": "Core business logic and SQLite database CRUD", "language": "python"},
            {"path": "app/database.py", "description": "SQLAlchemy SQLite engine, SessionLocal, Base, and get_db()", "language": "python"},
            {"path": "app/config.py", "description": "Application settings and database configuration", "language": "python"},
            {"path": "requirements.txt", "description": "Python dependencies with FastAPI and SQLAlchemy", "language": "text"},
            {"path": "README.md", "description": "Comprehensive project documentation with SQLite details", "language": "markdown"},
        ]
        fallback_arch = {
            "overview": "Layered architecture featuring SQLite persistence with SQLAlchemy ORM, clean service layer, and FastAPI endpoints.",
            "design_patterns": ["Layered Architecture", "Repository Pattern", "Dependency Injection"],
            "dependencies": ["fastapi", "uvicorn", "sqlalchemy", "pydantic", "pydantic-settings"],
            "file_plan": fallback_plan,
            "run_instructions": "pip install -r requirements.txt && uvicorn app.main:app --reload",
        }
        return {
            "architecture": fallback_arch,
            "file_plan": fallback_plan,
            "current_step": "writer",
            "progress": 40,
            "logs": logs,
            "degraded": True,
            "warnings": list(state.get("warnings", [])) + [f"Architect used generic fallback file plan: {e}"],
        }


def detect_domain(prompt: str, analysis: Optional[Dict[str, Any]] = None, project_name: str = "") -> Dict[str, Any]:
    """Detects domain semantics (url_shortener, todo, notes, blog, or custom domain entity)
    from prompt, requirements analysis, and project name.
    """
    raw_text = f"{prompt} {project_name}".lower()
    if analysis:
        raw_text += f" {analysis.get('summary', '')} {' '.join(analysis.get('features', []))}".lower()

    # 1. URL Shortener
    url_terms = [
        "url shortener", "url short", "shorten url", "short url", "link shortener",
        "link short", "shortener", "bitly", "tinyurl", "redirect url", "shorten", "short code",
        "url redirection", "shrink url"
    ]
    if any(term in raw_text for term in url_terms) or ("url" in raw_text and ("short" in raw_text or "redirect" in raw_text or "link" in raw_text)):
        return {
            "type": "url_shortener",
            "entity_name": "ShortURL",
            "plural_name": "urls",
            "singular_name": "url",
            "title": "URL Shortener API",
            "description": "Fast, collision-resistant URL shortening, redirect, and analytics service with SQLite persistence",
        }

    # 2. Todo / Task Manager
    todo_terms = ["todo", "todos", "task manager", "task tracker", "task api", "task management", "tasks", "checklist"]
    if any(term in raw_text for term in todo_terms):
        return {
            "type": "todo",
            "entity_name": "Todo",
            "plural_name": "todos",
            "singular_name": "todo",
            "title": "Todo & Task Management API",
            "description": "Task management API with completion tracking, priorities, deadlines, and SQLite persistence",
        }

    # 3. Notes / Note Taking
    notes_terms = ["note", "notes", "notebook", "memo", "memos", "scratchpad", "note taking", "note-taking"]
    if any(term in raw_text for term in notes_terms):
        return {
            "type": "notes",
            "entity_name": "Note",
            "plural_name": "notes",
            "singular_name": "note",
            "title": "Notes API",
            "description": "Note-taking and knowledge capture API with tagging, categories, and SQLite persistence",
        }

    # 4. Blog / Article Publishing
    blog_terms = ["blog", "article", "articles", "news feed", "cms", "publishing", "post api", "posts api"]
    if any(term in raw_text for term in blog_terms):
        return {
            "type": "blog",
            "entity_name": "Post",
            "plural_name": "posts",
            "singular_name": "post",
            "title": "Blog & Publishing API",
            "description": "Blog and article management API with slug-based lookups, publishing state, and SQLite persistence",
        }

    # 5. Custom Domain Extraction from analysis or prompt
    entity_name = "Item"
    singular_name = "item"
    plural_name = "items"

    if analysis and analysis.get("data_models"):
        models = analysis.get("data_models", [])
        if isinstance(models, list) and len(models) > 0:
            first_m = models[0]
            name = first_m.get("name", "") if isinstance(first_m, dict) else str(first_m)
            clean_name = re.sub(r"[^a-zA-Z0-9]", "", name)
            if clean_name and clean_name.lower() not in ("item", "post", "base", "model", "generic"):
                entity_name = clean_name.capitalize()
                singular_name = entity_name.lower()
                plural_name = singular_name + "s" if not singular_name.endswith("s") else singular_name + "es"

    if entity_name == "Item":
        domain_patterns = [
            ("recipe", "Recipe", "recipes"),
            ("book", "Book", "books"),
            ("product", "Product", "products"),
            ("movie", "Movie", "movies"),
            ("contact", "Contact", "contacts"),
            ("expense", "Expense", "expenses"),
            ("user", "User", "users"),
            ("student", "Student", "students"),
            ("employee", "Employee", "employees"),
            ("event", "Event", "events"),
            ("order", "Order", "orders"),
            ("comment", "Comment", "comments"),
            ("bookmark", "Bookmark", "bookmarks"),
            ("customer", "Customer", "customers"),
            ("course", "Course", "courses"),
            ("song", "Song", "songs"),
        ]
        for kw, ent, pl in domain_patterns:
            if kw in raw_text:
                entity_name = ent
                plural_name = pl
                singular_name = kw
                break

    return {
        "type": "custom",
        "entity_name": entity_name,
        "plural_name": plural_name,
        "singular_name": singular_name,
        "title": f"{entity_name} API",
        "description": f"Domain service for managing {plural_name} with SQLite persistence",
    }


def synthesize_complete_file(
    path: str,
    description: str,
    project_name: str,
    prompt: str,
    architecture: Dict[str, Any],
    analysis: Optional[Dict[str, Any]] = None,
) -> str:
    """Synthesizes complete, production-ready source code with real SQLite persistence.
    Guarantees working database.py, models.py, services.py, and main.py for any domain.
    """
    clean_p = path.strip().replace("\\", "/")
    filename = clean_p.split("/")[-1].lower()
    domain = detect_domain(prompt, analysis, project_name)
    p_name = project_name or domain["title"]

    if filename == "requirements.txt":
        return get_requirements_txt(architecture.get("dependencies", []))
    if filename.endswith(".md"):
        return get_readme_md(domain, p_name)
    if filename in ("config.py", "settings.py"):
        return get_config_py(p_name)
    if filename in ("database.py", "db.py"):
        return get_database_py()
    if filename in ("models.py", "schemas.py", "entities.py"):
        return get_models_py(domain)
    if filename in ("services.py", "service.py", "repository.py"):
        return get_services_py(domain)
    if filename in ("main.py", "app.py"):
        return get_main_py(domain, p_name)

    return get_models_py(domain)


# ---------------------------------------------------------------------------
# Writer helpers
# ---------------------------------------------------------------------------

FILE_BEGIN = "=====BEGIN FILE====="
FILE_END = "=====END FILE====="

# Dependency order: files are written so each one can see the real code it imports.
WRITE_ORDER = ["config.py", "settings.py", "database.py", "db.py", "models.py", "schemas.py",
               "repository.py", "services.py", "service.py", "main.py", "requirements.txt", "readme.md"]
TEST_FILE_PATH = "tests/test_api.py"
DETERMINISTIC_FILES = ("requirements.txt", "readme.md")
_TEST_FILE_DESCRIPTION = (
    "pytest suite for this exact code. Use `from fastapi.testclient import TestClient` and "
    "`from app.main import app`; create the client in a module-scoped fixture with "
    "`with TestClient(app) as client: yield client` so startup/lifespan runs and tables exist. "
    "Cover every endpoint in the requirements (happy path + 404/422 cases) and the key business rules. "
    "Use unique values (e.g. uuid) for unique fields and create the data each test needs inside that test. "
    "Only call routes, status codes and payload fields that exist in app/main.py and the schemas. "
    "Keep it to about 8-15 focused tests."
)


def _error_origin(output: str, paths: List[str]) -> Optional[str]:
    """Generated file of the LAST traceback frame (where the exception was raised)."""
    frames = re.findall(r"^\s*(?:[./\w-]*/)?((?:app|tests)/[\w/]+\.py):\d+", output or "", re.M)
    for f in reversed(frames):
        if f in paths:
            return f
    return None


def _failing_test_sources(output: str, test_code: str, limit: int = 2500) -> str:
    """Source of the failing test functions, so the Writer sees exactly what is expected."""
    names = list(dict.fromkeys(re.findall(r"(?:FAILED|ERROR) tests/[\w/]+\.py::(\w+)", output or "")))
    if not names or not test_code:
        return ""
    try:
        tree = ast.parse(test_code)
    except SyntaxError:
        return ""
    lines = test_code.splitlines()
    chunks = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            chunks.append("\n".join(lines[node.lineno - 1:node.end_lineno]))
    out = "\n\n".join(chunks)
    return out[:limit] + ("\n# ...(truncated)" if len(out) > limit else "")


def _status_asserts(code: str) -> Dict[str, int]:
    counts = {"2xx": 0, "4xx": 0}
    for m in re.finditer(r"status_code\s*==\s*(?:status\.HTTP_)?(\d{3})", code or ""):
        k = m.group(1)[0] + "xx"
        if k in counts:
            counts[k] += 1
    counts["tests"] = len(re.findall(r"^def test_|^async def test_", code or "", re.M))
    counts["asserts"] = len(re.findall(r"^\s*assert\b", code or "", re.M))
    return counts


def _tests_weakened(old: str, new: str) -> Optional[str]:
    """Deterministic guard against 'fixing' tests by weakening them."""
    a, b = _status_asserts(old), _status_asserts(new)
    if b["4xx"] < a["4xx"]:
        return f"error-case status assertions dropped from {a['4xx']} to {b['4xx']}"
    if b["tests"] < a["tests"]:
        return f"number of tests dropped from {a['tests']} to {b['tests']}"
    if b["asserts"] < a["asserts"] * 0.8:
        return f"assertions dropped from {a['asserts']} to {b['asserts']}"
    return None


def _focus_pytest_output(output: str, limit: int = 3500) -> str:
    """The useful part of pytest output for the LLM: per failure, the frames inside the generated
    project (app/, tests/) and the `E` exception lines. Library frames (site-packages, stdlib)
    are dropped - they used to fill the whole budget and hide the real error."""
    out = output or ""
    idx = min([i for i in (out.find("= FAILURES ="), out.find("= ERRORS =")) if i != -1] or [0])
    lines = out[idx:].splitlines()
    kept: List[str] = []
    seen_e = set()
    keep_code = 0
    lib_frame = re.compile(r"^\s*(\.\./|/)?\S*(site-packages|/usr/lib|lib/python3|\.venv)\S*\.py:\d+")
    own_frame = re.compile(r"^\s*(app|tests)/\S+\.py:\d+")
    for ln in lines:
        if ln.startswith("____") or ln.startswith("===") or ln.startswith("FAILED") or ln.startswith("ERROR"):
            kept.append(ln); keep_code = 0
        elif own_frame.match(ln):
            kept.append(ln); keep_code = 3
        elif lib_frame.match(ln):
            keep_code = 0
        elif ln.startswith("E "):
            key = ln.strip()
            if key not in seen_e:
                seen_e.add(key); kept.append(ln)
        elif keep_code > 0:
            kept.append(ln); keep_code -= 1
    focused = "\n".join(kept)
    if len(focused) > limit:
        summary_at = focused.rfind("short test summary info")
        tail = focused[summary_at - 20:] if summary_at != -1 else ""
        tail = tail[-1500:]
        focused = focused[: max(800, limit - len(tail))] + "\n...\n" + tail
    return focused


def _plan_sort_key(planned: Dict[str, Any]) -> Tuple[int, str]:
    path = planned.get("path", "")
    base = path.split("/")[-1].lower()
    if base in WRITE_ORDER:
        return (WRITE_ORDER.index(base), path)
    # Unknown python modules (routers, repositories...) go before main.py
    if base.endswith(".py"):
        return (WRITE_ORDER.index("main.py") - 1, path)
    return (len(WRITE_ORDER), path)


def _cap_file_plan(file_plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensures app/database.py exists and caps the plan to 7 files (her original rule)."""
    file_plan = list(file_plan)
    has_db_plan = any("database.py" in f.get("path", "").lower() or "db.py" in f.get("path", "").lower() for f in file_plan)
    if not has_db_plan:
        file_plan.append({
            "path": "app/database.py",
            "description": "SQLAlchemy SQLite engine, SessionLocal, Base, and get_db()",
            "language": "python",
            "exports": ["engine", "SessionLocal", "Base", "get_db"],
        })
    standard_names = ["main.py", "models.py", "services.py", "database.py", "config.py", "requirements.txt", "readme.md"]
    selected: List[Dict[str, Any]] = []
    selected_bases = set()
    for std_name in standard_names:
        for f in file_plan:
            base = f.get("path", "").split("/")[-1].lower()
            if base == std_name and base not in selected_bases:
                selected.append(f)
                selected_bases.add(base)
                break
    for f in file_plan:
        if len(selected) >= 7:
            break
        if f not in selected:
            selected.append(f)
    return sorted(selected[:7], key=_plan_sort_key)


def _interface_contract(file_plan: List[Dict[str, Any]]) -> str:
    """Human-readable file plan + interface contract shared with every Writer call."""
    lines = []
    for f in file_plan:
        line = f"- {f.get('path')}: {f.get('description', '')}"
        if f.get("exports"):
            line += f"\n    exports: {', '.join(map(str, f['exports']))}"
        if f.get("imports_from"):
            line += f"\n    imports from: {', '.join(map(str, f['imports_from']))}"
        lines.append(line)
    return "\n".join(lines)


def _extract_file_body(text: str) -> Optional[str]:
    """Pulls one file's content out of an LLM reply.
    Returns None if the BEGIN marker is present but END is missing (= truncated)."""
    text = text or ""
    # Models sometimes mangle the markers (e.g. "=====END FILE" or "== END FILE =="). Normalise them.
    text = re.sub(r"^[ \t]*=+[ \t]*BEGIN[ \t]*FILE[ \t]*=*[ \t]*$", FILE_BEGIN, text, flags=re.M | re.I)
    text = re.sub(r"^[ \t]*=+[ \t]*END[ \t]*FILE[ \t]*=*[ \t]*$", FILE_END, text, flags=re.M | re.I)
    if FILE_BEGIN in text:
        after = text.split(FILE_BEGIN, 1)[1]
        if FILE_END not in after:
            # finish_reason was checked before this (truncation is caught there), so a
            # missing END marker usually just means the model forgot it. Accept the rest;
            # .py files are still validated with ast.parse.
            if not after.strip():
                return None
            body = after.strip("\n")
            stripped = body.strip()
            if stripped.startswith("```"):
                body = extract_code_block(stripped)
            elif stripped.endswith("```"):
                body = stripped[: -3]
            return body.strip("\n") + "\n"
        body = after.split(FILE_END, 1)[0].strip("\n")
        # Strip a single wrapping code fence if the model added one inside the markers
        stripped = body.strip()
        if stripped.startswith("```") and stripped.rstrip().endswith("```"):
            body = extract_code_block(stripped)
        return body.strip("\n") + "\n"
    body = extract_code_block(text)
    return (body.strip("\n") + "\n") if body.strip() else ""


def _validate_file(path: str, content: Optional[str]) -> Optional[str]:
    """Returns an error message if the file is unusable, else None."""
    if content is None:
        return "output was truncated (no end marker) - likely hit max_tokens"
    if not content.strip():
        return "file is empty"
    if path.startswith("tests/") and path.endswith(".py") and "def test_" not in content:
        return "this must be the pytest test module (with `def test_...` functions), not another project file"
    if path.endswith(".py"):
        try:
            # NOTE: a fake filename - with the real relative path, Python's linecache would show the
            # line from the BACKEND's own file of the same name (e.g. backend/app/main.py), and that
            # misleading line was fed back to the LLM as "the error".
            ast.parse(content, filename=f"<generated {path}>")
        except SyntaxError as se:
            lines = content.splitlines()
            ln = se.lineno or 0
            snippet = "\n".join(f"{i}: {lines[i - 1]}" for i in range(max(1, ln - 3), min(len(lines), ln + 1) + 1)) if lines else ""
            return f"SyntaxError at line {ln}: {se.msg}. Code around it:\n{snippet[:800]}"
        undefined = undefined_names(content)
        if undefined:
            return ("undefined names (missing import or typo) - " + "; ".join(undefined[:8]))
    return None


def _module_name(path: str) -> str:
    p = path.replace("\\", "/")
    if p.endswith("/__init__.py"):
        p = p[: -len("/__init__.py")]
    elif p.endswith(".py"):
        p = p[:-3]
    return p.replace("/", ".")


def _top_level_names(tree: ast.Module) -> set:
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.Try, ast.If)):
            # try/except ImportError blocks and `if` guards at module level
            sub = ast.Module(body=list(node.body) + [x for h in getattr(node, "handlers", []) for x in h.body] + list(getattr(node, "orelse", [])), type_ignores=[])
            names |= _top_level_names(sub)
    return names


def check_internal_imports(files_map: Dict[str, str]) -> Dict[str, List[str]]:
    """Static cross-file check: every `from <generated module> import X` must find X.
    Returns {file_path: [problems]}."""
    py_files = {p: c for p, c in files_map.items() if p.endswith(".py")}
    modules: Dict[str, set] = {}
    trees: Dict[str, ast.Module] = {}
    for p, c in py_files.items():
        try:
            trees[p] = ast.parse(c)
            modules[_module_name(p)] = _top_level_names(trees[p])
        except SyntaxError:
            continue
    problems: Dict[str, List[str]] = {}
    for p, tree in trees.items():
        pkg = _module_name(p).rsplit(".", 1)[0] if "." in _module_name(p) else ""
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            mod = node.module
            if node.level:  # relative import: from .models import X
                base = pkg.split(".") if pkg else []
                if node.level > 1:
                    base = base[: len(base) - (node.level - 1)]
                mod = ".".join(base + [node.module])
            candidates = [mod, f"app.{mod}"]
            target = next((m for m in candidates if m in modules), None)
            if target is None:
                continue
            for alias in node.names:
                if alias.name != "*" and alias.name not in modules[target]:
                    problems.setdefault(p, []).append(
                        f"imports '{alias.name}' from {target}, but {target} does not define it "
                        f"(defined: {', '.join(sorted(n for n in modules[target] if not n.startswith('_'))[:40])})"
                    )
    return problems


def _build_other_files(generated: Dict[str, str], path: str, full_paths: List[str], stub_all: bool) -> str:
    """Context of already-written files: full source for `full_paths`, interface stubs for the rest."""
    chunks = []
    for p, c in generated.items():
        if p == path or not p.endswith(".py") or not c.strip():
            continue
        if p in full_paths and not stub_all:
            chunks.append(f"--- {p} (full source) ---\n{c}")
        else:
            chunks.append(f"--- {p} (interface only; bodies omitted) ---\n{stub_source(c)}")
    return "\n\n".join(chunks)


def _generate_one_file(
    llm: Any,
    run_id: str,
    planned: Dict[str, Any],
    base_context: str,
    generated: Dict[str, str],
    feedback: Optional[str] = None,
    current_content: Optional[str] = None,
    full_paths: Optional[List[str]] = None,
    extra_rules: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    """Generates ONE file. Validates it and retries once on failure.
    Prompts are kept under GROQ_INPUT_TOKEN_BUDGET (Groq free tier rejects requests larger
    than its tokens-per-minute limit with 413): other files are sent as interface stubs,
    and the context is shrunk further if Groq still says "Request too large".
    Returns (content, error). Fatal LLM errors (bad API key, daily quota) are raised."""
    path = planned.get("path", "")
    full_paths = list(full_paths or [])

    system_prompt = (
        "You are an expert FastAPI + SQLAlchemy 2.x + Pydantic v2 developer writing ONE file of a larger project.\n"
        "Rules:\n"
        "1. Output ONLY the complete content of the requested file between these exact marker lines:\n"
        f"{FILE_BEGIN}\n<file content>\n{FILE_END}\n"
        "2. No placeholders, no '...', no TODO stubs. The file must be complete and runnable.\n"
        "3. Honour the interface contract: define exactly the names listed in 'exports' for this file, and only import names "
        "that are actually defined in the already-written files shown to you (or in the contract for files not written yet).\n"
        "4. Use package-absolute imports such as 'from app.models import X'. No try/except ImportError import fallbacks.\n"
        "5. Keep it focused and reasonably short (well under 300 lines) so it fits in one response.\n"
        "6. Use Pydantic v2 (model_config = ConfigDict(from_attributes=True), model_dump) and SQLAlchemy 2.x APIs; "
        "do not use deprecated orm_mode, .dict() or @app.on_event.\n"
        f"7. Avoid these known pitfalls:\n{COMMON_PITFALLS}"
        + (f"\n{extra_rules}" if extra_rules else "")
    )

    budget = int(settings.groq_input_token_budget)
    last_error: Optional[str] = None
    attempt = 0
    shrink_level = 0  # 0 = as requested, 1 = stubs only, 2 = trimmed feedback/context
    while attempt < max(1, int(settings.writer_max_attempts)):
        stub_all = shrink_level >= 1
        other_files = _build_other_files(generated, path, full_paths, stub_all)
        fb = feedback or ""
        cur = current_content
        # Fit the prompt into the input budget
        def assemble() -> List[str]:
            parts = [base_context, f"FILE TO WRITE NOW: {path}\nPurpose: {planned.get('description', '')}"]
            if planned.get("exports"):
                parts.append(f"This file MUST define: {', '.join(map(str, planned['exports']))}")
            if other_files:
                parts.append(f"ALREADY-WRITTEN PROJECT FILES (import only names that exist here):\n{other_files}")
            if cur is not None:
                parts.append(f"CURRENT VERSION OF {path} (fix it, return the FULL corrected file):\n{cur}")
            if fb:
                parts.append(f"PROBLEMS TO FIX:\n{fb}")
            if last_error:
                parts.append(f"Your previous attempt for {path} was rejected: {last_error}\nReturn the full, corrected file.")
            return parts
        parts = assemble()
        size = estimate_tokens(system_prompt + "\n".join(parts))
        if size > budget and not stub_all:
            stub_all = True
            other_files = _build_other_files(generated, path, full_paths, True)
            parts = assemble(); size = estimate_tokens(system_prompt + "\n".join(parts))
        if size > budget and fb:
            fb = fb[-max(1500, len(fb) - int((size - budget) * 3.2)):]
            parts = assemble(); size = estimate_tokens(system_prompt + "\n".join(parts))
        if size > budget or shrink_level >= 2:
            keep = max(2000, len(other_files) - int((size - budget) * 3.2)) if size > budget else len(other_files) // 2
            other_files = other_files[:keep] + "\n# ...(context truncated to fit the token budget)"
            parts = assemble(); size = estimate_tokens(system_prompt + "\n".join(parts))
        logger.info(f"[{run_id}] Writer -> {path}: ~{size} input tokens (budget {budget}, stubs={stub_all})")

        try:
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content="\n\n".join(parts)),
            ])
        except Exception as e:
            if is_fatal_llm_error(e):
                raise
            if is_request_too_large(e) and shrink_level < 2:
                # Not the model's fault: shrink the prompt and try again without using up an attempt.
                shrink_level += 1
                budget = int(budget * 0.7)
                logger.warning(f"[{run_id}] Groq said request too large for {path}; shrinking context (level {shrink_level}).")
                continue
            last_error = f"LLM call failed: {e}"
            logger.warning(f"[{run_id}] Writer attempt {attempt + 1} for {path}: {last_error}")
            attempt += 1
            continue
        content = response.content if hasattr(response, "content") else str(response)
        if was_truncated(response):
            last_error = ("response hit max_tokens (finish_reason=length) - file truncated. "
                          "Write a shorter version: fewer comments/docstrings, no extra features")
        else:
            body = _extract_file_body(content)
            last_error = _validate_file(path, body)
            if last_error is None:
                return body, None
        logger.warning(f"[{run_id}] Writer attempt {attempt + 1} for {path} rejected: {last_error}")
        attempt += 1
    return None, last_error


def _files_mentioned(output: str, paths: List[str]) -> List[str]:
    """Generated files referenced in a pytest traceback / error output."""
    out = output.replace("\\", "/")
    hits = []
    for p in paths:
        if p in out or f"/{p}" in out or _module_name(p) in out:
            hits.append(p)
    return hits


def _to_file_list(files_map: Dict[str, str], file_plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for path_str, content_str in files_map.items():
        desc = next((f.get("description", "") for f in file_plan if f.get("path") == path_str), "Source code file")
        result.append({
            "path": path_str,
            "content": content_str,
            "language": detect_language(path_str),
            "size_bytes": len(content_str.encode("utf-8")),
            "description": desc,
        })
    return result


def _persist_intermediate(state: AgentState, run_id: str, logs: List[Dict[str, Any]], step: str, progress: int) -> None:
    try:
        from app.services.project_service import project_service
        intermediate_state = {**state, "logs": logs, "current_step": step, "progress": progress}
        project_service.runs[run_id] = intermediate_state
        project_service._persist_state(run_id, intermediate_state)
    except Exception:
        pass


def code_writer_node(state: AgentState) -> Dict[str, Any]:
    """Node 3: Code Writer.
    Generates the project ONE FILE PER LLM CALL in dependency order, so no single response
    can be truncated and each file sees the real code it imports. Every .py file is checked
    with ast.parse and retried once on failure; cross-file imports are checked statically.
    LLM failures are surfaced (run fails) - templates are only used when
    ALLOW_TEMPLATE_FALLBACK=true, and then for the WHOLE project, marked degraded.
    On a repair round (sent back by the Reviewer) it regenerates the files named in the
    pytest output, with the errors as feedback.
    """
    run_id = state.get("run_id", "unknown")
    prompt = state.get("prompt", "")
    project_name = state.get("project_name", "ACSA Project")
    analysis = state.get("analysis", {})
    architecture = state.get("architecture", {})
    file_plan = _cap_file_plan(state.get("file_plan", []))
    warnings = list(state.get("warnings", []))
    repair_round = int(state.get("repair_attempts", 0) or 0) > 0 and bool(state.get("files"))

    domain = detect_domain(prompt, analysis, project_name)
    logger.info(f"[{run_id}] Running Code Writer Node (planned {len(file_plan)} files, repair_round={repair_round})")

    logs = add_log(
        state,
        "writer",
        ("Code Writer repair round: fixing files based on failing test output..." if repair_round else
         f"Code Writer initiated: Generating {len(file_plan)} files one at a time for '{project_name}'..."),
    )
    _persist_intermediate(state, run_id, logs, "writer", 45)

    base_context = (
        f"Project Name: {project_name}\n"
        f"User Prompt: {prompt}\n"
        f"Requirements Summary: {analysis.get('summary', '')}\n"
        f"Features: {json.dumps(analysis.get('features', []))}\n"
        f"API Endpoints (ALL must be implemented): {json.dumps(analysis.get('api_endpoints', []))}\n"
        f"Data Models: {json.dumps(analysis.get('data_models', []))}\n"
        f"Architecture Overview: {architecture.get('overview', '')}\n"
        f"Dependencies: {json.dumps(architecture.get('dependencies', []))}\n\n"
        f"FILE PLAN AND INTERFACE CONTRACT:\n{_interface_contract(file_plan)}\n"
        f"- {TEST_FILE_PATH}: pytest suite (written later by the Tester; imports 'from app.main import app')"
    )

    try:
        llm = get_llm(temperature=0.15)
    except Exception as e:
        return _fail(state, logs, "writer", describe_fatal_llm_error(e))

    files_map: Dict[str, str] = {}
    failed: Dict[str, str] = {}

    try:
        if repair_round:
            # ---------------- Repair round ----------------
            files_map = {f["path"]: f["content"] for f in state.get("files", [])}
            report = state.get("test_report", {}) or {}
            output = report.get("output", "")
            candidates = [p for p in files_map if p.endswith(".py")
                          and not (p.endswith("__init__.py") and not files_map[p].strip())]
            mentioned = _files_mentioned(output, candidates)
            app_mentioned = [p for p in mentioned if p.startswith("app/")]
            origin = _error_origin(output, candidates)
            # Plain assertion failures only show the test file in the traceback; the bug is then
            # usually in the route/service logic, so revisit those (not config/database).
            default_app = [p for p in candidates if p.split("/")[-1] in ("main.py", "services.py", "service.py")]
            collection_error = "ERROR collecting" in output or "Interrupted:" in output
            hints = known_error_hints(output)
            # Files a known-error hint points at (e.g. "@contextmanager on get_db in app/database.py")
            hinted = [p for p in candidates if p.startswith("app/") and p in hints]
            assertion_failures = bool(re.search(r"^E\s+(assert |AssertionError)", output, re.M))
            if collection_error and origin:
                # Import-time crash: fix only the file where the exception was raised.
                to_fix = [origin]
            elif hinted:
                to_fix = hinted
            else:
                to_fix = [p for p in app_mentioned if p != TEST_FILE_PATH] or default_app or [p for p in candidates if p.startswith("app/")]
            # The LLM-written tests can be wrong too (e.g. expecting 400 where 422 is equally valid).
            # The test file is revised AFTER the app, against the repaired code, only when tests fail on
            # plain assertions (not when the app crashes), and only for expectations the prompt doesn't require.
            round_no = int(state.get("repair_attempts", 0) or 0)
            if (round_no >= 2 and assertion_failures and not collection_error and not hinted
                    and TEST_FILE_PATH in files_map and TEST_FILE_PATH not in to_fix):
                to_fix.append(TEST_FILE_PATH)
            short_out = _focus_pytest_output(output)
            failing_src = _failing_test_sources(output, files_map.get(TEST_FILE_PATH, ""))
            feedback = (
                f"pytest result: {report.get('summary', 'failed')}\n"
                + (f"The exception is raised in {origin}.\n" if origin else "")
                + (f"KNOWN CAUSE / HOW TO FIX:\n{hints}\n" if hints else "")
                + f"Failing output:\n{short_out}\n"
                + (f"\nSOURCE OF THE FAILING TESTS (this is the expected behaviour):\n{failing_src}\n" if failing_src else "")
                + (f"\nDependency install error:\n{report['install_error'][-1500:]}" if report.get("install_error") else "")
                + "\nFix the root cause."
            )
            plan_by_path = {f.get("path"): f for f in file_plan}
            rewritten = []
            for path in sorted(to_fix, key=lambda p: (p == TEST_FILE_PATH, _plan_sort_key({"path": p}))):
                if path == TEST_FILE_PATH:
                    planned = {"path": path, "description": _TEST_FILE_DESCRIPTION}
                    rules = ("You are revising the pytest suite after the app code was fixed. The tests are the SPEC: keep them "
                             "strict. You MAY only fix test mistakes: wrong URLs, wrong payload/response field names compared with the "
                             "schemas, wrong helper usage, tests depending on each other, or a choice between equivalent codes "
                             "(200 vs 201 for create, 200 vs 204 for delete, 400 vs 409 vs 422 for invalid input). You MUST NOT "
                             "change a test that expects 404 for a missing resource, must NOT make any test accept a 2xx response "
                             "for an error case, and must NOT remove assertions on the required business logic. Use uuid for unique values.")
                    full = ["app/main.py", "app/models.py", "app/schemas.py"]
                else:
                    planned = plan_by_path.get(path, {"path": path, "description": "project file"})
                    rules = ("Fix this file so the failing tests pass when the test expectation matches the user prompt. "
                             "Keep the same public names (other files import them).")
                    # main.py and services.py must agree on behaviour (None vs exception, return values):
                    # show the counterpart in full when it fits the budget.
                    counterpart = {"main.py": ["app/services.py", "app/service.py"], "services.py": ["app/main.py"],
                                   "service.py": ["app/main.py"]}.get(path.split("/")[-1], [])
                    full = counterpart + [x for x in ("app/models.py", "app/schemas.py") if x != path]
                content, err = _generate_one_file(llm, run_id, planned, base_context, files_map,
                                                  feedback=feedback, current_content=files_map[path],
                                                  full_paths=full, extra_rules=rules)
                if content is not None and path == TEST_FILE_PATH:
                    weakened = _tests_weakened(files_map[path], content)
                    if weakened:
                        warnings.append(f"Rejected test revision ({weakened}); kept the stricter tests.")
                        logs.append({"timestamp": logs[-1]["timestamp"], "step": "writer",
                                     "message": f"Rejected test revision: {weakened}", "level": "warning"})
                        continue
                if content is not None:
                    files_map[path] = content
                    rewritten.append(path)
                else:
                    warnings.append(f"Repair of {path} failed ({err}); kept previous version.")
            logs.append({"timestamp": logs[-1]["timestamp"], "step": "writer",
                         "message": f"Repair round rewrote: {', '.join(rewritten) or 'nothing'}.", "level": "info"})
        else:
            # ---------------- Normal generation, one file per call ----------------
            for planned in file_plan:
                path = planned.get("path", "")
                if path.split("/")[-1].lower() in DETERMINISTIC_FILES:
                    continue  # built from the real code below, no LLM call
                full = [str(x) for x in (planned.get("imports_from") or [])]
                content, err = _generate_one_file(llm, run_id, planned, base_context, files_map, full_paths=full)
                if content is None:
                    failed[path] = err or "unknown error"
                else:
                    files_map[path] = content

        # Deterministic fix: FastAPI matches routes in order, so a generic `/x/{id}/{id2}` registered first
        # makes `/x/low/{id}` unreachable (422). Move such routes up instead of spending an LLM round.
        for mp in [p for p in files_map if p.endswith("main.py") or p.startswith("app/routers/")]:
            fixed, moved = fix_route_shadowing(files_map[mp])
            if moved:
                files_map[mp] = fixed
                warnings.append(f"{mp}: " + "; ".join(moved))
                logs.append({"timestamp": logs[-1]["timestamp"], "step": "writer",
                             "message": f"Route order fixed in {mp}: " + "; ".join(moved), "level": "info"})

        # Cross-file import consistency check (one fix pass per offending file)
        if not failed:
            problems = check_internal_imports(files_map)
            plan_by_path = {f.get("path"): f for f in file_plan}
            for path, issues in problems.items():
                planned = plan_by_path.get(path, {"path": path, "description": "project file"})
                logger.warning(f"[{run_id}] Import mismatch in {path}: {issues}")
                content, err = _generate_one_file(
                    llm, run_id, planned, base_context, files_map,
                    feedback="Import errors:\n- " + "\n- ".join(issues), current_content=files_map[path],
                )
                if content is not None:
                    files_map[path] = content
            remaining = check_internal_imports(files_map)
            if remaining:
                # Not fatal here: pytest will catch it and the Reviewer can trigger repair.
                warnings.append(f"Unresolved import mismatches: {json.dumps(remaining)[:500]}")
    except Exception as e:
        if is_fatal_llm_error(e):
            return _fail(state, logs, "writer", describe_fatal_llm_error(e))
        failed["<writer>"] = str(e)

    if not failed:
        # requirements.txt and README.md are derived from the real code (no LLM call, no stale pins)
        files_map["requirements.txt"] = build_requirements(files_map)
        files_map["README.md"] = build_readme(project_name, analysis, files_map)
        for name, desc in (("requirements.txt", "Python dependencies detected from the generated code"),
                           ("README.md", "Setup, run, test instructions and endpoint table")):
            if not any(f.get("path") == name for f in file_plan):
                file_plan.append({"path": name, "description": desc, "language": detect_language(name)})

    degraded = bool(state.get("degraded", False))
    source = "LLM generated"
    if failed:
        reason = "; ".join(f"{p}: {r}" for p, r in failed.items())
        if settings.allow_template_fallback:
            # Use the template for the WHOLE project so the files are consistent with each other.
            logger.warning(f"[{run_id}] Writer failed ({reason}); ALLOW_TEMPLATE_FALLBACK=true -> full template project.")
            files_map = {}
            for planned in file_plan:
                files_map[planned["path"]] = synthesize_complete_file(
                    planned["path"], planned.get("description", ""), project_name, prompt, architecture, analysis=analysis
                )
            degraded = True
            source = "TEMPLATE fallback (degraded)"
            warnings.append(f"Writer LLM failed, whole project replaced by generic '{domain['title']}' template: {reason}")
        else:
            # Save what we have for inspection, then fail loudly.
            if files_map:
                save_project_files(run_id, _to_file_list(files_map, file_plan))
            result = _fail(state, logs, "writer", f"Code Writer could not produce valid files: {reason}")
            result["files"] = _to_file_list(files_map, file_plan)
            return result

    # Make app/ an importable package
    if any(p.startswith("app/") and p.endswith(".py") for p in files_map) and "app/__init__.py" not in files_map:
        files_map["app/__init__.py"] = ""

    final_files_list = _to_file_list(files_map, file_plan)
    save_project_files(run_id, final_files_list)
    save_code_files(files_map, run_id)

    logs.append({
        "timestamp": logs[-1]["timestamp"] if logs else "",
        "step": "writer",
        "message": f"Code generation complete: {len(final_files_list)} files ({source}). All .py files passed ast.parse.",
        "level": "warning" if degraded and source != "LLM generated" else "info",
    })

    return {
        "files": final_files_list,
        "code_files": files_map,
        "file_plan": file_plan,
        "current_step": "tester",
        "progress": 65,
        "logs": logs,
        "degraded": degraded,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Tester
# ---------------------------------------------------------------------------

def _static_smoke_tests(domain: Dict[str, Any]) -> str:
    """Her original generic tests, used only if the LLM cannot write tests (run is marked degraded)."""
    pl = domain["plural_name"]
    return (
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n\n\n"
        "def test_read_root():\n"
        "    with TestClient(app) as client:\n"
        "        assert client.get('/').status_code == 200\n\n\n"
        f"def test_list_{pl}():\n"
        "    with TestClient(app) as client:\n"
        f"        assert client.get('/{pl}').status_code == 200\n"
    )


def _parse_pytest_summary(output: str) -> Dict[str, int]:
    counts = {}
    for key in ("passed", "failed", "errors", "error", "skipped"):
        m = re.findall(rf"(\d+) {key}\b", output)
        if m:
            counts["errors" if key == "error" else key] = counts.get("errors" if key == "error" else key, 0) + int(m[-1])
    return counts


def run_pytest_on_project(project_dir: Path) -> Dict[str, Any]:
    """Copies the generated project to a temp dir, installs its requirements into a fresh
    venv (if TEST_USE_VENV), and runs pytest in a subprocess with a timeout."""
    tmp = Path(tempfile.mkdtemp(prefix="acsa_test_"))
    report: Dict[str, Any] = {"passed": False, "environment": "current interpreter", "install_error": None}
    try:
        work = tmp / "project"
        shutil.copytree(
            project_dir, work,
            ignore=shutil.ignore_patterns("acsa_state.json", "__pycache__", "*.db", ".pytest_cache", ".venv"),
        )
        python = sys.executable
        if settings.test_use_venv:
            venv_dir = tmp / ".venv"
            try:
                subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True,
                               capture_output=True, text=True, timeout=120)
                vpy = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                cmd = [str(vpy), "-m", "pip", "install", "--disable-pip-version-check", "-q", "pytest", "httpx"]
                if (work / "requirements.txt").exists():
                    cmd += ["-r", "requirements.txt"]
                inst = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=settings.test_install_timeout)
                if inst.returncode == 0:
                    python = str(vpy)
                    report["environment"] = "fresh venv with requirements.txt"
                else:
                    report["install_error"] = (inst.stdout + inst.stderr)[-4000:]
            except subprocess.TimeoutExpired:
                report["install_error"] = f"dependency install timed out after {settings.test_install_timeout}s"
            except Exception as e:
                report["install_error"] = f"could not create venv: {e}"
            if report["install_error"]:
                logger.warning(f"venv install failed, running pytest in current interpreter: {report['install_error'][:300]}")

        env = {**os.environ, "PYTHONPATH": str(work), "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("GROQ_API_KEY", None)  # generated code never needs her key
        try:
            proc = subprocess.run(
                [python, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider"],
                cwd=work, env=env, capture_output=True, text=True, timeout=settings.test_timeout,
            )
            output = proc.stdout + proc.stderr
            report["exit_code"] = proc.returncode
        except subprocess.TimeoutExpired as te:
            output = ((te.stdout or b"").decode(errors="replace") if isinstance(te.stdout, bytes) else (te.stdout or ""))
            output += f"\nTIMEOUT: pytest did not finish within {settings.test_timeout}s"
            report["exit_code"] = -1
        counts = _parse_pytest_summary(output)
        report.update({k: v for k, v in counts.items() if k != "passed"})
        report["passed_count"] = counts.get("passed", 0)
        report["output"] = output[-40000:]
        report["passed"] = report["exit_code"] == 0 and counts.get("passed", 0) > 0
        last_line = next((l for l in reversed(output.strip().splitlines()) if l.strip()), "")
        report["summary"] = last_line.strip("= ")[:300] or f"exit code {report['exit_code']}"
        if report["exit_code"] == 5:
            report["summary"] = "no tests were collected"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return report


def tester_node(state: AgentState) -> Dict[str, Any]:
    """Node 4: Tester.
    Writes a pytest suite from the ACTUAL generated code (LLM), then really runs it.
    On a repair round the existing (possibly repaired) test file is reused.
    """
    run_id = state.get("run_id", "unknown")
    files = list(state.get("files", []))
    project_name = state.get("project_name", "ACSA Project")
    prompt = state.get("prompt", "")
    analysis = state.get("analysis", {})
    warnings = list(state.get("warnings", []))
    degraded = bool(state.get("degraded", False))
    logger.info(f"[{run_id}] Running Tester Node")

    logs = add_log(state, "tester", f"Tester initiated: preparing and running pytest for '{project_name}'...")
    _persist_intermediate(state, run_id, logs, "tester", 70)

    domain = detect_domain(prompt, analysis, project_name)
    files_map = {f["path"]: f["content"] for f in files}
    test_file = next((f for f in files if f.get("path") == TEST_FILE_PATH), None)

    if test_file is None:
        planned = {"path": TEST_FILE_PATH, "description": _TEST_FILE_DESCRIPTION}
        base_context = (
            f"Project: {project_name}\nUser Prompt: {prompt}\n"
            f"API Endpoints: {json.dumps(analysis.get('api_endpoints', []))}\n"
        )
        content, err = None, None
        try:
            llm = get_llm(temperature=0.1)
            content, err = _generate_one_file(llm, run_id, planned, base_context, files_map,
                                              full_paths=["app/main.py", "app/models.py", "app/schemas.py"])
        except Exception as e:
            if is_fatal_llm_error(e):
                return _fail(state, logs, "tester", describe_fatal_llm_error(e))
            err = str(e)
        if content is None:
            content = _static_smoke_tests(domain)
            degraded = True
            warnings.append(f"Tester LLM failed ({err}); using generic smoke tests only.")
            logs.append({"timestamp": logs[-1]["timestamp"], "step": "tester",
                         "message": f"DEGRADED: test generation failed ({err}); using generic smoke tests.", "level": "warning"})
        test_file = {
            "path": TEST_FILE_PATH,
            "content": content,
            "language": "python",
            "description": f"Automated pytest suite for {project_name}",
            "size_bytes": len(content.encode("utf-8")),
        }
        files.append(test_file)

    save_project_files(run_id, [test_file])
    if "tests/__init__.py" not in files_map:
        save_project_files(run_id, [{"path": "tests/__init__.py", "content": ""}])

    project_dir = settings.projects_dir / run_id
    report = run_pytest_on_project(project_dir)
    if report.get("install_error"):
        warnings.append("Could not install requirements.txt in a clean venv; tests ran in the backend's own environment.")

    logs.append({
        "timestamp": logs[-1]["timestamp"],
        "step": "tester",
        "message": f"pytest {'PASSED' if report['passed'] else 'FAILED'} ({report.get('environment')}): {report.get('summary')}",
        "level": "info" if report["passed"] else "error",
    })

    return {
        "test_suite": {
            "test_framework": "pytest",
            "test_command": "python -m pytest -q",
            "test_strategy": f"Generated from the actual code and executed in a subprocess ({report.get('environment')})",
            "test_files": [test_file],
        },
        "test_report": report,
        "files": files,
        "current_step": "reviewer",
        "progress": 85,
        "logs": logs,
        "degraded": degraded,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------

def code_reviewer_node(state: AgentState) -> Dict[str, Any]:
    """Node 5: Code Reviewer.
    Ships ONLY when pytest passed. If tests failed and a repair round is still available,
    sends the run back to the Writer with the errors; otherwise marks the run failed.
    """
    run_id = state.get("run_id", "unknown")
    files = state.get("files", [])
    project_name = state.get("project_name", "ACSA Project")
    report = state.get("test_report", {}) or {}
    attempts = int(state.get("repair_attempts", 0) or 0)
    degraded = bool(state.get("degraded", False))
    warnings = list(state.get("warnings", []))
    logger.info(f"[{run_id}] Running Code Reviewer Node (tests passed={report.get('passed')}, repairs used={attempts})")

    logs = add_log(state, "reviewer", f"Code Reviewer initiated: checking test results for '{project_name}'...")
    _persist_intermediate(state, run_id, logs, "reviewer", 88)

    if not report.get("passed") and attempts < settings.max_repair_attempts:
        logs.append({
            "timestamp": logs[-1]["timestamp"], "step": "reviewer",
            "message": f"Tests failed ({report.get('summary')}). Sending errors back to the Code Writer (repair {attempts + 1}/{settings.max_repair_attempts}).",
            "level": "warning",
        })
        return {"repair_attempts": attempts + 1, "current_step": "writer", "progress": 60, "logs": logs}

    try:
        zip_path = create_project_zip(run_id, project_name)
        logger.info(f"[{run_id}] Project packaged into zip at {zip_path}")
    except Exception as ze:
        logger.warning(f"Zip creation error: {ze}")

    passed = bool(report.get("passed"))
    counts = f"{report.get('failed', 0)} failed, {report.get('errors', 0)} errors"
    if passed:
        verdict = "Ready to ship (degraded)" if degraded else "Ready to ship"
        score = 80 if degraded else 95
        summary = f"All tests passed: {report.get('summary')}."
    else:
        verdict = "Not ready - tests failing"
        score = 30
        summary = f"Tests still failing after {attempts} repair round(s): {report.get('summary')} ({counts})."

    review_data = {
        "score": score,
        "verdict": verdict,
        "summary": summary,
        "tests": {
            "passed": passed,
            "summary": report.get("summary"),
            "environment": report.get("environment"),
            "output_tail": (report.get("output") or "")[-3000:],
        },
        "repair_attempts": attempts,
        "degraded": degraded,
        "warnings": warnings,
        "strengths": [],
        "suggestions": [] if passed else ["Inspect the pytest output above; the generated code does not pass its own tests."],
        "security_notes": [],
        "performance_notes": [],
    }

    logs.append({
        "timestamp": logs[-1]["timestamp"],
        "step": "reviewer",
        "message": f"Review completed: {verdict} ({score}/100). {summary}",
        "level": "info" if passed else "error",
    })

    result: Dict[str, Any] = {
        "review": review_data,
        "current_step": "completed" if passed else "reviewer",
        "status": "completed" if passed else "failed",
        "progress": 100,
        "logs": logs,
    }
    if not passed:
        result["error"] = summary
    return result
