@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Missing virtual environment. Run: uv sync
  pause
  exit /b 1
)
if not exist "frontend\dist\index.html" (
  echo Build the frontend first: cd frontend ^&^& npm ci ^&^& npm run build
  pause
  exit /b 1
)
echo OFFLINE DEMO - fixture orders only, no model or Shopify calls.
echo Open http://127.0.0.1:8765/ after the server starts.
echo Press Ctrl+C in this window to stop the demo.
".venv\Scripts\python.exe" -m storekeeper.demo --port 8765
pause
