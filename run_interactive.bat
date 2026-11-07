@echo off
cd /d "C:\MyPersonalprojects\Scout"
title Scout AI Agent
call .venv\Scripts\activate.bat
python -m scout interactive
pause
