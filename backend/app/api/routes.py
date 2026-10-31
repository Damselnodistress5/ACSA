import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.services.project_service import project_service

logger = logging.getLogger("acsa.api")
router = APIRouter(prefix="/api", tags=["ACSA Project Operations"])


class StartProjectRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=5,
        description="Detailed requirement description of what software you want to build.",
        examples=["Build a full-stack task manager with FastAPI, SQLite, Pydantic, and JWT authentication."],
    )
    project_name: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Optional preferred name for the project.",
        examples=["TaskMaster API"],
    )


class StartProjectResponse(BaseModel):
    run_id: str
    status: str
    message: str


@router.post(
    "/start-project",
    response_model=StartProjectResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start new autonomous software engineering run",
)
async def start_project(req: StartProjectRequest):
    """
    Submits user requirements to initiate the 5-node ACSA agent pipeline:
    1. Requirements Analyst
    2. Code Architect
    3. Code Writer
    4. Tester
    5. Code Reviewer
    """
    try:
        run_id = project_service.start_project(prompt=req.prompt, project_name=req.project_name)
        return StartProjectResponse(
            run_id=run_id,
            status="running",
            message="Autonomous code generation workflow initiated.",
        )
    except Exception as e:
        logger.error(f"Failed to start project: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start project: {str(e)}",
        )


@router.get(
    "/status/{run_id}",
    summary="Check real-time execution status and logs of an ACSA run",
)
async def get_run_status(run_id: str):
    """Returns current active step, completion percentage, and real-time agent activity logs."""
    run_status = project_service.get_status(run_id)
    if not run_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run ID '{run_id}' not found.",
        )
    return run_status


@router.get(
    "/result/{run_id}",
    summary="Retrieve final project deliverables (Analysis, Architecture, Files, Tests, Review)",
)
async def get_run_result(run_id: str):
    """Returns comprehensive project deliverables once generation completes."""
    result = project_service.get_result(run_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run ID '{run_id}' not found.",
        )
    return result


@router.get(
    "/download/{run_id}",
    summary="Download complete generated project repository as a ZIP archive",
)
def download_project_zip(run_id: str):
    """Returns downloadable ZIP file containing all source code, tests, configs, and documentation."""
    zip_path = project_service.get_zip_path(run_id)
    if not zip_path or not zip_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ZIP archive for run '{run_id}' is not yet available or does not exist.",
        )

    # Determine friendly download filename
    state = project_service.runs.get(run_id, {})
    raw_name = state.get("project_name", run_id)
    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in raw_name)
    download_filename = f"{safe_name.strip('-_') or 'acsa-project'}.zip"

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=download_filename,
    )


@router.get("/health", summary="Health check endpoint")
def health_check():
    """Health check for service monitoring."""
    return {
        "status": "healthy",
        "service": "ACSA Backend",
        "version": "1.0.0",
        "model": settings.groq_model,
    }
