from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = REPO_ROOT / "app"

for path in (str(REPO_ROOT), str(APP_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)
