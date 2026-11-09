@echo off
cd /d "%~dp0"
python -X utf8 update_and_open.py
if errorlevel 1 pause
