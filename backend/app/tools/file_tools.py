import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.config import settings


def sanitize_relative_path(path_str: str) -> Path:
    """
    Sanitize and normalize a relative file path to prevent directory traversal attacks.
    Strips leading slashes, backslashes, and dots.
    """
    cleaned = path_str.strip().replace("\\", "/")
    # Remove leading slashes and ./
    while cleaned.startswith("/") or cleaned.startswith("./"):
        cleaned = cleaned.lstrip("/.")
    parts = [p for p in cleaned.split("/") if p and p != "."]
    safe_parts: List[str] = []
    for part in parts:
        if part == "..":
            if safe_parts:
                safe_parts.pop()
        else:
            safe_parts.append(part)
    if not safe_parts:
        return Path("file.txt")
    return Path(*safe_parts)


def detect_language(file_path: str) -> str:
    """Infers code language from filename extension for syntax highlighting."""
    ext = os.path.splitext(file_path)[1].lower()
    mapping = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".json": "json",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".md": "markdown",
        ".sql": "sql",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".sh": "bash",
        ".bash": "bash",
        ".env": "bash",
        ".toml": "toml",
        ".ini": "ini",
        ".dockerfile": "dockerfile",
        "dockerfile": "dockerfile",
    }
    basename = os.path.basename(file_path).lower()
    if basename in ("dockerfile", "containerfile"):
        return "dockerfile"
    return mapping.get(ext, "text")


def save_project_files(run_id: str, files: List[Dict[str, str]]) -> Path:
    """
    Saves a list of files to the project directory on disk.
    Each file dict should have 'path' and 'content'.
    Returns the project root directory path.
    """
    project_dir = settings.projects_dir / run_id
    project_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        rel_path = sanitize_relative_path(f.get("path", "untitled.txt"))
        full_path = project_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        content = f.get("content", "")
        with open(full_path, "w", encoding="utf-8") as file_out:
            file_out.write(content)

    return project_dir


def save_code_files(files: Dict[str, str], run_id: Optional[str] = None) -> Path:
    """
    Saves a dictionary of {filepath: content} to disk.
    If run_id is omitted, saves to the latest active project run directory.
    """
    target_run_id = run_id
    if not target_run_id:
        if settings.projects_dir.exists():
            dirs = [d for d in settings.projects_dir.iterdir() if d.is_dir()]
            if dirs:
                dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
                target_run_id = dirs[0].name
    if not target_run_id:
        target_run_id = "default_run"

    project_dir = settings.projects_dir / target_run_id
    project_dir.mkdir(parents=True, exist_ok=True)

    formatted_files = []
    for rel_path_str, content in files.items():
        rel_path = sanitize_relative_path(rel_path_str)
        full_path = project_dir / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        formatted_files.append({
            "path": rel_path.as_posix(),
            "content": content,
            "language": detect_language(rel_path.as_posix()),
            "size_bytes": len(content.encode("utf-8")),
        })

    # Sync with acsa_state.json if present
    state_file = project_dir / "acsa_state.json"
    if state_file.exists():
        try:
            import json
            state_data = json.loads(state_file.read_text(encoding="utf-8"))
            state_data["files"] = formatted_files
            state_data["code_files"] = files
            state_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
        except Exception:
            pass

    return project_dir


def read_project_files(run_id: str) -> List[Dict[str, Any]]:
    """Reads all files for a project run from disk."""
    project_dir = settings.projects_dir / run_id
    if not project_dir.exists():
        return []

    result_files: List[Dict[str, Any]] = []
    for root, _, filenames in os.walk(project_dir):
        for name in filenames:
            if name in ("acsa_state.json", ".DS_Store"):
                continue
            full_path = Path(root) / name
            rel_path = full_path.relative_to(project_dir).as_posix()
            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception:
                content = "[Binary or unreadable content]"

            result_files.append({
                "path": rel_path,
                "content": content,
                "language": detect_language(rel_path),
                "size_bytes": full_path.stat().st_size,
            })

    result_files.sort(key=lambda x: x["path"])
    return result_files


def build_file_tree(run_id: str) -> Dict[str, Any]:
    """Generates a hierarchical file tree structure for visualization."""
    project_dir = settings.projects_dir / run_id
    if not project_dir.exists():
        return {"name": run_id, "type": "directory", "children": []}

    def walk_dir(path: Path) -> Dict[str, Any]:
        node: Dict[str, Any] = {
            "name": path.name,
            "type": "directory" if path.is_dir() else "file",
        }
        if path.is_dir():
            children = []
            for item in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if item.name in ("acsa_state.json", ".DS_Store"):
                    continue
                children.append(walk_dir(item))
            node["children"] = children
        else:
            node["language"] = detect_language(path.name)
            node["size"] = path.stat().st_size
        return node

    return walk_dir(project_dir)
