@echo off
cd /d "%~dp0"
echo REAL DEEPSEEK CALLS - fixture orders only, no real Shopify operations.
".venv\Scripts\python.exe" -m storekeeper.live_preview --port 8766
pause
