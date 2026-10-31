"""Offline tests for the ACSA pipeline. A fake LLM replaces Groq, so no API key or network is needed.
Run from backend/:  GROQ_API_KEY=dummy python -m pytest -q tests
"""
import json

import pytest

from app.config import settings
from app.graph import nodes
from app.graph.state import create_initial_state

FB, FE = nodes.FILE_BEGIN, nodes.FILE_END

ANALYSIS = {
    "project_name": "Attendance API", "summary": "Tracks student attendance.",
    "tech_stack": ["FastAPI", "SQLite"], "features": ["students", "attendance %"],
    "api_endpoints": [{"method": "POST", "path": "/students", "description": "create"},
                      {"method": "GET", "path": "/students/{id}", "description": "get"}],
    "data_models": [{"name": "Student", "description": "student"}],
    "security_considerations": [], "user_stories": [],
}
PLAN = [
    {"path": "app/main.py", "description": "entrypoint", "language": "python", "exports": ["app"]},
    {"path": "app/models.py", "description": "models", "language": "python", "exports": ["Student", "StudentCreate", "StudentRead"]},
    {"path": "app/services.py", "description": "services", "language": "python", "exports": ["create_student", "get_student"]},
    {"path": "app/database.py", "description": "db", "language": "python", "exports": ["engine", "SessionLocal", "Base", "get_db"]},
    {"path": "app/config.py", "description": "config", "language": "python", "exports": ["DATABASE_URL"]},
    {"path": "requirements.txt", "description": "deps", "language": "text"},
    {"path": "README.md", "description": "docs", "language": "markdown"},
]
ARCH = {"overview": "layered", "design_patterns": ["Layered"], "dependencies": ["fastapi", "sqlalchemy"],
        "file_plan": PLAN, "run_instructions": "uvicorn app.main:app"}

GOOD = {
    "app/config.py": 'DATABASE_URL = "sqlite:///./app.db"\n',
    "app/database.py": (
        "from sqlalchemy import create_engine\n"
        "from sqlalchemy.orm import declarative_base, sessionmaker\n"
        "from app.config import DATABASE_URL\n"
        "engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})\n"
        "SessionLocal = sessionmaker(bind=engine, autoflush=False)\n"
        "Base = declarative_base()\n\n"
        "def get_db():\n    db = SessionLocal()\n    try:\n        yield db\n    finally:\n        db.close()\n"
    ),
    "app/models.py": (
        "from pydantic import BaseModel, ConfigDict\n"
        "from sqlalchemy import Column, Integer, String\n"
        "from app.database import Base\n\n"
        "class Student(Base):\n    __tablename__ = 'students'\n"
        "    id = Column(Integer, primary_key=True)\n    name = Column(String, nullable=False)\n\n"
        "class StudentCreate(BaseModel):\n    name: str\n\n"
        "class StudentRead(StudentCreate):\n    model_config = ConfigDict(from_attributes=True)\n    id: int\n"
    ),
    "app/services.py": (
        "from sqlalchemy.orm import Session\n"
        "from app.models import Student, StudentCreate\n\n"
        "def create_student(db: Session, data: StudentCreate) -> Student:\n"
        "    s = Student(name=data.name)\n    db.add(s)\n    db.commit()\n    db.refresh(s)\n    return s\n\n"
        "def get_student(db: Session, student_id: int):\n    return db.get(Student, student_id)\n"
    ),
    "app/main.py": (
        "from contextlib import asynccontextmanager\n"
        "from fastapi import Depends, FastAPI, HTTPException\n"
        "from sqlalchemy.orm import Session\n"
        "from app.database import Base, engine, get_db\n"
        "from app.models import StudentCreate, StudentRead\n"
        "from app.services import create_student, get_student\n\n"
        "@asynccontextmanager\nasync def lifespan(app):\n    Base.metadata.create_all(bind=engine)\n    yield\n\n"
        "app = FastAPI(lifespan=lifespan)\n\n"
        "@app.post('/students', response_model=StudentRead, status_code=201)\n"
        "def create(payload: StudentCreate, db: Session = Depends(get_db)):\n    return create_student(db, payload)\n\n"
        "@app.get('/students/{student_id}', response_model=StudentRead)\n"
        "def read(student_id: int, db: Session = Depends(get_db)):\n"
        "    s = get_student(db, student_id)\n    if not s:\n        raise HTTPException(404, 'not found')\n    return s\n"
    ),
    "requirements.txt": "fastapi\nsqlalchemy\nhttpx\npytest\n",
    "README.md": "# Attendance API\n\n```bash\nuvicorn app.main:app\n```\n",
    "tests/test_api.py": (
        "import pytest\nfrom fastapi.testclient import TestClient\nfrom app.main import app\n\n"
        "@pytest.fixture(scope='module')\ndef client():\n    with TestClient(app) as c:\n        yield c\n\n"
        "def test_create_and_get(client):\n"
        "    r = client.post('/students', json={'name': 'Asha'})\n    assert r.status_code == 201\n"
        "    sid = r.json()['id']\n    assert client.get(f'/students/{sid}').json()['name'] == 'Asha'\n\n"
        "def test_missing(client):\n    assert client.get('/students/99999').status_code == 404\n\n"
        "def test_validation(client):\n    assert client.post('/students', json={}).status_code == 422\n"
    ),
}


