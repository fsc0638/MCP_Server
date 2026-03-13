@echo off
echo Starting MCP Server with Python Virtual Environment...
call .venv\Scripts\activate.bat
python -m uvicorn server.app:app --port 8500 --reload

