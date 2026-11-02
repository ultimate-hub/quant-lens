@echo off
title Quant Terminal - Install
cd /d "%~dp0"

set "PYEXE="

echo ============================================================
echo   Quant Terminal v2 - First Time Setup
echo ============================================================
echo.

REM ---- locate python ----
for %%P in (python.exe python3.exe) do (
    if not defined PYEXE (
        for /f "delims=" %%I in ('where %%P 2^>nul') do (
            if not defined PYEXE set "PYEXE=%%I"
        )
    )
)

if not defined PYEXE (
    echo [ERROR] Python not found.
    echo.
    echo Please install Python 3.10+ from:
    echo   https://www.python.org/downloads/
    echo.
    echo IMPORTANT: check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo Python found: %PYEXE%
"%PYEXE%" --version
echo.

REM ---- create virtual env ----
if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Creating virtual environment...
    "%PYEXE%" -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv
        pause
        exit /b 1
    )
) else (
    echo [1/2] Virtual environment already exists, skipping.
)

REM ---- install deps ----
echo.
echo [2/2] Installing dependencies...
echo   (first time may take 3-8 minutes, ~250MB)
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed.
    echo Try running manually:
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Setup complete!
echo.
echo   Now double-click:  启动.vbs
echo   (or Launch.vbs)
echo ============================================================
echo.
pause