class Resp:
    def __init__(self, content, finish="stop"):
        self.content = content
        self.response_metadata = {"finish_reason": finish}


class FakeLLM:
    """Scripted LLM. `script[path]` is a list of replies consumed in order (last one repeats)."""

    def __init__(self, script=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls = []

    def invoke(self, messages):
        system, human = messages[0].content, messages[1].content
        if "Requirements Analyst" in system:
            return Resp(json.dumps(ANALYSIS))
        if "Principal Software Architect" in system:
            return Resp(json.dumps(ARCH))
        path = human.split("FILE TO WRITE NOW: ", 1)[1].split("\n", 1)[0].strip()
        self.calls.append(path)
        replies = self.script.get(path)
        if replies:
            reply = replies.pop(0) if len(replies) > 1 else replies[0]
        else:
            reply = Resp(f"{FB}\n{GOOD[path]}{FE}")
        return reply if isinstance(reply, Resp) else Resp(reply)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(settings, "test_use_venv", False)  # fast: use this interpreter
    monkeypatch.setattr(settings, "allow_template_fallback", False)
    monkeypatch.setattr(settings, "max_repair_attempts", 1)


def run(monkeypatch, llm):
    monkeypatch.setattr(nodes, "get_llm", lambda *a, **k: llm)
    from app.graph.workflow import build_acsa_graph
    return build_acsa_graph().invoke(create_initial_state("acsa_test", "Student attendance API"))


def test_happy_path_ships_only_after_real_pytest(monkeypatch):
    final = run(monkeypatch, FakeLLM())
    assert final["status"] == "completed", final.get("error")
    assert final["test_report"]["passed"] and final["test_report"]["passed"] == True
    assert final["review"]["verdict"] == "Ready to ship"
    assert final["degraded"] is False
    assert "app/__init__.py" in {f["path"] for f in final["files"]}


def test_truncated_file_is_retried(monkeypatch):
    llm = FakeLLM({"app/services.py": [Resp(f"{FB}\nfrom sqlalchemy.orm import Session\n    .order_by(", finish="length"),
                                       f"{FB}\n{GOOD['app/services.py']}{FE}"]})
    final = run(monkeypatch, llm)
    assert llm.calls.count("app/services.py") == 2
    assert final["status"] == "completed"


def test_syntax_error_twice_fails_run_without_templates(monkeypatch):
    llm = FakeLLM({"app/services.py": [f"{FB}\ndef broken(:\n{FE}"]})
    final = run(monkeypatch, llm)
    assert final["status"] == "failed"
    assert "app/services.py" in final["error"]
    assert not final.get("test_report")  # tester never ran


def test_invalid_api_key_fails_fast(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Error code: 401 - {'error': {'message': 'Invalid API Key'}}")
    monkeypatch.setattr(nodes, "get_llm", boom)
    from app.graph.workflow import build_acsa_graph
    final = build_acsa_graph().invoke(create_initial_state("acsa_test", "Student attendance API"))
    assert final["status"] == "failed"
    assert "GROQ_API_KEY" in final["error"]
    assert final["current_step"] == "analyst"


def test_import_mismatch_is_detected_and_fixed(monkeypatch):
    bad_main = GOOD["app/main.py"].replace("StudentRead", "StudentResponse")
    llm = FakeLLM({"app/main.py": [f"{FB}\n{bad_main}{FE}", f"{FB}\n{GOOD['app/main.py']}{FE}"]})
    final = run(monkeypatch, llm)
    assert llm.calls.count("app/main.py") == 2
    assert final["status"] == "completed"


def test_failing_tests_trigger_one_repair_then_ship(monkeypatch):
    buggy = GOOD["app/services.py"].replace("return db.get(Student, student_id)", "return None")
    llm = FakeLLM({"app/services.py": [f"{FB}\n{buggy}{FE}", f"{FB}\n{GOOD['app/services.py']}{FE}"]})
    final = run(monkeypatch, llm)
    assert final["repair_attempts"] == 1
    assert final["status"] == "completed"


def test_still_failing_after_repair_marks_failed(monkeypatch):
    buggy = GOOD["app/services.py"].replace("return db.get(Student, student_id)", "return None")
    llm = FakeLLM({"app/services.py": [f"{FB}\n{buggy}{FE}"]})
    final = run(monkeypatch, llm)
    assert final["repair_attempts"] == 1
    assert final["status"] == "failed"
    assert final["review"]["verdict"].startswith("Not ready")


def test_check_internal_imports_flags_missing_names():
    probs = nodes.check_internal_imports({
        "app/models.py": "class Student: pass\n",
        "app/main.py": "from app.models import Student, StudentResponse\nfrom .models import Student\n",
    })
    assert list(probs) == ["app/main.py"] and "StudentResponse" in probs["app/main.py"][0]


# ---------------------------------------------------------------------------
# Tests for the free-tier / robustness fixes
# ---------------------------------------------------------------------------
from app.graph import codegen_utils as cu


def test_requirements_and_readme_need_no_llm_call(monkeypatch):
    llm = FakeLLM()
    final = run(monkeypatch, llm)
    assert "requirements.txt" not in llm.calls and "README.md" not in llm.calls
    req = next(f["content"] for f in final["files"] if f["path"] == "requirements.txt")
    assert "sqlalchemy>=2.0" in req and "==" not in req


def test_route_shadowing_is_fixed():
    code = (
        "from fastapi import FastAPI\napp = FastAPI()\n\n"
        "@app.get('/students/{student_id}')\ndef one(student_id: int):\n    return {}\n\n"
        "@app.get('/students/below-threshold')\ndef low():\n    return []\n"
    )
    fixed, msgs = cu.fix_route_shadowing(code)
    assert msgs and fixed.index("below-threshold") < fixed.index("{student_id}")


def test_undefined_name_rejected_then_retried(monkeypatch):
    bad = GOOD["app/services.py"] + "\n\ndef broken():\n    return Integer\n"
    llm = FakeLLM({"app/services.py": [f"{FB}\n{bad}{FE}", f"{FB}\n{GOOD['app/services.py']}{FE}"]})
    final = run(monkeypatch, llm)
    assert llm.calls.count("app/services.py") == 2
    assert final["status"] == "completed"


def test_request_too_large_shrinks_context(monkeypatch):
    class TooLargeOnce(FakeLLM):
        def __init__(self):
            super().__init__(); self.raised = False
        def invoke(self, messages, **kw):
            if "FILE TO WRITE NOW: app/main.py" in messages[1].content and not self.raised:
                self.raised = True
                raise RuntimeError("Error code: 413 - Request too large for model on tokens per minute (TPM)")
            return super().invoke(messages)
    final = run(monkeypatch, TooLargeOnce())
    assert final["status"] == "completed"


def test_daily_quota_fails_fast_with_clear_message(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("Error code: 429 - rate_limit_exceeded on tokens per day (TPD): Limit 200000")
    monkeypatch.setattr(nodes, "get_llm", boom)
    from app.graph.workflow import build_acsa_graph
    final = build_acsa_graph().invoke(create_initial_state("acsa_test", "x"))
    assert final["status"] == "failed" and "quota" in final["error"]


def test_weakened_tests_are_rejected():
    old = "def test_a(c):\n    assert c.get('/x/1').status_code == 404\n"
    new = "def test_a(c):\n    assert c.get('/x/1').status_code == 200\n"
    assert nodes._tests_weakened(old, new)
    assert nodes._tests_weakened(old, old) is None


def test_syntax_error_message_shows_generated_code_not_backend_file():
    err = nodes._validate_file("app/main.py", "x = 1\ndef f(a=1, b):\n    pass\n")
    assert "def f(a=1, b)" in err and "ACSA" not in err
