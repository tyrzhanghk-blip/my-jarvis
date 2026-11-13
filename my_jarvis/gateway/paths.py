"""路径与数据目录。"""
from pathlib import Path
import os

DATA_DIR = Path(os.environ.get("JARVIS_HOME", "~/.my-jarvis")).expanduser()
CONFIG_DIR = DATA_DIR / "config"
DB_PATH = DATA_DIR / "jarvis.db"
LOG_DIR = DATA_DIR / "logs"
WORKSPACE_DIR = DATA_DIR / "workspace"
SCRIPTS_DIR = DATA_DIR / "scripts"
TOKEN_PATH = DATA_DIR / "token"

def ensure_dirs() -> None:
    for p in (DATA_DIR, CONFIG_DIR, LOG_DIR, WORKSPACE_DIR, SCRIPTS_DIR):
        p.mkdir(parents=True, exist_ok=True)
