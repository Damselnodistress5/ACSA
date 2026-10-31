"""Deterministic helpers for the Code Writer / Tester.

* Token budgeting for Groq's free tier (8k tokens-per-minute on gpt-oss-20b): a single
  request larger than the TPM limit is rejected with HTTP 413 "Request too large".
* Compact "interface stubs" of already-written files (signatures, fields, routes)
  instead of the full source, so prompts stay small.
* requirements.txt and README.md are built from the real generated code instead of
  asking the LLM (the LLM used to pin ancient versions such as pydantic-core builds
  that cannot compile on Python 3.13, and README calls wasted ~5k tokens).
"""
import ast
import json
import re
import sys
from typing import Any, Dict, List, Optional


def estimate_tokens(text: str) -> int:
    """Rough, deliberately pessimistic token estimate (code is ~3.2 chars/token)."""
    return int(len(text or "") / 3.2) + 1


class _Stubber(ast.NodeTransformer):
    """Keeps imports, class fields, decorators and signatures; drops function bodies."""

    def _stub_func(self, node):
        doc = ast.get_docstring(node)
        body: List[ast.stmt] = []
        if doc:
            body.append(ast.Expr(ast.Constant(doc.strip().splitlines()[0][:120])))
        # Keep `raise HTTPException(...)` lines: tests need to know the error status codes.
        # Keep the behaviour other files depend on: what it raises / when it returns None.
        notes = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Raise) and sub.exc is not None:
                exc = sub.exc.func if isinstance(sub.exc, ast.Call) else sub.exc
                src = ast.unparse(sub)[:140] if "HTTPException" in ast.unparse(exc) else f"raise {ast.unparse(exc)}(...)"
                if src not in notes:
                    notes.append(src)
            elif isinstance(sub, ast.Return) and (sub.value is None or (isinstance(sub.value, ast.Constant) and sub.value.value is None)):
                if "return None" not in notes:
                    notes.append("return None")
        for r in notes[:5]:
            body.append(ast.Expr(ast.Constant(f"may: {r}")))
        body.append(ast.Expr(ast.Constant(...)))
        node.body = body
        return node

    def visit_FunctionDef(self, node):
        return self._stub_func(node)

    def visit_AsyncFunctionDef(self, node):
        return self._stub_func(node)


