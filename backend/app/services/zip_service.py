import logging
import os
import zipfile
from pathlib import Path
from typing import Optional
from app.config import settings

logger = logging.getLogger("acsa.zip")


def create_project_zip(run_id: str, project_name: Optional[str] = None) -> Path:
    """
    Creates a ZIP archive containing all files from the project directory.
    If project_name is provided, files are placed inside a top-level directory with that name.
    """
    source_dir = settings.projects_dir / run_id
    if not source_dir.exists():
        raise FileNotFoundError(f"Project directory for run {run_id} does not exist at {source_dir}")

    zip_filename = f"{run_id}.zip"
    zip_path = settings.zips_dir / zip_filename

    # Clean archive folder name
    clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in (project_name or run_id))
    clean_name = clean_name.strip("-_") or "project"

    ignored_files = {".DS_Store", "Thumbs.db", "acsa_state.json"}
    ignored_dirs = {"__pycache__", ".git", ".pytest_cache", "node_modules", ".venv"}

    logger.info(f"Creating zip archive for run {run_id} -> {zip_path}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if d not in ignored_dirs]
            for file in files:
                if file in ignored_files:
                    continue
                file_path = Path(root) / file
                rel_path = file_path.relative_to(source_dir)
                archive_name = Path(clean_name) / rel_path
                zip_file.write(file_path, arcname=archive_name.as_posix())

    logger.info(f"Zip archive successfully created: {zip_path} ({zip_path.stat().st_size} bytes)")
    return zip_path
