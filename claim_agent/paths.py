"""Repository-local resources, independent of the server's working directory."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_ROOT / "fixtures"
WEB_DIR = PROJECT_ROOT / "web"
