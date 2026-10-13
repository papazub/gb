@echo off
REM Nyalakan Tailscale Funnel agar Grabber bisa diakses dari internet publik
REM WAJIB: isi password.txt dulu!
if not "%~1"=="run" (cmd /k ""%~f0" run" & exit /b)
set "TS=tailscale"
where tailscale >nul 2>nul
if errorlevel 1 set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
"%TS%" funnel --bg 8765
if errorlevel 1 "%TS%" funnel 8765
"%TS%" funnel status
