@echo off
setlocal

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%backend\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Backend virtual environment not found at backend\.venv
    echo Run the first-time setup in README.md, then try again.
    exit /b 1
)

if not exist "%ROOT%frontend\dist\index.html" (
    echo Frontend build not found - building now...
    pushd "%ROOT%frontend"
    call npm run build
    if errorlevel 1 (
        popd
        echo Frontend build failed.
        exit /b 1
    )
    popd
)

echo Starting Autodarts on http://0.0.0.0:8000 ...
pushd "%ROOT%backend"
"%VENV_PY%" -m uvicorn app:app --host 0.0.0.0 --port 8000
popd
