"""Dev server with --reload correctly scoped to backend/ source only.

`uvicorn --reload` with no reload-dir watches the current working directory
recursively, which includes backend/.venv (thousands of installed-package
files) - editing any file then triggers a slow full-tree rescan, or with
`--app-dir backend` run from the project root, it watches frontend/node_modules
too, which is worse. This sets reload_dirs/reload_excludes programmatically
instead of as CLI flags, because reload-exclude patterns containing a `*`
get mangled by this environment's shell layer before uvicorn ever sees them.

Run from backend/:  .venv\Scripts\python dev_server.py
Run from project root:  backend\.venv\Scripts\python backend\dev_server.py

Both forms behave identically regardless of the working directory the
command happens to be run from - uvicorn resolves reload_excludes patterns
relative to the process's cwd, so this pins cwd to backend/ itself first.
"""
import os
import pathlib

import uvicorn

BACKEND_DIR = pathlib.Path(__file__).resolve().parent
os.chdir(BACKEND_DIR)

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["."],
        reload_excludes=[".venv/*", "**/__pycache__/*"],
    )