def stub_source(code: str) -> str:
    """Interface-only view of a python file. Falls back to the raw code if it can't parse."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    tree = _Stubber().visit(tree)
    ast.fix_missing_locations(tree)
    try:
        return ast.unparse(tree)
    except Exception:
        return code


# ---------------------------------------------------------------------------
# requirements.txt
# ---------------------------------------------------------------------------

# import name -> pip requirement (unpinned lower bounds: always resolvable on new Pythons)
PIP_NAMES = {
    "fastapi": "fastapi>=0.110",
    "uvicorn": "uvicorn[standard]>=0.27",
    "pydantic": "pydantic>=2.6",
    "pydantic_settings": "pydantic-settings>=2.2",
    "sqlalchemy": "sqlalchemy>=2.0",
    "httpx": "httpx>=0.27",
    "pytest": "pytest>=8.0",
    "email_validator": "email-validator>=2.0",
    "dotenv": "python-dotenv>=1.0",
    "multipart": "python-multipart>=0.0.9",
    "jose": "python-jose[cryptography]>=3.3",
    "jwt": "PyJWT>=2.8",
    "passlib": "passlib[bcrypt]>=1.7",
    "bcrypt": "bcrypt>=4.0",
    "yaml": "PyYAML>=6.0",
    "requests": "requests>=2.31",
    "aiofiles": "aiofiles>=23.2",
    "dateutil": "python-dateutil>=2.8",
    "starlette": None,  # comes with fastapi
    "typing_extensions": None,
    "annotated_types": None,
}
ALWAYS = ["fastapi", "uvicorn", "pydantic", "httpx", "pytest"]


def build_requirements(files_map: Dict[str, str]) -> str:
    local_tops = {p.split("/")[0].replace(".py", "") for p in files_map if p.endswith(".py")}
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    found: List[str] = []
    text_all = "\n".join(c for p, c in files_map.items() if p.endswith(".py"))
    for p, c in files_map.items():
        if not p.endswith(".py"):
            continue
        try:
            tree = ast.parse(c)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                mods = [node.module]
            for m in mods:
                top = m.split(".")[0]
                if top in stdlib or top in local_tops or top in ("app", "tests"):
                    continue
                if top not in found:
                    found.append(top)
    if "EmailStr" in text_all and "email_validator" not in found:
        found.append("email_validator")
    if re.search(r"\b(Form|UploadFile|File)\s*\(", text_all) and "multipart" not in found:
        found.append("multipart")
    lines: List[str] = []
    for top in ALWAYS + found:
        req = PIP_NAMES.get(top, top.replace("_", "-"))
        if req and req not in lines:
            lines.append(req)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# README.md
# ---------------------------------------------------------------------------

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def extract_routes(main_code: str) -> List[Dict[str, str]]:
    """[(METHOD, path, summary)] from @app.get('/x') / @router.post(...) decorators."""
    routes: List[Dict[str, str]] = []
    try:
        tree = ast.parse(main_code)
    except SyntaxError:
        return routes
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in HTTP_METHODS and dec.args
                    and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str)):
                doc = (ast.get_docstring(node) or node.name.replace("_", " ")).strip().splitlines()[0]
                routes.append({"method": dec.func.attr.upper(), "path": dec.args[0].value, "summary": doc})
    return routes


def build_readme(project_name: str, analysis: Dict[str, Any], files_map: Dict[str, str],
                 test_summary: Optional[str] = None) -> str:
    routes = extract_routes(files_map.get("app/main.py", ""))
    features = analysis.get("features") or []
    out = [f"# {project_name}", "", analysis.get("summary", "").strip() or "FastAPI service generated by ACSA.", ""]
    if features:
        out += ["## Features", ""] + [f"- {f}" for f in features] + [""]
    out += [
        "## Tech stack", "",
        ", ".join(analysis.get("tech_stack") or ["FastAPI", "Pydantic", "Uvicorn"]), "",
        "## Setup", "",
        "```bash",
        "python3 -m venv .venv",
        "source .venv/bin/activate        # Windows: .venv\\Scripts\\activate",
        "pip install -r requirements.txt",
        "```", "",
        "## Run", "",
        "```bash",
        "uvicorn app.main:app --reload",
        "```", "",
        "Open http://127.0.0.1:8000/docs for the interactive Swagger UI.", "",
        "## Test", "",
        "```bash",
        "python -m pytest -q",
        "```", "",
    ]
    if test_summary:
        out += [f"Last ACSA test run: `{test_summary}`", ""]
    if routes:
        out += ["## API endpoints", "", "| Method | Path | Description |", "|---|---|---|"]
        out += [f"| {r['method']} | `{r['path']}` | {r['summary']} |" for r in routes]
        out += [""]
    out += ["## Project layout", ""] + [f"- `{p}`" for p in sorted(files_map) if not p.endswith("__init__.py")] + [""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Known failure patterns -> concrete fix hints (sent with repair feedback)
# ---------------------------------------------------------------------------

COMMON_PITFALLS = (
    "- Never give a Pydantic field the same name as its type (e.g. `date: date`): use `import datetime as dt` and "
    "`date: dt.date`, or name the field `attendance_date`.\n"
    "- Never name a SQLAlchemy column `metadata` (reserved).\n"
    "- In route functions put parameters WITHOUT defaults (path params, request bodies) BEFORE parameters with defaults "
    "such as `db: Session = Depends(get_db)` or query params with defaults.\n"
    "- Pydantic v2: use `pattern=` not `regex=`, `model_dump()` not `.dict()`, `ConfigDict(from_attributes=True)`.\n"
    "- For SQLite with TestClient use `connect_args={'check_same_thread': False}`; create tables in the lifespan handler.\n"
    "- `get_db()` must be a plain generator (`db = SessionLocal(); try: yield db; finally: db.close()`), NEVER decorated "
    "with @contextmanager.\n"
    "- In-memory SQLite (`sqlite://` or `sqlite:///:memory:`) needs `poolclass=StaticPool` so all sessions share one DB.\n"
    "- Services return None for a missing record; app/main.py turns that into `HTTPException(status_code=404)`. "
    "Never let ValueError/LookupError escape a route.\n"
    "- Import every SQLAlchemy name you use (Integer, func, case, select...).\n"
    "- Return 404 via `raise HTTPException(status_code=404, ...)` when an id does not exist."
)

_HINTS = [
    (r"field name clashing with a type annotation|unevaluable-type-annotation",
     "A Pydantic model has a field whose NAME equals a type used in its annotation (typically `date: date` or "
     "`date: Optional[date]`). Fix: `import datetime as dt` and annotate `date: dt.date` (and `dt.datetime`), "
     "or rename the field everywhere (models, services, main, tests)."),
    (r"_GeneratorContextManager",
     "get_db() is decorated with @contextmanager. FastAPI dependencies must be plain generator functions: remove "
     "@contextmanager (and its import) from get_db in app/database.py."),
    (r"no such table|OperationalError", "Tables missing at request time. In app/database.py: if the URL is in-memory "
     "SQLite, create the engine with `poolclass=StaticPool` (from sqlalchemy.pool) and "
     "`connect_args={'check_same_thread': False}` so every session shares the one database; make sure "
     "Base.metadata.create_all(bind=engine) runs in the app lifespan after all models are imported."),
    (r"(?m)^app/main\.py:\d+: in .*\n(?:.*\n){0,3}?app/services\.py:\d+: in .*\n(?:.*\n){0,3}?E\s+(ValueError|LookupError|KeyError|Exception)",
     "A service raised an exception that escaped a route in app/main.py (-> 500). In app/main.py wrap the service call "
     "in try/except and raise HTTPException (404 for missing records, 400/409 for duplicates/invalid input)."),
    (r"Attribute name 'metadata' is reserved", "Rename the SQLAlchemy column `metadata` (reserved by the Declarative API)."),
    (r"`regex` is removed|regex.*use 'pattern' instead", "Pydantic v2: replace `regex=` with `pattern=`."),
    (r"email-validator is not installed", "EmailStr needs the email-validator package (requirements.txt is regenerated automatically)."),
    (r"SQLite objects created in a thread can only be used in that same thread",
     "create_engine(..., connect_args={'check_same_thread': False})"),
    (r"ResponseValidationError|response_model", "The returned object does not match the response_model: return data whose "
     "fields match the schema (use from_attributes=True for ORM objects) or adjust the schema."),
    (r"DetachedInstanceError", "Access ORM attributes before the session closes, or refresh/return Pydantic models."),
    (r"IntegrityError|UNIQUE constraint failed", "Duplicate unique value: tests must use unique values (uuid) and the "
     "service should catch IntegrityError and return 400/409."),
    (r"ModuleNotFoundError: No module named '(?!app)", "A third-party import is missing from the environment; only use "
     "fastapi, pydantic, sqlalchemy, httpx, pytest and the standard library."),
]


def known_error_hints(output: str) -> str:
    hints = []
    for pattern, hint in _HINTS:
        if re.search(pattern, output or ""):
            hints.append(f"- {hint}")
    return "\n".join(hints)


def undefined_names(code: str) -> List[str]:
    """pyflakes 'undefined name' findings (e.g. `Integer` used but never imported)."""
    try:
        from pyflakes import checker, messages
    except Exception:
        return []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    w = checker.Checker(tree, filename="<generated>")
    out = []
    for m in w.messages:
        if isinstance(m, (messages.UndefinedName, messages.UndefinedLocal)):
            out.append(f"line {m.lineno}: {m.message % m.message_args}")
    return out


# ---------------------------------------------------------------------------
# FastAPI route shadowing: `/attendance/{student_id}/{subject_id}` registered before
# `/attendance/low/{subject_id}` swallows the latter (-> 422 "low" is not an int).
# Fixed deterministically by moving the more specific route above the generic one.
# ---------------------------------------------------------------------------

def _route_infos(tree: ast.Module) -> List[Dict[str, Any]]:
    infos = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr in HTTP_METHODS and dec.args
                    and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str)):
                start = min([d.lineno for d in node.decorator_list] + [node.lineno])
                infos.append({"method": dec.func.attr, "path": dec.args[0].value,
                              "start": start, "end": node.end_lineno, "name": node.name})
                break
    return infos


def _shadows(a: str, b: str) -> bool:
    """True if route path `a` (registered first) would match every request meant for `b`."""
    sa, sb = a.strip("/").split("/"), b.strip("/").split("/")
    if len(sa) != len(sb) or a == b:
        return False
    swallowed = False
    for x, y in zip(sa, sb):
        x_param, y_param = x.startswith("{"), y.startswith("{")
        if x_param and not y_param:
            swallowed = True
        elif not x_param and (y_param or x != y):
            return False
    return swallowed


def fix_route_shadowing(code: str) -> tuple:
    """Returns (code, [messages]). Moves shadowed routes (with their decorators) above the route
    that shadows them. Text-based, so comments and formatting are preserved."""
    msgs: List[str] = []
    for _ in range(20):
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return code, msgs
        infos = _route_infos(tree)
        pair = None
        for i, a in enumerate(infos):
            for b in infos[i + 1:]:
                if a["method"] == b["method"] and _shadows(a["path"], b["path"]):
                    pair = (a, b)
                    break
            if pair:
                break
        if not pair:
            return code, msgs
        a, b = pair
        lines = code.splitlines(keepends=True)
        block = lines[b["start"] - 1:b["end"]]
        del lines[b["start"] - 1:b["end"]]
        lines[a["start"] - 1:a["start"] - 1] = block + ["\n\n"]
        code = "".join(lines)
        msgs.append(f"moved {b['method'].upper()} {b['path']} above {a['method'].upper()} {a['path']} (it was unreachable)")
    return code, msgs
