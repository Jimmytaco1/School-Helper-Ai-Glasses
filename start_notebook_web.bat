@echo off
rem OPTIONAL older web version. It opens in Microsoft Edge's slim "app" window
rem (not Chrome), but needs more memory than the normal desktop window.
rem Needs:  pip install -r requirements-web.txt
cd /d "%~dp0"
start "Notebook web server" /min python -m streamlit run notebook_app.py --server.headless true --browser.gatherUsageStats false
echo Starting... (a few seconds)
timeout /t 6 /nobreak >nul
start "" msedge --app=http://localhost:8501
start "Notebook hub" /min python notebook_hub.py
