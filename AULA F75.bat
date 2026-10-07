@echo off
title AULA F75
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python f75.py ui
) else (
    py -3 f75.py ui
)
if errorlevel 1 pause
