@echo off
REM Grabber - mode server (0.0.0.0:8765) untuk akses via Tailscale dari HP
REM WAJIB: isi password.txt dulu sebelum dipakai!
cd /d %~dp0
if not exist .venv python -m venv .venv
if errorlevel 1 goto gagal
call .venv\Scripts\activate
if errorlevel 1 goto gagal
pip install -r requirements.txt
if errorlevel 1 goto gagal
python -m playwright install chromium
if errorlevel 1 goto gagal
set GRABBER_SERVER=1
python app.py
if errorlevel 1 goto gagal
goto selesai
:gagal
echo.
echo [GAGAL] Terjadi kesalahan pada langkah di atas.
pause
:selesai
