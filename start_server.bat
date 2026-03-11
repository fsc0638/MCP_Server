@echo off
echo Starting MCP Server with Python Virtual Environment...
call .venv\Scripts\activate.bat
python -m uvicorn router:app --port 8500 --reload
