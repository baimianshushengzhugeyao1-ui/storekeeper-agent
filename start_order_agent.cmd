@echo off
cd /d "%~dp0"
echo REAL MODEL - synthetic customer orders only. No real store operations.
".venv\Scripts\python.exe" -m storekeeper.order_agent_preview --port 8768
pause
