@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run uv sync first.
  pause
  exit /b 1
)
if not exist "var\vendor\shop-1.2.0\package\bin\shop.mjs" (
  ".venv\Scripts\python.exe" scripts\prepare_shop_skill.py
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
echo Real-model Skill and public catalog query. API credit is used. No purchases.
".venv\Scripts\python.exe" -u scripts\run_catalog_skill.py
pause
