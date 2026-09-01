@echo off
rem ---------------------------------------------------------------
rem  FF Draft Assistant launcher
rem ---------------------------------------------------------------
cd /d "%~dp0"

rem Make sure dependencies are present (only installs the first time).
python -c "import requests, truststore, openpyxl" 1>nul 2>nul
if errorlevel 1 (
    echo Installing dependencies, one moment...
    python -m pip install -r requirements.txt
)

rem Launch the app with no console window (falls back to python).
where pythonw 1>nul 2>nul
if errorlevel 1 (
    start "" python ffdraft.py
) else (
    start "" pythonw ffdraft.py
)
