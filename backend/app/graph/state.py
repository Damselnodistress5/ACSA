from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from typing_extensions import TypedDict


class LogEntry(TypedDict, total=False):
    timestamp: str
    step: str
    message: str
    level: str


class GeneratedFile(TypedDict, total=False):
    path: str
    content: str
    language: str
    description: Optional[str]
    size_bytes: Optional[int]


class PlannedFile(TypedDict, total=False):
    path: str
    description: str
    language: Optional[str]
    # Interface contract from the Architect, shared with the Writer so imports line up
    exports: List[str]      # top-level classes/functions/variables this file must define
    imports_from: List[str]  # other planned files this one imports from


class AgentState(TypedDict, total=False):
    run_id: str
    prompt: str
    project_name: str
    status: str  # "pending", "running", "completed", "failed"
    current_step: str  # "analyst", "architect", "writer", "tester", "reviewer", "completed"
    progress: int  # 0 to 100
    logs: List[LogEntry]
    analysis: Dict[str, Any]
    architecture: Dict[str, Any]
    file_plan: List[PlannedFile]
    files: List[GeneratedFile]
    test_suite: Dict[str, Any]
    review: Dict[str, Any]
    error: Optional[str]
    # Honest run bookkeeping
    requirements: Dict[str, Any]
    code_files: Dict[str, str]
    test_report: Dict[str, Any]   # result of actually running pytest
    repair_attempts: int          # Writer repair rounds used so far
    degraded: bool                # True if any stage used a fallback instead of the LLM
    warnings: List[str]           # human-readable reasons for degraded/failed status


def create_initial_state(run_id: str, prompt: str, project_name: Optional[str] = None) -> AgentState:
    """Helper to initialize fresh state for a project run."""
    now = datetime.now(timezone.utc).isoformat()
    clean_name = project_name.strip() if project_name else ""
    return {
        "run_id": run_id,
        "prompt": prompt,
        "project_name": clean_name or "Generated Project",
        "status": "running",
        "current_step": "analyst",
        "progress": 5,
        "logs": [
            {
                "timestamp": now,
                "step": "system",
                "message": f"ACSA session initialized for run {run_id}",
                "level": "info",
            }
        ],
        "analysis": {},
        "architecture": {},
        "file_plan": [],
        "files": [],
        "test_suite": {},
        "review": {},
        "error": None,
        "test_report": {},
        "repair_attempts": 0,
        "degraded": False,
        "warnings": [],
    }


def add_log(state: AgentState, step: str, message: str, level: str = "info") -> List[LogEntry]:
    """Helper to append a log message and return updated logs list."""
    existing = list(state.get("logs", []))
    existing.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "step": step,
        "message": message,
        "level": level,
    })
    return existing
