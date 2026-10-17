@echo off
rem Same as start_jarvis.bat, but also lets your phone open Jarvis over the home Wi-Fi.
rem The phone link is printed below and behind the Phone button in the web page.
rem Close this window to stop Jarvis.
cd /d "%~dp0"
if not defined JARVIS_HOME set "JARVIS_HOME=D:\Arbinda\AI-data\jarvis"
if not defined OLLAMA_MODELS set "OLLAMA_MODELS=D:\Arbinda\AI-data\ollama-models"

curl -s -o nul http://127.0.0.1:11434/api/version
if errorlevel 1 (
    echo Starting Ollama...
    start "" "D:\Ollama\ollama app.exe"
    timeout /t 8 /nobreak >nul
)

python main.py gui --host 0.0.0.0
if errorlevel 1 pause
