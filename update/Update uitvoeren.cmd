@echo off
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0Update.ps1"
if errorlevel 1 exit /b 1
