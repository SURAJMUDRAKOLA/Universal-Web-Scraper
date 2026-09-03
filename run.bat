@echo off
REM Universal Web Scraper — Windows startup script
REM Run from project root: run.bat

echo [1/4] Setting up virtual environment...
if not exist venv (
    python -m venv venv
)

echo [2/4] Installing dependencies...
venv\Scripts\pip install --upgrade pip -q
venv\Scripts\pip install -r requirements.txt -q

echo [3/4] Installing Playwright browser...
venv\Scripts\python -m playwright install chromium --with-deps

echo [4/4] Starting server on http://localhost:8000
venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
