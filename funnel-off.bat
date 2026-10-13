@echo off
REM Matikan Tailscale Funnel
if not "%~1"=="run" (cmd /k ""%~f0" run" & exit /b)
set "TS=tailscale"
where tailscale >nul 2>nul
if errorlevel 1 set "TS=%ProgramFiles%\Tailscale\tailscale.exe"
"%TS%" funnel reset
echo Funnel dimatikan.
