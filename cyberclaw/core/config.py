import os
from collections.abc import Iterable
from pathlib import Path

from .environment import PROJECT_ROOT

WORKSPACE_DIR = os.getenv("CYBERCLAW_WORKSPACE", str(PROJECT_ROOT / "workspace"))
DB_PATH = os.path.join(WORKSPACE_DIR, "state.sqlite3")
MEMORY_DIR = os.path.join(WORKSPACE_DIR, "memory")
PERSONAS_DIR = os.path.join(WORKSPACE_DIR, "personas")
SCRIPTS_DIR = os.path.join(WORKSPACE_DIR, "scripts")
OFFICE_DIR = os.path.join(WORKSPACE_DIR, "office")
SKILLS_DIR = os.path.join(OFFICE_DIR, "skills")
TASKS_FILE = os.path.join(WORKSPACE_DIR, "tasks.json")
WORKSPACE_DIRECTORIES = (
    WORKSPACE_DIR,
    MEMORY_DIR,
    PERSONAS_DIR,
    SCRIPTS_DIR,
    OFFICE_DIR,
    SKILLS_DIR,
)
def ensure_workspace(
    directories: Iterable[str | Path] = WORKSPACE_DIRECTORIES,
) -> None:
    """Create runtime directories explicitly during application startup."""
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
