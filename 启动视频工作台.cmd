@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m workbench --nas-config data/nas-deploy/client.json
if errorlevel 1 pause
