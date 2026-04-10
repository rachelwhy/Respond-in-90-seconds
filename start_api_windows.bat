@echo off
cd /d %~dp0
call .venv\Scripts\activate
uvicorn api_server:app --host 0.0.0.0 --port 8000
pause
