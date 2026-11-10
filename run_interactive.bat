@echo off
cd /d "%~dp0"
title Scout AI Agent
call "%~dp0.venv\Scripts\activate.bat"
python -m scout interactive
pause
