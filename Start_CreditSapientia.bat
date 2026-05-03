@echo off
cd /d C:\CreditRepairCRM\app
start "CreditSapientia Server" cmd /k python -m uvicorn main:app --reload
timeout /t 4 /nobreak >nul
start "" http://127.0.0.1:8000