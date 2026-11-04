@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run uv sync first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\index_policies.py
if errorlevel 1 (
  pause
  exit /b 1
)
echo Real model chooses read-only tools. API credit is used. No order writes.
".venv\Scripts\python.exe" -u scripts\run_tool_agent.py
pause
