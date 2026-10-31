import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

from app.config import settings
from app.graph.state import AgentState, create_initial_state
from app.graph.workflow import acsa_graph
from app.services.zip_service import create_project_zip

logger = logging.getLogger("acsa.service")


class ProjectService:
    """Service to coordinate ACSA agent workflow executions and track run state."""

    def __init__(self):
        # In-memory registry of run states
        self.runs: Dict[str, AgentState] = {}
        # Set of run_ids currently being processed by an active worker
        self._active_run_ids: Set[str] = set()
        self._lock = threading.Lock()

    def _state_file_path(self, run_id: str) -> Path:
        return settings.projects_dir / run_id / "acsa_state.json"

    def _persist_state(self, run_id: str, state: AgentState):
        """Saves current state snapshot to disk for persistence across server restarts."""
        try:
            state_file = self._state_file_path(run_id)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.warning(f"[{run_id}] Failed to persist state snapshot: {e}")

    def _load_state(self, run_id: str) -> Optional[AgentState]:
        """Loads state from memory or disk snapshot."""
        if run_id in self.runs:
            return self.runs[run_id]

        state_file = self._state_file_path(run_id)
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.runs[run_id] = loaded
                    return loaded
            except Exception as e:
                logger.warning(f"[{run_id}] Failed to load state from {state_file}: {e}")

        return None

    def start_project(self, prompt: str, project_name: Optional[str] = None) -> str:
        """Initializes a new ACSA project run and triggers the background agent task."""
        run_id = f"acsa_{uuid.uuid4().hex[:8]}"
        initial_state = create_initial_state(run_id=run_id, prompt=prompt, project_name=project_name)
        self.runs[run_id] = initial_state
        self._persist_state(run_id, initial_state)

        with self._lock:
            self._active_run_ids.add(run_id)

        # Launch single worker thread for this run
        worker_thread = threading.Thread(
            target=self._run_workflow_sync,
            args=(run_id, initial_state),
            name=f"acsa-worker-{run_id}",
            daemon=True,
        )
        worker_thread.start()

        logger.info(f"Started project run {run_id} for prompt: {prompt[:60]}...")
        return run_id

    def _run_workflow_sync(self, run_id: str, state: AgentState):
        """Executes the multi-agent graph synchronously inside a dedicated worker thread."""
        try:
            logger.info(f"[{run_id}] Commencing LangGraph execution stream...")
            current = state

            # Stream sequentially through the 5 agent nodes
            for output in acsa_graph.stream(current):
                for node_name, node_output in output.items():
                    current = {**current, **node_output}
                    self.runs[run_id] = current
                    self._persist_state(run_id, current)
                    logger.info(
                        f"[{run_id}] Completed node '{node_name}', step now '{current.get('current_step')}' ({current.get('progress')}%)"
                    )

            # Respect a failure reported by any node instead of always forcing "completed"
            if current.get("status") != "failed":
                current["status"] = "completed"
            current["progress"] = 100
            self.runs[run_id] = current
            self._persist_state(run_id, current)
            if current["status"] == "failed":
                logger.error(f"[{run_id}] ACSA run FAILED: {current.get('error')}")
            else:
                logger.info(
                    f"[{run_id}] ACSA Multi-Agent execution finished "
                    f"({'DEGRADED' if current.get('degraded') else 'ok'})."
                )

        except Exception as e:
            logger.error(f"[{run_id}] Workflow execution failed: {e}", exc_info=True)
            current_state = self.runs.get(run_id, state)
            current_state["status"] = "failed"
            current_state["error"] = str(e)
            current_state.setdefault("logs", []).append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "step": "system",
                "message": f"Execution error: {str(e)}",
                "level": "error",
            })
            self.runs[run_id] = current_state
            self._persist_state(run_id, current_state)
        finally:
            with self._lock:
                self._active_run_ids.discard(run_id)
            logger.info(f"[{run_id}] Worker thread terminated. Run released from active set.")

    def get_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        """
        Pure read-only status query.
        Returns the current execution status, progress percentage, and logs.
        Never spawns duplicate background tasks.
        """
        state = self._load_state(run_id)
        if not state:
            return None

        return {
            "run_id": run_id,
            "project_name": state.get("project_name", "ACSA Project"),
            "status": state.get("status", "running"),
            "current_step": state.get("current_step", "analyst"),
            "progress": state.get("progress", 0),
            "logs": state.get("logs", []),
            "error": state.get("error"),
            "degraded": state.get("degraded", False),
            "warnings": state.get("warnings", []),
        }

    def get_result(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Returns the full result bundle (analysis, architecture, files, tests, review)."""
        state = self._load_state(run_id)
        if not state:
            return None

        files = state.get("files", [])
        if not files:
            from app.tools.file_tools import read_project_files
            files = read_project_files(run_id)

        return {
            "run_id": run_id,
            "project_name": state.get("project_name", "ACSA Project"),
            "status": state.get("status", "running"),
            "current_step": state.get("current_step", "completed"),
            "progress": state.get("progress", 100),
            "analysis": state.get("analysis", {}),
            "architecture": state.get("architecture", {}),
            "files": files,
            "test_suite": state.get("test_suite", {}),
            "review": state.get("review", {}),
            "test_report": state.get("test_report", {}),
            "degraded": state.get("degraded", False),
            "warnings": state.get("warnings", []),
            "download_url": f"/api/download/{run_id}",
            "error": state.get("error"),
        }

    def get_zip_path(self, run_id: str) -> Optional[Path]:
        """Returns the path to the project's zip archive, creating it if necessary."""
        zip_path = settings.zips_dir / f"{run_id}.zip"
        if zip_path.exists():
            return zip_path

        # If project folder exists on disk, create zip now
        project_dir = settings.projects_dir / run_id
        if project_dir.exists():
            state = self._load_state(run_id) or {}
            name = state.get("project_name", run_id)
            return create_project_zip(run_id, name)

        return None


# Global singleton service instance
project_service = ProjectService()
