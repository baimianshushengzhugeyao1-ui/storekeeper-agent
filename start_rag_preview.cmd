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
echo Real model RAG preview with fixture orders: http://127.0.0.1:8767/
".venv\Scripts\python.exe" -m storekeeper.live_preview --rag enhanced --port 8767
pause
