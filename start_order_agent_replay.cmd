@echo off
cd /d "%~dp0"
echo SCRIPTED REPLAY ONLY - not real model behavior. No external calls.
".venv\Scripts\python.exe" -m storekeeper.order_agent_preview --replay --port 8769
pause
