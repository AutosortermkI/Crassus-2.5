@echo off
call .venv\Scripts\activate
python -m pytest tests/ -v
